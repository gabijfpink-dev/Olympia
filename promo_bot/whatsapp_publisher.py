"""Envio de promoções para grupos do WhatsApp via automação do WhatsApp Web.

ATENÇÃO — leia antes de ligar isso:

Isso NÃO é a API oficial do WhatsApp Business. É automação de navegador em
cima do WhatsApp Web comum (mesmo princípio de ferramentas tipo
whatsapp-web.js) — o WhatsApp pode restringir ou banir um número que ele
identifique como bot (volume alto, mensagens muito repetitivas ou muito
rápidas). Mitigamos isso com um delay aleatório entre cada envio, mas o
risco nunca é zero. Recomendação: use um número secundário, não o seu
principal, pelo menos enquanto estiver testando.

Os seletores usados aqui (aria-label em português) refletem a estrutura do
WhatsApp Web no momento em que isso foi escrito — o WhatsApp muda o layout
de vez em quando, então se algum passo parar de funcionar, é aqui que se
ajusta primeiro.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from promo_bot.pipeline import PromoResult

logger = logging.getLogger(__name__)

MIN_DELAY_SECONDS = 8
MAX_DELAY_SECONDS = 25

_SEARCH_BOX_SELECTOR = 'div[contenteditable="true"][aria-label="Caixa de texto de pesquisa"]'
_MESSAGE_BOX_SELECTOR = 'div[contenteditable="true"][aria-label="Digite uma mensagem"]'


def format_whatsapp_message(result: PromoResult) -> str:
    """Monta o texto final enviado ao grupo — só a legenda, sem o roteiro de vídeo."""
    return result.caption


class WhatsAppSession:
    """Mantém um navegador logado no WhatsApp Web entre vários envios."""

    def __init__(self, session_dir: str = "whatsapp_session", headless: bool = False):
        self.session_dir = session_dir
        self.headless = headless
        self._playwright = None
        self._context = None
        self._page = None

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depende de dependência opcional
            raise RuntimeError(
                "Playwright não instalado. Rode `pip install playwright` e depois "
                "`playwright install chromium` para usar o envio pro WhatsApp."
            ) from exc

        Path(self.session_dir).mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            self.session_dir, headless=self.headless
        )
        self._page = await self._context.new_page()
        await self._page.goto("https://web.whatsapp.com")
        logger.info(
            "Abrindo WhatsApp Web — se aparecer QR code, escaneie no navegador que abriu "
            "(fica salvo pras próximas vezes, não precisa escanear de novo)."
        )
        await self._page.wait_for_selector(_SEARCH_BOX_SELECTOR, timeout=120_000)
        logger.info("WhatsApp Web conectado.")

    async def send_message(self, group_name: str, message: str) -> None:
        if self._page is None:
            raise RuntimeError("Sessão do WhatsApp não iniciada — chame start() primeiro.")

        search_box = self._page.locator(_SEARCH_BOX_SELECTOR)
        await search_box.click()
        await search_box.fill(group_name)
        await self._page.wait_for_timeout(1500)
        await self._page.locator(f'span[title="{group_name}"]').first.click()

        message_box = self._page.locator(_MESSAGE_BOX_SELECTOR)
        await message_box.click()
        lines = message.split("\n")
        for i, line in enumerate(lines):
            await message_box.type(line)
            if i < len(lines) - 1:
                await message_box.press("Shift+Enter")
        await message_box.press("Enter")

        logger.info("Mensagem enviada para o grupo '%s'.", group_name)
        delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
        logger.info("Aguardando %.1fs antes do próximo envio (evita padrão de bot).", delay)
        await asyncio.sleep(delay)

    async def send_to_groups(self, groups: list[str], message: str) -> None:
        for group_name in groups:
            await self.send_message(group_name, message)

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
