"""Construção dos parâmetros e orquestração da criação de Campanhas, Conjuntos de
Anúncios e Anúncios no Meta Ads a partir de um arquivo de configuração enviado pelo usuário.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.campaign import Campaign
from facebook_business.exceptions import FacebookRequestError

from meta_ads_automation.image_utils import ImageUploader

logger = logging.getLogger(__name__)


class CreationError(RuntimeError):
    """Erro ao criar um objeto na Marketing API, com o contexto do que falhou."""


# ---------------------------------------------------------------------------
# Funções puras de construção de parâmetros (sem chamadas de rede), fáceis de testar.
# Qualquer chave dentro de "settings" no JSON do usuário é copiada como está para os
# parâmetros finais, sobrescrevendo os campos "nomeados" caso haja conflito — isso
# garante que qualquer configuração adicional enviada pelo usuário seja respeitada.
# ---------------------------------------------------------------------------


def build_campaign_params(campaign_cfg: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        Campaign.Field.name: campaign_cfg["name"],
        Campaign.Field.objective: campaign_cfg.get("objective", "OUTCOME_TRAFFIC"),
        Campaign.Field.status: campaign_cfg.get("status", "PAUSED"),
        Campaign.Field.special_ad_categories: campaign_cfg.get("special_ad_categories", []),
    }
    if "buying_type" in campaign_cfg:
        params[Campaign.Field.buying_type] = campaign_cfg["buying_type"]
    if "daily_budget" in campaign_cfg:
        params[Campaign.Field.daily_budget] = campaign_cfg["daily_budget"]
    if "lifetime_budget" in campaign_cfg:
        params[Campaign.Field.lifetime_budget] = campaign_cfg["lifetime_budget"]
    if "bid_strategy" in campaign_cfg:
        params[Campaign.Field.bid_strategy] = campaign_cfg["bid_strategy"]

    params.update(campaign_cfg.get("settings", {}))
    return params


def build_adset_params(adset_cfg: dict[str, Any], campaign_id: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        AdSet.Field.name: adset_cfg["name"],
        AdSet.Field.campaign_id: campaign_id,
        AdSet.Field.status: adset_cfg.get("status", "PAUSED"),
        AdSet.Field.billing_event: adset_cfg.get("billing_event", "IMPRESSIONS"),
        AdSet.Field.optimization_goal: adset_cfg.get("optimization_goal", "LINK_CLICKS"),
    }

    if "daily_budget" in adset_cfg:
        params[AdSet.Field.daily_budget] = adset_cfg["daily_budget"]
    if "lifetime_budget" in adset_cfg:
        params[AdSet.Field.lifetime_budget] = adset_cfg["lifetime_budget"]
    if "bid_amount" in adset_cfg:
        params[AdSet.Field.bid_amount] = adset_cfg["bid_amount"]
    if "bid_strategy" in adset_cfg:
        params[AdSet.Field.bid_strategy] = adset_cfg["bid_strategy"]
    if "start_time" in adset_cfg:
        params[AdSet.Field.start_time] = adset_cfg["start_time"]
    if "end_time" in adset_cfg:
        params[AdSet.Field.end_time] = adset_cfg["end_time"]
    if "targeting" in adset_cfg:
        params[AdSet.Field.targeting] = adset_cfg["targeting"]
    if "destination_type" in adset_cfg:
        params[AdSet.Field.destination_type] = adset_cfg["destination_type"]
    if "promoted_object" in adset_cfg:
        params[AdSet.Field.promoted_object] = adset_cfg["promoted_object"]

    params.update(adset_cfg.get("settings", {}))
    return params


def build_creative_object_story_spec(
    creative_cfg: dict[str, Any],
    page_id: str,
    image_hash: str | None,
    instagram_actor_id: str | None = None,
) -> dict[str, Any]:
    """Monta o object_story_spec a partir de campos amigáveis (legenda, imagem, link...)."""
    link_data: dict[str, Any] = {}

    if "message" in creative_cfg:
        link_data["message"] = creative_cfg["message"]
    if "caption" in creative_cfg:
        link_data["caption"] = creative_cfg["caption"]
    if "link" in creative_cfg:
        link_data["link"] = creative_cfg["link"]
    if "description" in creative_cfg:
        link_data["description"] = creative_cfg["description"]
    if "headline" in creative_cfg:
        link_data["name"] = creative_cfg["headline"]
    if image_hash:
        link_data["image_hash"] = image_hash
    if "call_to_action_type" in creative_cfg:
        cta: dict[str, Any] = {"type": creative_cfg["call_to_action_type"]}
        if "link" in creative_cfg:
            cta["value"] = {"link": creative_cfg["link"]}
        link_data["call_to_action"] = cta

    link_data.update(creative_cfg.get("link_data_settings", {}))

    object_story_spec: dict[str, Any] = {"page_id": page_id, "link_data": link_data}
    if instagram_actor_id:
        object_story_spec["instagram_actor_id"] = instagram_actor_id
    return object_story_spec


def build_creative_params(
    creative_cfg: dict[str, Any],
    page_id: str,
    image_hash: str | None,
    instagram_actor_id: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {AdCreative.Field.name: creative_cfg.get("name", "Criativo")}

    if "object_story_spec" in creative_cfg:
        # Usuário enviou o object_story_spec completo (ex.: carrossel) -> usar como está.
        params[AdCreative.Field.object_story_spec] = creative_cfg["object_story_spec"]
    else:
        params[AdCreative.Field.object_story_spec] = build_creative_object_story_spec(
            creative_cfg, page_id, image_hash, instagram_actor_id
        )

    params.update(creative_cfg.get("settings", {}))
    return params


def build_ad_params(ad_cfg: dict[str, Any], adset_id: str, creative_id: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        Ad.Field.name: ad_cfg["name"],
        Ad.Field.adset_id: adset_id,
        Ad.Field.status: ad_cfg.get("status", "PAUSED"),
        Ad.Field.creative: {"creative_id": creative_id},
    }
    if "tracking_specs" in ad_cfg:
        params[Ad.Field.tracking_specs] = ad_cfg["tracking_specs"]

    params.update(ad_cfg.get("settings", {}))
    return params


# ---------------------------------------------------------------------------
# Orquestração (efeitos colaterais: chamadas à API / criação real dos objetos)
# ---------------------------------------------------------------------------


@dataclass
class CreationResult:
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"campaigns": self.campaigns, "errors": self.errors}


class Creator:
    def __init__(
        self,
        account: AdAccount,
        dry_run: bool = False,
        continue_on_error: bool = False,
    ):
        self._account = account
        self._dry_run = dry_run
        self._continue_on_error = continue_on_error
        self._image_uploader = ImageUploader(account, dry_run=dry_run)
        self._dry_run_counter = 0

    def run(self, config: dict[str, Any]) -> CreationResult:
        defaults = config.get("defaults", {})
        result = CreationResult()

        for campaign_cfg in config.get("campaigns", []):
            try:
                result.campaigns.append(self._create_campaign_tree(campaign_cfg, defaults))
            except CreationError as exc:
                logger.error(str(exc))
                result.errors.append({"campaign": campaign_cfg.get("name"), "error": str(exc)})
                if not self._continue_on_error:
                    raise
        return result

    def _create_campaign_tree(self, campaign_cfg: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        campaign_params = build_campaign_params(campaign_cfg)
        campaign_id = self._create(
            "campanha", campaign_cfg["name"], lambda: self._account.create_campaign(params=campaign_params)
        )
        campaign_summary: dict[str, Any] = {
            "name": campaign_cfg["name"],
            "id": campaign_id,
            "ad_sets": [],
        }

        for adset_cfg in campaign_cfg.get("ad_sets", []):
            try:
                campaign_summary["ad_sets"].append(self._create_adset_tree(adset_cfg, campaign_id, defaults))
            except CreationError as exc:
                logger.error(str(exc))
                campaign_summary.setdefault("errors", []).append(str(exc))
                if not self._continue_on_error:
                    raise

        return campaign_summary

    def _create_adset_tree(
        self, adset_cfg: dict[str, Any], campaign_id: str, defaults: dict[str, Any]
    ) -> dict[str, Any]:
        adset_params = build_adset_params(adset_cfg, campaign_id)
        adset_id = self._create(
            "conjunto de anúncios", adset_cfg["name"], lambda: self._account.create_ad_set(params=adset_params)
        )
        adset_summary: dict[str, Any] = {"name": adset_cfg["name"], "id": adset_id, "ads": []}

        page_id = adset_cfg.get("page_id", defaults.get("page_id"))
        instagram_actor_id = adset_cfg.get("instagram_actor_id", defaults.get("instagram_actor_id"))

        for ad_cfg in adset_cfg.get("ads", []):
            try:
                adset_summary["ads"].append(
                    self._create_ad(ad_cfg, adset_id, page_id, instagram_actor_id)
                )
            except CreationError as exc:
                logger.error(str(exc))
                adset_summary.setdefault("errors", []).append(str(exc))
                if not self._continue_on_error:
                    raise

        return adset_summary

    def _create_ad(
        self,
        ad_cfg: dict[str, Any],
        adset_id: str,
        page_id: str | None,
        instagram_actor_id: str | None,
    ) -> dict[str, Any]:
        creative_cfg = ad_cfg.get("creative", {})
        if not page_id and "object_story_spec" not in creative_cfg:
            raise CreationError(
                f"Anúncio '{ad_cfg.get('name')}' sem page_id definido "
                "(defina em defaults.page_id, no ad_set ou dentro de creative.object_story_spec)."
            )

        image_hash = self._image_uploader.resolve_image_hash(
            creative_cfg.get("image_path") or creative_cfg.get("image_url"),
            existing_hash=creative_cfg.get("image_hash"),
        )

        creative_params = build_creative_params(creative_cfg, page_id or "", image_hash, instagram_actor_id)
        creative_id = self._create(
            "criativo", creative_cfg.get("name", ad_cfg["name"]),
            lambda: self._account.create_ad_creative(params=creative_params),
        )

        ad_params = build_ad_params(ad_cfg, adset_id, creative_id)
        ad_id = self._create("anúncio", ad_cfg["name"], lambda: self._account.create_ad(params=ad_params))

        return {"name": ad_cfg["name"], "id": ad_id, "creative_id": creative_id}

    def _create(self, kind: str, name: str, action) -> str:
        if self._dry_run:
            self._dry_run_counter += 1
            fake_id = f"DRY_RUN_{kind.upper().replace(' ', '_')}_{self._dry_run_counter}"
            logger.info("[dry-run] criaria %s '%s' -> %s", kind, name, fake_id)
            return fake_id
        try:
            obj = action()
            obj_id = obj["id"] if isinstance(obj, dict) or hasattr(obj, "__getitem__") else obj.get_id()
            logger.info("%s '%s' criado(a): %s", kind.capitalize(), name, obj_id)
            return obj_id
        except FacebookRequestError as exc:
            raise CreationError(
                f"Falha ao criar {kind} '{name}': {exc.api_error_message()} "
                f"(código {exc.api_error_code()}, subcódigo {exc.api_error_subcode()})"
            ) from exc
