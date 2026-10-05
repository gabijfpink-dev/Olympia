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
            if state.is_published(post.id, platform):
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
