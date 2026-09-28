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
    # Filtra a busca de geolocalização pra só aceitar resultado nessa região
    # (ex.: "Federal District") — evita pegar cidade homônima de outro estado
    # (ex.: "Sobradinho" existe no DF e também na Bahia).
    preferred_region: str | None = None
    # None = só "city" (comportamento original, usado pelo Bebetto). Em
    # capitais como Brasília, onde regiões administrativas são cadastradas
    # como bairro, configure ["city", "neighborhood"] no clients/<nome>.json.
    geo_location_types: list | None = None  # type: ignore[assignment]
    # "single" (padrão, 1 imagem/coluna "image") ou "carousel" (2 cartões,
    # colunas "image_card1"/"image_card2" na planilha, mesmo link nos dois).
    creative_type: str = "single"


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
        preferred_region=data.get("preferred_region") or None,
        geo_location_types=list(data["geo_location_types"]) if data.get("geo_location_types") else None,
        creative_type=str(data.get("creative_type", "single")),
    )


@dataclass(frozen=True)
class BootstrapConfig:
    """Dados pra criar do zero: campanha (CBO) + 1º adset + criativo + anúncio.

    Depois de rodar, o campaign_id/model_adset_id resultantes vão pro
    clients/<nome>.json normal, e o fluxo sync/activate segue igual ao
    de qualquer outro cliente a partir daí."""

    campaign_name: str
    page_id: str
    region: str
    url: str
    images_folder: str
    primary_text: str
    headline: str
    description: str
    daily_budget_cents: int
    # "single" (usa `image`) ou "carousel" (usa `image_card1`/`image_card2`).
    creative_type: str = "single"
    image: str | None = None
    image_card1: str | None = None
    image_card2: str | None = None
    objective: str = "OUTCOME_TRAFFIC"
    optimization_goal: str = "LINK_CLICKS"
    billing_event: str = "IMPRESSIONS"
    instagram_actor_id: str | None = None
    authorization_category: str | None = None
    special_ad_categories: list = None  # type: ignore[assignment]
    age_min: int = 18
    age_max: int = 65
    genders: list = None  # type: ignore[assignment]
    radius_km: int = 40
    fallback_state: str | None = None
    preferred_region: str | None = None
    ad_name: str = "01"

    def __post_init__(self):
        if self.special_ad_categories is None:
            object.__setattr__(self, "special_ad_categories", [])
        if self.genders is None:
            object.__setattr__(self, "genders", [1, 2])
        if self.creative_type == "carousel":
            if not self.image_card1 or not self.image_card2:
                raise ValueError("creative_type='carousel' precisa de image_card1 e image_card2.")
        elif not self.image:
            raise ValueError("creative_type='single' precisa de image.")


REQUIRED_BOOTSTRAP_FIELDS = [
    "campaign_name", "page_id", "region", "url", "images_folder",
    "primary_text", "headline", "description", "daily_budget_cents",
]


def load_bootstrap(path: str) -> BootstrapConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    missing = [field for field in REQUIRED_BOOTSTRAP_FIELDS if not data.get(field) and data.get(field) != 0]
    if missing:
        raise ValueError(f"Config de bootstrap '{path}' sem os campos obrigatórios: {missing}")

    return BootstrapConfig(
        campaign_name=str(data["campaign_name"]),
        page_id=str(data["page_id"]),
        region=str(data["region"]),
        url=str(data["url"]),
        images_folder=str(data["images_folder"]),
        primary_text=str(data["primary_text"]),
        headline=str(data["headline"]),
        description=str(data["description"]),
        daily_budget_cents=int(data["daily_budget_cents"]),
        creative_type=str(data.get("creative_type", "single")),
        image=str(data["image"]) if data.get("image") else None,
        image_card1=str(data["image_card1"]) if data.get("image_card1") else None,
        image_card2=str(data["image_card2"]) if data.get("image_card2") else None,
        objective=str(data.get("objective", "OUTCOME_TRAFFIC")),
        optimization_goal=str(data.get("optimization_goal", "LINK_CLICKS")),
        billing_event=str(data.get("billing_event", "IMPRESSIONS")),
        instagram_actor_id=str(data["instagram_actor_id"]) if data.get("instagram_actor_id") else None,
        authorization_category=data.get("authorization_category") or None,
        special_ad_categories=list(data.get("special_ad_categories", [])),
        age_min=int(data.get("age_min", 18)),
        age_max=int(data.get("age_max", 65)),
        genders=list(data.get("genders", [1, 2])),
        radius_km=int(data.get("radius_km", 40)),
        fallback_state=data.get("fallback_state") or None,
        preferred_region=data.get("preferred_region") or None,
        ad_name=str(data.get("ad_name", "01")),
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
