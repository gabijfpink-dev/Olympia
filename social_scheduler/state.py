"""Registro do que já foi publicado — é o que impede post duplicado.

Um arquivo JSON por agenda ({id_do_post: {plataforma: {...}}}), gravado logo
depois de cada publicação: se o processo cair no meio, o que já saiu fica
anotado e não é repostado na próxima rodada.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

PUBLISHED = "published"
SCHEDULED = "scheduled"  # já entregue à rede com data marcada (agendamento nativo do Facebook)
FAILED = "failed"

LOCK_STALE_SECONDS = 2 * 60 * 60


class LockedError(RuntimeError):
    """Outra execução do agendador ainda está rodando com o mesmo estado."""


def default_state_path(schedule_path: str) -> str:
    name = os.path.splitext(os.path.basename(schedule_path))[0]
    return os.path.join(".state", f"social_{name}.json")


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, dict[str, dict[str, Any]]] = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)

    def get(self, post_id: str, platform: str) -> dict[str, Any]:
        return self.data.get(post_id, {}).get(platform, {})

    def is_published(self, post_id: str, platform: str) -> bool:
        return self.get(post_id, platform).get("status") == PUBLISHED

    def is_done(self, post_id: str, platform: str) -> bool:
        """Publicado ou já agendado na própria rede: o runner não deve mexer."""
        return self.get(post_id, platform).get("status") in (PUBLISHED, SCHEDULED)

    def attempts(self, post_id: str, platform: str) -> int:
        return int(self.get(post_id, platform).get("attempts", 0))

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def mark_published(self, post_id: str, platform: str, remote_id: str) -> None:
        self.data.setdefault(post_id, {})[platform] = {
            "status": PUBLISHED,
            "remote_id": remote_id,
            "published_at": self._now(),
            "attempts": self.attempts(post_id, platform) + 1,
        }
        self.save()

    def mark_scheduled(self, post_id: str, platform: str, remote_id: str, scheduled_for: str) -> None:
        self.data.setdefault(post_id, {})[platform] = {
            "status": SCHEDULED,
            "remote_id": remote_id,
            "scheduled_for": scheduled_for,
            "scheduled_at": self._now(),
            "attempts": self.attempts(post_id, platform) + 1,
        }
        self.save()

    def mark_failed(self, post_id: str, platform: str, error: str) -> None:
        self.data.setdefault(post_id, {})[platform] = {
            "status": FAILED,
            "error": error[:1000],
            "failed_at": self._now(),
            "attempts": self.attempts(post_id, platform) + 1,
        }
        self.save()

    def reset(self, post_id: str, platform: str | None = None) -> bool:
        if post_id not in self.data:
            return False
        if platform:
            removed = self.data[post_id].pop(platform, None) is not None
            if not self.data[post_id]:
                del self.data[post_id]
        else:
            del self.data[post_id]
            removed = True
        self.save()
        return removed

    def save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self.path)


class RunLock:
    """Evita duas rodadas simultâneas (ex.: agendador disparando de novo
    enquanto um Reels ainda está processando) publicarem o mesmo post."""

    def __init__(self, state_path: str):
        self.path = state_path + ".lock"
        self.fd: int | None = None

    def __enter__(self) -> "RunLock":
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        if os.path.exists(self.path) and time.time() - os.path.getmtime(self.path) > LOCK_STALE_SECONDS:
            os.remove(self.path)  # sobra de uma execução que morreu
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise LockedError(
                f"Outra execução está em andamento ({self.path}). Se tiver certeza de que "
                "não há nenhuma rodando, apague esse arquivo."
            ) from None
        os.write(self.fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
            os.remove(self.path)
