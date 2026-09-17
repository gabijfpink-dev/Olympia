"""Pipeline: texto de mensagem -> link(s) de afiliado + legenda/roteiro prontos."""

from __future__ import annotations

from dataclasses import dataclass

from promo_bot.affiliate import build_affiliate_link
from promo_bot.caption_generator import Promo, generate_caption, generate_script
from promo_bot.config import MonitorConfig
from promo_bot.link_extractor import extract_urls
from promo_bot.price_extractor import extract_price_info
from promo_bot.stores import detect_store


@dataclass
class PromoResult:
    store: str
    original_url: str
    affiliate_link: str
    caption: str
    script: str
    source_text: str


def process_message(text: str, config: MonitorConfig, *, seed: int | None = None) -> list[PromoResult]:
    """Processa uma mensagem e devolve uma promoção pronta para cada link reconhecido.

    Aplica os filtros de `config` (loja permitida, palavras-chave) e ignora
    silenciosamente link de loja não configurada ou sem regra de afiliado —
    a ideia é nunca gerar um post com link sem o código de afiliada.
    """
    if not text or not _passes_keyword_filters(text, config):
        return []

    price_info = extract_price_info(text)
    title = _guess_title(text)

    results = []
    for url in extract_urls(text):
        store = detect_store(url)
        if store is None or store not in config.allowed_stores or store not in config.affiliates:
            continue

        affiliate_link = build_affiliate_link(url, store, config.affiliates)
        promo = Promo(
            store=store,
            link=affiliate_link,
            title=title,
            price=price_info.price,
            original_price=price_info.original_price,
            discount_pct=price_info.discount_pct,
        )

        results.append(
            PromoResult(
                store=store,
                original_url=url,
                affiliate_link=affiliate_link,
                caption=generate_caption(promo, seed=seed),
                script=generate_script(promo, seed=seed),
                source_text=text,
            )
        )

    return results


def _passes_keyword_filters(text: str, config: MonitorConfig) -> bool:
    lowered = text.lower()
    if config.keywords_exclude and any(k in lowered for k in config.keywords_exclude):
        return False
    if config.keywords_include and not any(k in lowered for k in config.keywords_include):
        return False
    return True


def _guess_title(text: str) -> str | None:
    """Usa a primeira linha "de verdade" da mensagem (sem link, sem emoji solto) como título."""
    for line in text.splitlines():
        stripped = line.strip(" -•*✅🔥📦🧡👗💛🚀🔵💥⚡😱\t")
        if stripped and not stripped.startswith("http"):
            return stripped
    return None
