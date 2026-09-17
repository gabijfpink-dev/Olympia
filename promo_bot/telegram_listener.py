"""Escuta grupos/canais do Telegram e roda o pipeline em cada mensagem nova.

Usa Telethon com a *sua própria conta* (não um bot) porque grupos de
promoção normalmente não deixam bots entrarem sozinhos. A automação aqui é
"minha conta lê os grupos em que eu já estou", sem entrar em grupo sozinho e
sem raspar conteúdo de terceiros além do texto das mensagens recebidas.
"""

from __future__ import annotations

import logging

from promo_bot.config import MonitorConfig, TelegramSecrets
from promo_bot.pipeline import process_message
from promo_bot.publisher import save_to_log, send_for_review

logger = logging.getLogger(__name__)


async def run_listener(secrets: TelegramSecrets, config: MonitorConfig) -> None:
    try:
        from telethon import TelegramClient, events
    except ImportError as exc:  # pragma: no cover - depende de dependência opcional
        raise RuntimeError(
            "Telethon não instalado. Rode `pip install telethon` para usar o modo --listen."
        ) from exc

    client = TelegramClient(secrets.session_name, secrets.api_id, secrets.api_hash)

    @client.on(events.NewMessage(chats=config.telegram_channels))
    async def _handler(event):  # pragma: no cover - exercitado via integração real, não em unit test
        text = event.raw_text or ""
        for result in process_message(text, config):
            save_to_log(result, config.output_log)
            logger.info("Promo detectada (%s): %s", result.store, result.affiliate_link)
            if config.review_chat:
                await send_for_review(client, config.review_chat, result)

    await client.start()
    logger.info("Conectado. Monitorando %d canal(is): %s", len(config.telegram_channels), config.telegram_channels)
    await client.run_until_disconnected()
