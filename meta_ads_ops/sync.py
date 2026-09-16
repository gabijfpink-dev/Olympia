"""Garante, para cada cidade da planilha do cliente:

    adset (clonado do modelo) -> imagem com hash -> criativo -> anúncio

O que já existe é decidido consultando a API ao vivo (lista de adsets da
campanha + anúncios de cada adset) — não depende de planilhas de progresso
locais desatualizáveis. O único cache local é o hash de imagem já enviada
(puramente uma otimização; se ele sumir, o pior caso é reenviar a imagem,
o que a Meta deduplica pelo conteúdo do arquivo).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .clients import ClientConfig
from .graph import GraphClient, GraphError
from .normalize import normalizar

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["city", "url", "image", "primary_text", "headline", "description"]

MODEL_ADSET_FIELDS = (
    "id,name,optimization_goal,billing_event,bid_strategy,"
    "daily_budget,destination_type,promoted_object,targeting"
)


@dataclass
class SyncResult:
    adsets_criados: list[dict[str, Any]] = field(default_factory=list)
    ads_criados: list[dict[str, Any]] = field(default_factory=list)
    ja_prontos: list[str] = field(default_factory=list)
    cidades_nao_encontradas: list[str] = field(default_factory=list)
    erros: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "adsets_criados": self.adsets_criados,
            "ads_criados": self.ads_criados,
            "ja_prontos": len(self.ja_prontos),
            "cidades_nao_encontradas": self.cidades_nao_encontradas,
            "erros": self.erros,
        }


class CitySync:
    def __init__(
        self,
        graph: GraphClient,
        client: ClientConfig,
        ad_account_id: str,
        dry_run: bool = False,
        state_dir: str = ".state",
    ):
        self.graph = graph
        self.client = client
        self.ad_account_id = ad_account_id
        self.dry_run = dry_run
        self._image_cache_path = Path(state_dir) / f"{client.name}_image_hashes.json"
        self._image_cache = self._load_image_cache()
        self._dry_run_counter = 0

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    def load_spreadsheet(self) -> pd.DataFrame:
        df = pd.read_excel(self.client.excel_file)

        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"Planilha '{self.client.excel_file}' sem colunas obrigatórias: {missing}")

        df = df.dropna(subset=["city"]).copy()
        df["city"] = df["city"].astype(str).str.strip()
        return df

    def get_model_adset(self) -> dict[str, Any]:
        modelo = self.graph.get(self.client.model_adset_id, {"fields": MODEL_ADSET_FIELDS})
        if not modelo.get("targeting"):
            raise ValueError(f"Adset-modelo {self.client.model_adset_id} não tem targeting.")
        return modelo

    def list_existing_adsets(self) -> dict[str, dict[str, Any]]:
        adsets = self.graph.paginate(f"{self.client.campaign_id}/adsets", {"fields": "id,name,status"})
        return {normalizar(a["name"]): a for a in adsets}

    def list_ads_by_adset(self) -> dict[str, list[dict[str, Any]]]:
        ads = self.graph.paginate(f"{self.client.campaign_id}/ads", {"fields": "id,name,status,adset_id"})
        por_adset: dict[str, list[dict[str, Any]]] = {}
        for ad in ads:
            por_adset.setdefault(str(ad["adset_id"]), []).append(ad)
        return por_adset

    def search_city(self, nome_cidade: str) -> dict[str, Any] | None:
        nome_busca = str(nome_cidade).replace(" - Capital", "").strip()
        data = self.graph.get(
            "search",
            {
                "type": "adgeolocation",
                "location_types": json.dumps(["city"]),
                "q": nome_busca,
                "country_code": "BR",
                "limit": 50,
            },
        )
        resultados = data.get("data", [])
        if not resultados:
            return None

        alvo = normalizar(nome_busca)
        candidatos_br = [
            local for local in resultados if str(local.get("country_code", "")).upper() == "BR"
        ]

        for local in candidatos_br:
            regiao = normalizar(local.get("region", ""))
            if normalizar(local.get("name", "")) == alvo and (
                "sao paulo" in regiao or regiao == "sp"
            ):
                return local

        exatos = [local for local in candidatos_br if normalizar(local.get("name", "")) == alvo]
        if len(exatos) == 1:
            return exatos[0]

        return None

    # ------------------------------------------------------------------
    # Imagem
    # ------------------------------------------------------------------

    def _load_image_cache(self) -> dict[str, str]:
        if self._image_cache_path.is_file():
            try:
                cache = json.loads(self._image_cache_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                logger.warning("Cache de hash de imagem corrompido, ignorando: %s", self._image_cache_path)
                return {}

            # Autocorreção: versões antigas podiam gravar hashes falsos de
            # dry-run nesse arquivo; nunca reaproveitar um valor desses.
            limpo = {k: v for k, v in cache.items() if not str(v).startswith("DRY_RUN_HASH::")}
            if len(limpo) != len(cache):
                logger.warning(
                    "Removidos %d hash(es) falso(s) de dry-run do cache %s",
                    len(cache) - len(limpo), self._image_cache_path,
                )
            return limpo
        return {}

    def _save_image_cache(self) -> None:
        self._image_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._image_cache_path.write_text(
            json.dumps(self._image_cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def resolve_image_hash(self, image_name: str) -> str | None:
        if image_name in self._image_cache:
            return self._image_cache[image_name]

        path = os.path.join(self.client.images_folder, image_name)
        if not os.path.isfile(path):
            return None

        if self.dry_run:
            # Nunca persiste no cache real: um hash falso de dry-run não pode
            # sobreviver para "enganar" uma rodada de verdade depois.
            return f"DRY_RUN_HASH::{image_name}"

        resp = self.graph.post_image(f"{self.ad_account_id}/adimages", path)
        imagens = resp.get("images") or {}
        if not imagens:
            return None
        image_hash = list(imagens.values())[0]["hash"]

        self._image_cache[image_name] = image_hash
        self._save_image_cache()
        return image_hash

    # ------------------------------------------------------------------
    # Criação
    # ------------------------------------------------------------------

    def _clone_adset_params(self, modelo: dict[str, Any], cidade: str, local: dict[str, Any]) -> dict[str, Any]:
        targeting = copy.deepcopy(modelo["targeting"])
        targeting.setdefault("geo_locations", {})

        cidades_modelo = modelo.get("targeting", {}).get("geo_locations", {}).get("cities", [])
        raio = cidades_modelo[0].get("radius") if cidades_modelo else 40

        targeting["geo_locations"]["cities"] = [
            {"key": str(local.get("key")), "radius": raio, "distance_unit": "kilometer"}
        ]
        for chave in ("regions", "countries", "country_groups"):
            targeting["geo_locations"].pop(chave, None)

        params: dict[str, Any] = {
            "name": cidade,
            "campaign_id": self.client.campaign_id,
            "status": "PAUSED",
            "optimization_goal": modelo.get("optimization_goal"),
            "billing_event": modelo.get("billing_event"),
            "targeting": json.dumps(targeting),
        }
        if modelo.get("bid_strategy"):
            params["bid_strategy"] = modelo["bid_strategy"]
        if modelo.get("daily_budget"):
            params["daily_budget"] = modelo["daily_budget"]
        if modelo.get("destination_type"):
            params["destination_type"] = modelo["destination_type"]
        if modelo.get("promoted_object"):
            params["promoted_object"] = json.dumps(modelo["promoted_object"])

        return {k: v for k, v in params.items() if v is not None}

    def _create_adset(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.dry_run:
            self._dry_run_counter += 1
            return {"id": f"DRY_RUN_ADSET_{self._dry_run_counter}"}
        return self.graph.post(f"{self.ad_account_id}/adsets", params)

    def _create_creative(self, cidade: str, row: pd.Series, image_hash: str) -> dict[str, Any]:
        object_story_spec: dict[str, Any] = {
            "page_id": self.client.page_id,
            "link_data": {
                "link": str(row["url"]).strip(),
                "message": str(row["primary_text"]).strip(),
                "name": str(row["headline"]).strip(),
                "description": str(row["description"]).strip(),
                "image_hash": image_hash,
                "call_to_action": {"type": "LEARN_MORE", "value": {"link": str(row["url"]).strip()}},
            },
        }
        if self.client.instagram_actor_id:
            object_story_spec["instagram_actor_id"] = self.client.instagram_actor_id

        params: dict[str, Any] = {
            "name": f"{cidade} Creative",
            "object_story_spec": json.dumps(object_story_spec, ensure_ascii=False),
        }
        if self.client.authorization_category:
            params["authorization_category"] = self.client.authorization_category

        if self.dry_run:
            self._dry_run_counter += 1
            return {"id": f"DRY_RUN_CREATIVE_{self._dry_run_counter}"}
        return self.graph.post(f"{self.ad_account_id}/adcreatives", params)

    def _create_ad(self, adset_id: str, creative_id: str, status: str = "PAUSED") -> dict[str, Any]:
        params = {
            "name": self.client.ad_name,
            "adset_id": adset_id,
            "creative": json.dumps({"creative_id": creative_id}),
            "status": status,
        }
        if self.dry_run:
            self._dry_run_counter += 1
            return {"id": f"DRY_RUN_AD_{self._dry_run_counter}"}
        return self.graph.post(f"{self.ad_account_id}/ads", params)

    # ------------------------------------------------------------------
    # Orquestração
    # ------------------------------------------------------------------

    def sync(self) -> SyncResult:
        result = SyncResult()

        df = self.load_spreadsheet()
        modelo = self.get_model_adset()
        existing_adsets = self.list_existing_adsets()
        ads_by_adset = self.list_ads_by_adset()

        for _, row in df.iterrows():
            cidade = str(row["city"]).strip()
            chave = normalizar(cidade)

            try:
                adset = existing_adsets.get(chave)

                if adset is None:
                    local = self.search_city(cidade)
                    if not local or not local.get("key"):
                        logger.warning("Cidade não encontrada com segurança: %s", cidade)
                        result.cidades_nao_encontradas.append(cidade)
                        continue

                    params = self._clone_adset_params(modelo, cidade, local)
                    criado = self._create_adset(params)
                    adset = {"id": criado["id"], "name": cidade, "status": "PAUSED"}
                    existing_adsets[chave] = adset
                    ads_by_adset.setdefault(str(adset["id"]), [])
                    result.adsets_criados.append({"city": cidade, "adset_id": adset["id"]})
                    logger.info("Adset criado: %s -> %s", cidade, adset["id"])
                    time.sleep(1)

                if ads_by_adset.get(str(adset["id"])):
                    result.ja_prontos.append(cidade)
                    continue

                image_hash = self.resolve_image_hash(str(row["image"]).strip())
                if not image_hash:
                    result.erros.append({"city": cidade, "step": "image", "error": "imagem/hash ausente"})
                    continue

                creative = self._create_creative(cidade, row, image_hash)
                ad = self._create_ad(adset["id"], creative["id"])

                ads_by_adset.setdefault(str(adset["id"]), []).append(ad)
                result.ads_criados.append(
                    {"city": cidade, "adset_id": adset["id"], "creative_id": creative["id"], "ad_id": ad["id"]}
                )
                logger.info("Anúncio criado: %s -> %s", cidade, ad["id"])
                time.sleep(0.35)

            except GraphError as exc:
                logger.error("Erro na cidade %s: %s", cidade, exc)
                logger.error("Detalhe bruto da API: %s", json.dumps(exc.error, ensure_ascii=False))
                result.erros.append({"city": cidade, "error": str(exc), "raw": exc.error})

        return result

    def activate(self) -> SyncResult:
        """Ativa (status ACTIVE) o adset e o anúncio de cada cidade da planilha
        que ainda não estiverem ativos. Único passo que efetivamente coloca
        anúncio no ar — rode com calma."""

        result = SyncResult()

        df = self.load_spreadsheet()
        existing_adsets = self.list_existing_adsets()
        ads_by_adset = self.list_ads_by_adset()

        for _, row in df.iterrows():
            cidade = str(row["city"]).strip()
            chave = normalizar(cidade)

            adset = existing_adsets.get(chave)
            if adset is None:
                result.erros.append({"city": cidade, "step": "adset", "error": "adset não existe ainda"})
                continue

            ads = ads_by_adset.get(str(adset["id"]), [])
            if not ads:
                result.erros.append({"city": cidade, "step": "ad", "error": "anúncio não existe ainda"})
                continue

            try:
                if str(adset.get("status", "")).upper() != "ACTIVE":
                    if not self.dry_run:
                        self.graph.post(str(adset["id"]), {"status": "ACTIVE"})
                    adset["status"] = "ACTIVE"
                    time.sleep(1)

                ad = ads[0]
                if str(ad.get("status", "")).upper() != "ACTIVE":
                    if not self.dry_run:
                        self.graph.post(str(ad["id"]), {"status": "ACTIVE"})
                    ad["status"] = "ACTIVE"
                    result.ads_criados.append({"city": cidade, "ad_id": ad["id"], "acao": "ativado"})
                    time.sleep(1)
                else:
                    result.ja_prontos.append(cidade)

            except GraphError as exc:
                logger.error("Erro ativando %s: %s", cidade, exc)
                logger.error("Detalhe bruto da API: %s", json.dumps(exc.error, ensure_ascii=False))
                result.erros.append({"city": cidade, "error": str(exc), "raw": exc.error})

        return result
