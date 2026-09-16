"""Config por cliente (campanha/adset-modelo/página) + credenciais globais.

Separar isso em dois arquivos resolve o problema que tivemos: os scripts
antigos tinham CAMPAIGN_ID/MODEL_ADSET_ID escritos direto no código, e ficou
fácil perder o controle de qual campanha cada script realmente apontava.
Agora cada cliente vive num JSON próprio (clients/<nome>.json, sem segredo,
pode ir pro Git) e é sempre passado explicitamente via --client.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClientConfig:
    name: str
    campaign_id: str
    model_adset_id: str
    page_id: str
    excel_file: str
    images_folder: str
    instagram_actor_id: str | None = None
    authorization_category: str | None = None
    ad_name: str = "01"
    fallback_state: str | None = None


REQUIRED_CLIENT_FIELDS = ["name", "campaign_id", "model_adset_id", "page_id", "excel_file", "images_folder"]


def load_client(path: str) -> ClientConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    missing = [field for field in REQUIRED_CLIENT_FIELDS if not data.get(field)]
    if missing:
        raise ValueError(f"Config de cliente '{path}' sem os campos obrigatórios: {missing}")

    return ClientConfig(
        name=str(data["name"]),
        campaign_id=str(data["campaign_id"]),
        model_adset_id=str(data["model_adset_id"]),
        page_id=str(data["page_id"]),
        excel_file=data["excel_file"],
        images_folder=data["images_folder"],
        instagram_actor_id=str(data["instagram_actor_id"]) if data.get("instagram_actor_id") else None,
        authorization_category=data.get("authorization_category") or None,
        ad_name=str(data.get("ad_name", "01")),
        fallback_state=data.get("fallback_state") or None,
    )


@dataclass(frozen=True)
class Secrets:
    access_token: str
    ad_account_id: str
    api_version: str


REQUIRED_SECRET_FIELDS = ["ACCESS_TOKEN", "AD_ACCOUNT_ID", "API_VERSION"]


def load_secrets(config_path: str = "config.py") -> Secrets:
    """Carrega ACCESS_TOKEN / AD_ACCOUNT_ID / API_VERSION de um config.py local
    (mesmo formato já usado antes — arquivo nunca commitado, fica só na máquina)."""

    resolved = Path(config_path)
    if not resolved.is_file():
        example = resolved.with_name("config.example.py")
        if not example.is_file():
            example = Path(__file__).resolve().parent.parent / "config.example.py"

        if example.is_file():
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            raise FileNotFoundError(
                f"Criei {resolved} agora (a partir do modelo). Abra esse arquivo, preencha "
                "ACCESS_TOKEN / AD_ACCOUNT_ID / API_VERSION com os valores reais, salve "
                "(Ctrl+S) e rode o comando de novo."
            )

        raise FileNotFoundError(
            f"Não encontrei {config_path} nem um config.example.py para copiar. Crie "
            f"{config_path} manualmente com ACCESS_TOKEN, AD_ACCOUNT_ID e API_VERSION."
        )

    spec = importlib.util.spec_from_file_location("meta_ads_ops_local_config", resolved)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(module)

    missing = [field for field in REQUIRED_SECRET_FIELDS if not getattr(module, field, None)]
    if missing:
        raise ValueError(f"{config_path} está sem: {missing}")

    account_id = module.AD_ACCOUNT_ID
    if not account_id.startswith("act_"):
        account_id = f"act_{account_id}"

    return Secrets(
        access_token=module.ACCESS_TOKEN,
        ad_account_id=account_id,
        api_version=module.API_VERSION,
    )
