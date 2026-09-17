"""Extração heurística de preço/desconto a partir do texto da mensagem.

É heurística mesmo: mensagem de grupo de promoção não tem formato único.
Quando não dá pra identificar com confiança, os campos voltam None — melhor
faltar um dado na legenda do que inventar um preço errado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PRICE_RE = re.compile(r"R\$\s?\d{1,3}(?:\.\d{3})*,\d{2}")
_DISCOUNT_RE = re.compile(r"(\d{1,3})\s?%\s?(?:off|de desconto)?", re.IGNORECASE)
_DE_POR_RE = re.compile(
    r"de\s+(R\$\s?\d{1,3}(?:\.\d{3})*,\d{2})\s+por\s+(R\$\s?\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PriceInfo:
    price: str | None = None
    original_price: str | None = None
    discount_pct: str | None = None


def extract_price_info(text: str) -> PriceInfo:
    if not text:
        return PriceInfo()

    de_por = _DE_POR_RE.search(text)
    if de_por:
        original, final = de_por.group(1), de_por.group(2)
    else:
        prices = _PRICE_RE.findall(text)
        if len(prices) == 1:
            original, final = None, prices[0]
        elif len(prices) >= 2:
            # Sem "de ... por ..." explícito: assume que o menor valor é o
            # preço com desconto e o maior é o original.
            ordenados = sorted(prices, key=_to_float)
            final, original = ordenados[0], ordenados[-1]
        else:
            original, final = None, None

    discount_match = _DISCOUNT_RE.search(text)
    discount = f"{discount_match.group(1)}%" if discount_match else None

    return PriceInfo(price=final, original_price=original, discount_pct=discount)


def _to_float(price: str) -> float:
    digits = price.replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return 0.0
