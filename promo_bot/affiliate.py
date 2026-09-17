"""Conversão de um link de produto no link de afiliado da loja correspondente."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from promo_bot.config import AffiliateRule


class AffiliateRuleMissingError(RuntimeError):
    """Loja reconhecida, mas sem regra de afiliado configurada para ela."""


def build_affiliate_link(url: str, store: str, rules: dict[str, AffiliateRule]) -> str:
    """Aplica a AffiliateRule da loja e devolve o link com o seu código de afiliada.

    Nunca devolve o link "cru": se a loja não tiver regra configurada, levanta
    AffiliateRuleMissingError — melhor falhar alto do que postar um link sem
    afiliação por engano.
    """
    rule = rules.get(store)
    if rule is None:
        raise AffiliateRuleMissingError(
            f"Nenhuma regra de afiliado configurada para a loja '{store}'. "
            'Adicione uma entrada em "affiliates" no arquivo de config.'
        )

    result = url
    if rule.template:
        result = rule.template.format(url=result)
    if rule.params:
        result = _merge_query_params(result, rule.params)
    return result


def _merge_query_params(url: str, extra_params: dict[str, str]) -> str:
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query))
    params.update(extra_params)
    return urlunparse(parsed._replace(query=urlencode(params)))
