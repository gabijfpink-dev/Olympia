"""Reconhecimento de loja a partir do domínio do link."""

from __future__ import annotations

from urllib.parse import urlparse

# Domínios conhecidos por loja, incluindo encurtadores oficiais de cada uma
# (o link que circula em grupo de promoção quase sempre já vem encurtado).
# Novos domínios/lojas podem ser adicionados aqui sem mexer no resto do
# pipeline.
STORE_DOMAINS: dict[str, list[str]] = {
    "amazon": ["amazon.com.br", "amazon.com", "amzn.to", "amzn.com"],
    "shopee": ["shopee.com.br", "shopee.com", "s.shopee.com.br", "shp.ee"],
    "shein": ["shein.com", "shein.com.br", "sheinurl.com", "s.shein.com"],
    "mercadolivre": ["mercadolivre.com.br", "mercadolibre.com", "produto.mercadolivre.com.br"],
    "aliexpress": ["aliexpress.com", "s.click.aliexpress.com"],
    "magalu": ["magazineluiza.com.br", "magalu.com"],
}


def detect_store(url: str) -> str | None:
    """Retorna a chave da loja em STORE_DOMAINS, ou None se o domínio não for reconhecido."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None

    for store, domains in STORE_DOMAINS.items():
        for domain in domains:
            if host == domain or host.endswith(f".{domain}"):
                return store
    return None
