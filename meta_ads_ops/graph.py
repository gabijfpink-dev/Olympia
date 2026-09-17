"""Cliente HTTP fino para a Graph API do Meta.

Centraliza timeout, paginação e o tratamento de rate limit — antes cada
script tratava um conjunto diferente de códigos de erro, o que fazia alguns
pararem por rate limit e outros continuarem tentando (e falhando) sem parar.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

RATE_LIMIT_CODES = {4, 17, 32, 613}
RATE_LIMIT_SUBCODES = {2446079}

# Falha de rede (timeout, conexão recusada/perdida) é diferente de rate limit
# ou erro de negócio da API: costuma ser um soluço pontual da rede local ou
# do lado da Meta, então vale tentar de novo sozinho antes de derrubar uma
# execução inteira (importante em lotes de 100+ cidades, ~30min corridos).
NETWORK_RETRY_ATTEMPTS = 3
NETWORK_RETRY_DELAYS = (5, 15)  # segundos entre as tentativas 1->2 e 2->3


class GraphError(RuntimeError):
    """Erro de negócio retornado pela Graph API (não é rate limit)."""

    def __init__(self, error: dict[str, Any]):
        self.error = error

        partes = []
        if error.get("message"):
            partes.append(error["message"])
        if error.get("error_user_title"):
            partes.append(f"título: {error['error_user_title']}")
        if error.get("error_user_msg"):
            partes.append(f"detalhe: {error['error_user_msg']}")
        if error.get("error_subcode"):
            partes.append(f"subcode: {error['error_subcode']}")
        blame = (error.get("error_data") or {}).get("blame_field_specs")
        if blame:
            partes.append(f"campo apontado: {blame}")

        super().__init__(" | ".join(partes) if partes else str(error))


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

    def _send(self, send_once) -> requests.Response:
        """Executa send_once() com retry para falhas de rede (não de negócio)."""
        for tentativa in range(1, NETWORK_RETRY_ATTEMPTS + 1):
            try:
                return send_once()
            except requests.exceptions.RequestException as exc:
                if tentativa == NETWORK_RETRY_ATTEMPTS:
                    raise GraphError(
                        {"message": f"Falha de rede após {NETWORK_RETRY_ATTEMPTS} tentativas: {exc}"}
                    ) from exc
                atraso = NETWORK_RETRY_DELAYS[tentativa - 1]
                logger.warning(
                    "Falha de rede (tentativa %d/%d): %s — tentando de novo em %ds",
                    tentativa, NETWORK_RETRY_ATTEMPTS, exc, atraso,
                )
                time.sleep(atraso)
        raise AssertionError("inalcançável")  # loop sempre retorna ou levanta

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        params["access_token"] = self.access_token
        r = self._send(lambda: requests.get(f"{self.base_url}/{path}", params=params, timeout=self.timeout))
        return self._parse(r)

    def post(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        data = dict(data or {})
        data["access_token"] = self.access_token
        r = self._send(lambda: requests.post(f"{self.base_url}/{path}", data=data, timeout=self.timeout))
        return self._parse(r)

    def post_image(self, path: str, file_path: str) -> dict[str, Any]:
        def enviar():
            with open(file_path, "rb") as fh:
                return requests.post(
                    f"{self.base_url}/{path}",
                    files={"filename": fh},
                    data={"access_token": self.access_token},
                    timeout=self.timeout,
                )

        return self._parse(self._send(enviar))

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(params or {})
        params.setdefault("limit", 500)
        params["access_token"] = self.access_token

        url = f"{self.base_url}/{path}"
        items: list[dict[str, Any]] = []

        while url:
            call_params = params
            r = self._send(lambda: requests.get(url, params=call_params, timeout=self.timeout))
            data = self._parse(r)
            items.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = {}  # "next" já vem como URL completa, com os params embutidos

        return items
