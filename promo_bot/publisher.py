"""Publicação do resultado do pipeline: log local + (opcional) chat de revisão no Telegram."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from promo_bot.pipeline import PromoResult

logger = logging.getLogger(__name__)


def save_to_log(result: PromoResult, log_path: str) -> None:
    """Acrescenta o resultado a um arquivo JSONL local (histórico, nunca sobrescreve)."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")


async def send_for_review(client: Any, chat: str, result: PromoResult) -> None:
    """Envia legenda + roteiro + link para um chat/canal de revisão.

    Recebe um `client` do Telethon já conectado (ver telegram_listener.py)
    para não abrir uma segunda conexão. O bot nunca publica direto num canal
    público sozinho — sempre "gera e manda revisar" antes de você postar.
    """
    message = f"{result.caption}\n\n📝 Roteiro sugerido:\n{result.script}"
    await client.send_message(chat, message)
    logger.info("Enviado para revisão em %s (loja=%s)", chat, result.store)
