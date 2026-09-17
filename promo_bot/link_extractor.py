"""Extração de links de um texto de mensagem (grupo do Telegram, post, etc.)."""

from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def extract_urls(text: str) -> list[str]:
    """Retorna todos os links http(s) do texto, na ordem em que aparecem (sem duplicar)."""
    if not text:
        return []

    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.findall(text):
        url = _clean(match)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _clean(url: str) -> str:
    # Pontuação de frase colada no fim do link (".", ",", "!", ")") não é parte da URL.
    return url.rstrip(".,!?;:)")
