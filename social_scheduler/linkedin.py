"""Publicação no LinkedIn (Posts API, /rest/posts) e login OAuth pra pegar o token."""

from __future__ import annotations

import logging
import mimetypes
import re
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from social_scheduler.schedule import Post, is_url
from social_scheduler.settings import LinkedInSettings

logger = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com/rest"
AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

NETWORK_RETRY_ATTEMPTS = 3
NETWORK_RETRY_DELAYS = (5, 15)


class LinkedInError(RuntimeError):
    """Erro retornado pela API do LinkedIn."""


def _raise_for(response: requests.Response) -> None:
    if response.ok:
        return
    try:
        body = response.json()
        detail = body.get("message") or body.get("error_description") or body
    except ValueError:
        detail = response.text[:500]
    hint = ""
    if response.status_code == 401:
        hint = " — token expirado ou inválido (token do LinkedIn dura 60 dias; rode 'linkedin-auth' de novo)"
    elif response.status_code == 403:
        hint = " — o app não tem a permissão necessária (w_member_social / w_organization_social)"
    elif response.status_code == 426 or "version" in str(detail).lower():
        hint = " — versão da API vencida; atualize LINKEDIN_VERSION no .env (formato AAAAMM)"
    raise LinkedInError(f"HTTP {response.status_code}: {detail}{hint}")


class LinkedInClient:
    def __init__(self, access_token: str, version: str, timeout: int = 120):
        self.access_token = access_token
        self.version = version
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "LinkedIn-Version": self.version,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        for tentativa in range(1, NETWORK_RETRY_ATTEMPTS + 1):
            try:
                response = requests.request(method, url, timeout=self.timeout, **kwargs)
            except requests.exceptions.RequestException as exc:
                if tentativa == NETWORK_RETRY_ATTEMPTS:
                    raise LinkedInError(f"Falha de rede após {NETWORK_RETRY_ATTEMPTS} tentativas: {exc}") from exc
                logger.warning("Falha de rede no LinkedIn (%s), tentando de novo...", exc)
                time.sleep(NETWORK_RETRY_DELAYS[tentativa - 1])
                continue
            _raise_for(response)
            return response
        raise AssertionError("inalcançável")

    def post_json(self, path: str, body: dict[str, Any], params: dict[str, str] | None = None) -> requests.Response:
        return self.request("POST", f"{API_BASE}/{path}", headers=self.headers, json=body, params=params)

    def upload_image(self, owner: str, image: str) -> str:
        """Sobe uma imagem (URL ou arquivo local) e devolve o URN urn:li:image:..."""
        init = self.post_json(
            "images", {"initializeUploadRequest": {"owner": owner}}, params={"action": "initializeUpload"}
        ).json()["value"]

        if is_url(image):
            content = self.request("GET", image).content
            content_type = mimetypes.guess_type(urlparse(image).path)[0]
        else:
            with open(image, "rb") as fh:
                content = fh.read()
            content_type = mimetypes.guess_type(image)[0]

        self.request(
            "PUT",
            init["uploadUrl"],
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": content_type or "application/octet-stream",
            },
            data=content,
        )
        return init["image"]


class LinkedInPublisher:
    platform = "linkedin"

    def __init__(self, settings: LinkedInSettings, client: LinkedInClient | None = None):
        self.author = settings.author_urn
        self.client = client or LinkedInClient(settings.access_token, settings.version)

    def publish(self, post: Post) -> str:
        body: dict[str, Any] = {
            "author": self.author,
            # Texto do LinkedIn usa "little text format": ( ) [ ] { } < > @ | ~ _ * \
            # têm significado especial e precisam de escape pra sair literal.
            "commentary": escape_commentary(post.text_for(self.platform)),
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        if post.images:
            urns = [self.client.upload_image(self.author, image) for image in post.images]
            if len(urns) == 1:
                body["content"] = {"media": {"id": urns[0], "altText": post.alt_text}}
            else:
                body["content"] = {
                    "multiImage": {"images": [{"id": urn, "altText": post.alt_text} for urn in urns]}
                }
        elif post.link:
            body["content"] = {"article": {"source": post.link, "title": post.link_title or post.link}}

        response = self.client.post_json("posts", body)
        return response.headers.get("x-restli-id") or response.headers.get("x-linkedin-id", "")


_LITTLE_TEXT_RESERVED = set("\\|{}@[]()<>#*_~")
_HASHTAG = re.compile(r"#(\w+)")


def _escape(text: str) -> str:
    return "".join("\\" + ch if ch in _LITTLE_TEXT_RESERVED else ch for ch in text)


def escape_commentary(text: str) -> str:
    """Escapa o texto pro formato do LinkedIn, mantendo #hashtags clicáveis."""
    parts: list[str] = []
    last = 0
    for match in _HASHTAG.finditer(text):
        parts.append(_escape(text[last : match.start()]))
        parts.append("{hashtag|\\#|" + match.group(1) + "}")
        last = match.end()
    parts.append(_escape(text[last:]))
    return "".join(parts)


# --- OAuth (pra gerar o token uma vez a cada ~60 dias) -----------------------


def authorization_url(client_id: str, redirect_uri: str, scopes: list[str]) -> tuple[str, str]:
    state = secrets.token_urlsafe(16)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(scopes),
        }
    )
    return f"{AUTH_URL}?{query}", state


def extract_code(redirected_url: str, expected_state: str) -> str:
    params = parse_qs(urlparse(redirected_url.strip()).query)
    if "error" in params:
        raise LinkedInError(f"Autorização negada: {params.get('error_description', params['error'])[0]}")
    if params.get("state", [""])[0] != expected_state:
        raise LinkedInError("O parâmetro 'state' não confere — copie a URL inteira desta mesma tentativa.")
    code = params.get("code", [""])[0]
    if not code:
        raise LinkedInError("Não achei o 'code' na URL colada.")
    return code


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict[str, Any]:
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=60,
    )
    _raise_for(response)
    return response.json()


def person_urn(access_token: str) -> tuple[str, str]:
    response = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=60)
    _raise_for(response)
    info = response.json()
    return f"urn:li:person:{info['sub']}", info.get("name", "")
