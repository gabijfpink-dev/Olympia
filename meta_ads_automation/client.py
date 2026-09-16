"""Inicialização da sessão com a Marketing API do Meta."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.api import FacebookAdsApi

DEFAULT_API_VERSION = "v21.0"


class MissingCredentialError(RuntimeError):
    """Levantado quando uma credencial obrigatória não foi configurada."""


@dataclass(frozen=True)
class MetaCredentials:
    app_id: str
    app_secret: str
    access_token: str
    ad_account_id: str
    api_version: str = DEFAULT_API_VERSION

    @property
    def account_id_with_prefix(self) -> str:
        return self.ad_account_id if self.ad_account_id.startswith("act_") else f"act_{self.ad_account_id}"


def load_credentials(env_file: str | None = None) -> MetaCredentials:
    """Carrega as credenciais do Meta a partir de variáveis de ambiente / arquivo .env."""
    load_dotenv(env_file) if env_file else load_dotenv()

    required = {
        "META_APP_ID": os.getenv("META_APP_ID"),
        "META_APP_SECRET": os.getenv("META_APP_SECRET"),
        "META_ACCESS_TOKEN": os.getenv("META_ACCESS_TOKEN"),
        "META_AD_ACCOUNT_ID": os.getenv("META_AD_ACCOUNT_ID"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise MissingCredentialError(
            "Variáveis de ambiente ausentes: " + ", ".join(missing) + ". Veja .env.example."
        )

    return MetaCredentials(
        app_id=required["META_APP_ID"],
        app_secret=required["META_APP_SECRET"],
        access_token=required["META_ACCESS_TOKEN"],
        ad_account_id=required["META_AD_ACCOUNT_ID"],
        api_version=os.getenv("META_API_VERSION", DEFAULT_API_VERSION),
    )


def init_api(credentials: MetaCredentials) -> AdAccount:
    """Inicializa o SDK do Facebook Business e retorna a conta de anúncios pronta para uso."""
    FacebookAdsApi.init(
        app_id=credentials.app_id,
        app_secret=credentials.app_secret,
        access_token=credentials.access_token,
        api_version=credentials.api_version,
    )
    return AdAccount(credentials.account_id_with_prefix)
