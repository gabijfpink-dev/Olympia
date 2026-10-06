"""Uma rodada do agendador: publica o que já venceu e ainda não saiu."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Protocol

from meta_ads_ops.graph import RateLimitError
from social_scheduler.schedule import Post
from social_scheduler.settings import MissingCredentialError
from social_scheduler.state import StateStore

logger = logging.getLogger(__name__)


class Publisher(Protocol):
    platform: str

    def publish(self, post: Post) -> str: ...


@dataclass
class RunReport:
    published: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    would_publish: list[str] = field(default_factory=list)
    too_late: list[str] = field(default_factory=list)
    gave_up: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.failed or self.invalid)


def due_jobs(
    posts: list[Post],
    state: StateStore,
    now: datetime,
    max_late: timedelta,
    max_attempts: int,
    report: RunReport,
) -> list[tuple[Post, str]]:
    jobs: list[tuple[Post, str]] = []
    for post in posts:
        if not post.enabled or post.publish_at > now:
            continue
        if not post.valid:
            report.invalid.append(f"{post.id}: {'; '.join(post.errors)}")
            continue
        for platform in post.platforms:
            key = f"{post.id} [{platform}]"
            if state.is_done(post.id, platform):
                continue
            if now - post.publish_at > max_late:
                # Post antigo que nunca saiu (máquina desligada, token vencido...):
                # não publica sozinho fora de hora — ajuste publish_at se ainda quiser.
                report.too_late.append(key)
                continue
            if state.attempts(post.id, platform) >= max_attempts:
                report.gave_up.append(key)
                continue
            jobs.append((post, platform))
    return jobs


def run(
    posts: list[Post],
    state: StateStore,
    now: datetime,
    make_publisher: Callable[[str], Publisher],
    max_late: timedelta = timedelta(hours=6),
    max_attempts: int = 3,
    dry_run: bool = False,
) -> RunReport:
    report = RunReport()
    jobs = due_jobs(posts, state, now, max_late, max_attempts, report)

    publishers: dict[str, Publisher] = {}
    for post, platform in jobs:
        key = f"{post.id} [{platform}]"
        if dry_run:
            logger.info("[dry-run] publicaria %s: %r", key, post.text_for(platform)[:80])
            report.would_publish.append(key)
            continue

        if platform not in publishers:
            try:
                publishers[platform] = make_publisher(platform)
            except MissingCredentialError as exc:
                # Configuração, não falha do post: não gasta tentativa.
                logger.error("%s: %s", key, exc)
                report.failed.append(f"{key}: {exc}")
                continue

        try:
            remote_id = publishers[platform].publish(post)
        except RateLimitError as exc:
            # Insistir só piora; o resto sai na próxima rodada do agendador.
            logger.error("Rate limit em %s: %s — parando esta rodada.", key, exc)
            report.failed.append(f"{key}: rate limit")
            break
        except Exception as exc:  # noqa: BLE001 — um post ruim não pode travar os outros
            logger.error("Falhou %s: %s", key, exc)
            state.mark_failed(post.id, platform, str(exc))
            report.failed.append(f"{key}: {exc}")
            continue

        state.mark_published(post.id, platform, remote_id)
        logger.info("Publicado %s -> %s", key, remote_id)
        report.published.append(key)

    return report


# O Facebook só aceita agendar com pelo menos 10 min de antecedência; a folga
# cobre o tempo de subir as fotos antes da chamada final.
NATIVE_MIN_LEAD = timedelta(minutes=15)


@dataclass
class NativeReport:
    scheduled: list[str] = field(default_factory=list)
    would_schedule: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    too_soon: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.failed or self.invalid)


def schedule_native(
    posts: list[Post],
    state: StateStore,
    now: datetime,
    make_publisher: Callable[[str], Publisher],
    platform: str = "facebook",
    dry_run: bool = False,
) -> NativeReport:
    """Entrega de uma vez à rede todos os posts futuros, com data marcada.

    Só o Facebook tem isso na API. Depois de agendado, o post fica no
    Facebook (Business Suite > Planejador) e o 'run' não mexe mais nele.
    """
    report = NativeReport()
    publisher: Publisher | None = None
    for post in sorted(posts, key=lambda p: p.publish_at):
        if platform not in post.platforms or not post.enabled or state.is_done(post.id, platform):
            continue
        key = f"{post.id} [{platform}]"
        if not post.valid:
            report.invalid.append(f"{post.id}: {'; '.join(post.errors)}")
            continue
        if post.publish_at - now < NATIVE_MIN_LEAD:
            # Muito em cima da hora pro Facebook aceitar; o 'run' publica na hora.
            report.too_soon.append(key)
            continue
        if dry_run:
            logger.info("[dry-run] agendaria %s para %s", key, post.publish_at.isoformat())
            report.would_schedule.append(key)
            continue

        try:
            if publisher is None:
                publisher = make_publisher(platform)
            remote_id = publisher.publish(post, scheduled_for=post.publish_at)
        except MissingCredentialError as exc:
            report.failed.append(f"{key}: {exc}")
            break
        except RateLimitError as exc:
            logger.error("Rate limit em %s: %s — rode de novo mais tarde (o que já foi fica).", key, exc)
            report.failed.append(f"{key}: rate limit")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("Falhou ao agendar %s: %s", key, exc)
            report.failed.append(f"{key}: {exc}")
            continue

        state.mark_scheduled(post.id, platform, remote_id, post.publish_at.isoformat())
        logger.info("Agendado %s para %s -> %s", key, post.publish_at.isoformat(), remote_id)
        report.scheduled.append(key)
    return report
