"""Cliente HTTP fino para a Graph API do Meta.

Centraliza timeout, paginação e o tratamento de rate limit — antes cada
script tratava um conjunto diferente de códigos de erro, o que fazia alguns
pararem por rate limit e outros continuarem tentando (e falhando) sem parar.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

RATE_LIMIT_CODES = {4, 17, 32, 613}
RATE_LIMIT_SUBCODES = {2446079}


class GraphError(RuntimeError):
    """Erro de negócio retornado pela Graph API (não é rate limit)."""

    def __init__(self, error: dict[str, Any]):
        self.error = error
        super().__init__(error.get("message", str(error)))


class RateLimitError(RuntimeError):
    """A Meta sinalizou rate limit. Parar e tentar de novo mais tarde."""


class GraphClient:
    def __init__(self, access_token: str, api_version: str, timeout: int = 120):
        self.access_token = access_token
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self.timeout = timeout

    def _check(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise GraphError({"message": f"Resposta inesperada da API: {payload!r}"})

        error = payload.get("error")
        if error:
            code = error.get("code")
            subcode = error.get("error_subcode")
            if code in RATE_LIMIT_CODES or subcode in RATE_LIMIT_SUBCODES:
                logger.warning("Rate limit da Meta (code=%s subcode=%s): %s", code, subcode, error)
                raise RateLimitError(error.get("message", "rate limit"))
            raise GraphError(error)

        return payload

    def _parse(self, response: requests.Response) -> dict[str, Any]:
        try:
            return self._check(response.json())
        except ValueError as exc:
            raise GraphError({"message": f"Resposta inválida da API: {response.text[:500]}"}) from exc

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        params["access_token"] = self.access_token
        r = requests.get(f"{self.base_url}/{path}", params=params, timeout=self.timeout)
        return self._parse(r)

    def post(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        data = dict(data or {})
        data["access_token"] = self.access_token
        r = requests.post(f"{self.base_url}/{path}", data=data, timeout=self.timeout)
        return self._parse(r)

    def post_image(self, path: str, file_path: str) -> dict[str, Any]:
        with open(file_path, "rb") as fh:
            r = requests.post(
                f"{self.base_url}/{path}",
                files={"filename": fh},
                data={"access_token": self.access_token},
                timeout=self.timeout,
            )
        return self._parse(r)

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(params or {})
        params.setdefault("limit", 500)
        params["access_token"] = self.access_token

        url = f"{self.base_url}/{path}"
        items: list[dict[str, Any]] = []

        while url:
            r = requests.get(url, params=params, timeout=self.timeout)
            data = self._parse(r)
            items.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = {}  # "next" já vem como URL completa, com os params embutidos

        return items
