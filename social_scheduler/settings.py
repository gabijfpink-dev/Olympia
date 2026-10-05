"""Credenciais das redes, lidas de variáveis de ambiente / arquivo .env.

Um .env por cliente (ex.: .env.bebetto) — escolhido com --env-file na hora de
rodar, igual ao config_<conta>.py do meta_ads_ops. Nunca commitar esses
arquivos (já estão no .gitignore).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_GRAPH_VERSION = "v23.0"
# A LinkedIn mantém cada versão mensal da API por ~1 ano. Se começar a dar
# erro de versão, troque LINKEDIN_VERSION no .env pra um mês mais recente.
DEFAULT_LINKEDIN_VERSION = "202608"
DEFAULT_TIMEZONE = "America/Sao_Paulo"


class MissingCredentialError(RuntimeError):
    """Uma credencial necessária pra plataforma pedida não foi configurada."""


@dataclass(frozen=True)
class FacebookSettings:
    page_id: str
    page_access_token: str
    api_version: str


@dataclass(frozen=True)
class InstagramSettings:
    ig_user_id: str
    access_token: str
    api_version: str
    host: str


@dataclass(frozen=True)
class LinkedInSettings:
    access_token: str
    author_urn: str
    version: str


def load_env(env_file: str | None = None) -> None:
    # override=True: com --env-file, o arquivo do cliente manda, mesmo que
    # tenha sobrado variável de outro cliente no ambiente do terminal.
    if env_file:
        if not os.path.exists(env_file):
            raise MissingCredentialError(f"Arquivo de credenciais não encontrado: {env_file}")
        load_dotenv(env_file, override=True)
    else:
        load_dotenv()


def _require(*names: str) -> dict[str, str]:
    values = {name: (os.getenv(name) or "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise MissingCredentialError(
            "Variáveis ausentes no .env: " + ", ".join(missing) + ". Veja .env.social.example."
        )
    return values


def graph_version() -> str:
    return os.getenv("META_GRAPH_VERSION") or DEFAULT_GRAPH_VERSION


def timezone_name() -> str:
    return os.getenv("SCHEDULER_TIMEZONE") or DEFAULT_TIMEZONE


def facebook_settings() -> FacebookSettings:
    v = _require("META_PAGE_ID", "META_PAGE_ACCESS_TOKEN")
    return FacebookSettings(v["META_PAGE_ID"], v["META_PAGE_ACCESS_TOKEN"], graph_version())


def instagram_settings() -> InstagramSettings:
    v = _require("META_IG_USER_ID")
    # Login do Facebook (padrão): o token da Página publica no Instagram
    # vinculado. Login do Instagram: token próprio + host graph.instagram.com.
    token = (os.getenv("META_IG_ACCESS_TOKEN") or os.getenv("META_PAGE_ACCESS_TOKEN") or "").strip()
    if not token:
        raise MissingCredentialError(
            "Variáveis ausentes no .env: META_PAGE_ACCESS_TOKEN (ou META_IG_ACCESS_TOKEN). "
            "Veja .env.social.example."
        )
    return InstagramSettings(
        ig_user_id=v["META_IG_USER_ID"],
        access_token=token,
        api_version=graph_version(),
        host=os.getenv("META_IG_GRAPH_HOST") or "graph.facebook.com",
    )


def linkedin_settings() -> LinkedInSettings:
    v = _require("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN")
    author = v["LINKEDIN_AUTHOR_URN"]
    if not author.startswith(("urn:li:person:", "urn:li:organization:")):
        raise MissingCredentialError(
            "LINKEDIN_AUTHOR_URN deve ser urn:li:person:<id> ou urn:li:organization:<id> "
            f"(veio: {author!r}). Rode 'linkedin-auth' pra descobrir o seu."
        )
    return LinkedInSettings(
        access_token=v["LINKEDIN_ACCESS_TOKEN"],
        author_urn=author,
        version=os.getenv("LINKEDIN_VERSION") or DEFAULT_LINKEDIN_VERSION,
    )
