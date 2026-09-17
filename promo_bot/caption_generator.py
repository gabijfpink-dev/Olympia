"""Geração automática de legenda e roteiro curto a partir de uma promoção detectada."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class Promo:
    store: str
    link: str
    title: str | None = None
    price: str | None = None
    original_price: str | None = None
    discount_pct: str | None = None


STORE_EMOJI = {
    "amazon": "📦",
    "shopee": "🧡",
    "shein": "👗",
    "mercadolivre": "💛",
    "aliexpress": "🚀",
    "magalu": "🔵",
}

STORE_LABEL = {
    "amazon": "Amazon",
    "shopee": "Shopee",
    "shein": "Shein",
    "mercadolivre": "Mercado Livre",
    "aliexpress": "AliExpress",
    "magalu": "Magalu",
}

_HOOKS = [
    "🚨 ACHADINHO DO DIA",
    "🔥 CORRE QUE ACABA RÁPIDO",
    "😱 OLHA ESSE PREÇO",
    "⚡ OFERTA RELÂMPAGO",
    "💥 PREÇO DE LIQUIDAÇÃO",
]

_CTAS = [
    "Garanta o seu antes que acabe 👇",
    "Link na descrição, corre lá 👇",
    "Poucas unidades, não deixa pra depois 👇",
    "Aproveita antes que o preço volte 👇",
]


def generate_caption(promo: Promo, *, seed: int | None = None) -> str:
    """Monta uma legenda pronta pra postar: gancho + produto + preço + CTA + link."""
    rng = random.Random(seed)
    emoji = STORE_EMOJI.get(promo.store, "🛍️")
    label = STORE_LABEL.get(promo.store, promo.store.title())

    lines = [f"{rng.choice(_HOOKS)} {emoji}", (promo.title or f"Promoção {label}").strip()]

    price_line = _price_line(promo)
    if price_line:
        lines.append(price_line)

    lines.append(f"🏬 {label}")
    lines.append(rng.choice(_CTAS))
    lines.append(promo.link)

    return "\n".join(lines)


def _price_line(promo: Promo) -> str | None:
    if promo.price and promo.original_price:
        line = f"~~{promo.original_price}~~ por {promo.price}"
        if promo.discount_pct:
            line += f" ({promo.discount_pct} OFF)"
        return line
    if promo.price:
        return f"💰 {promo.price}"
    return None


_ROTEIRO_TEMPLATE = """\
[GANCHO - 0-3s]
{hook} Vocês PRECISAM ver esse preço da {label}!

[PRODUTO - 3-8s]
{title}

[PREÇO - 8-12s]
{price_call}

[CTA - final]
{cta} Link fixado nos comentários / na bio!
"""


def generate_script(promo: Promo, *, seed: int | None = None) -> str:
    """Roteiro curto (Reels/TikTok/Shorts) para gravar em cima da promoção."""
    rng = random.Random(seed)
    label = STORE_LABEL.get(promo.store, promo.store.title())

    if promo.price and promo.original_price:
        price_call = f"De {promo.original_price} por apenas {promo.price}!"
    elif promo.price:
        price_call = f"Só {promo.price}!"
    else:
        price_call = "Com um desconto gigante, corre ver!"

    return _ROTEIRO_TEMPLATE.format(
        hook=rng.choice(_HOOKS),
        label=label,
        title=promo.title or f"Achadinho da {label}",
        price_call=price_call,
        cta=rng.choice(_CTAS),
    )
