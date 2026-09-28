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

from .clients import BootstrapConfig, ClientConfig
from .graph import GraphClient, GraphError
from .normalize import normalizar

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS_SINGLE = ["city", "url", "image", "primary_text", "headline", "description"]
# Carrossel de 2 cartões (ex.: Simone + Rui) — mesmo link nos dois, cada um
# com a própria imagem.
REQUIRED_COLUMNS_CAROUSEL = ["city", "url", "image_card1", "image_card2", "primary_text", "headline", "description"]

MODEL_ADSET_FIELDS = (
    "id,name,optimization_goal,billing_event,bid_strategy,"
    "daily_budget,destination_type,promoted_object,targeting"
)


def _buscar_cidade(
    graph: GraphClient,
    nome_cidade: str,
    location_types: list[str] | None = None,
    preferred_region: str | None = None,
) -> dict[str, Any] | None:
    """Busca de geolocalização por cidade/bairro — usada tanto pra clonar adsets
    (CitySync) quanto pra montar o primeiro adset do zero (CampaignBootstrapper).

    location_types padrão inclui "neighborhood" além de "city" porque em
    algumas capitais (ex.: Brasília/DF) a cidade é uma só e as regiões
    administrativas (Plano Piloto, Taguatinga, Ceilândia...) são cadastradas
    pela Meta como bairro, não como cidade própria.

    preferred_region: quando dado (ex.: "Federal District"), SÓ aceita um
    resultado cuja região bata — existem cidades homônimas em estados
    diferentes (ex.: "Sobradinho" existe no DF e na Bahia; sem esse filtro
    um "único resultado exato" podia vir do estado errado)."""
    tipos = location_types or ["city", "neighborhood"]
    nome_busca = str(nome_cidade).replace(" - Capital", "").strip()
    data = graph.get(
        "search",
        {
            "type": "adgeolocation",
            "location_types": json.dumps(tipos),
            "q": nome_busca,
            "country_code": "BR",
            "limit": 50,
        },
    )
    resultados = data.get("data", [])
    if not resultados:
        logger.debug("Busca geo '%s' (%s): 0 resultado(s).", nome_busca, tipos)
        return None

    alvo = normalizar(nome_busca)
    candidatos_br = [local for local in resultados if str(local.get("country_code", "")).upper() == "BR"]

    if preferred_region:
        alvo_regiao = normalizar(preferred_region)
        for local in candidatos_br:
            if normalizar(local.get("name", "")) == alvo and alvo_regiao in normalizar(local.get("region", "")):
                return local
    else:
        # Comportamento original (Bebetto/SP): prioriza resultado em São Paulo
        # quando o nome bate, antes de cair no "único resultado exato".
        for local in candidatos_br:
            regiao = normalizar(local.get("region", ""))
            if normalizar(local.get("name", "")) == alvo and ("sao paulo" in regiao or regiao == "sp"):
                return local

    exatos = [local for local in candidatos_br if normalizar(local.get("name", "")) == alvo]
    if len(exatos) == 1:
        unico = exatos[0]
        if preferred_region and normalizar(preferred_region) not in normalizar(unico.get("region", "")):
            logger.debug(
                "Busca geo '%s': único resultado exato é de outra região ('%s', esperava '%s') — rejeitando.",
                nome_busca, unico.get("region"), preferred_region,
            )
            return None
        return unico

    logger.debug(
        "Busca geo '%s' (%s): %d candidato(s) BR, %d exato(s) — ambíguo ou não encontrado: %s",
        nome_busca, tipos, len(candidatos_br), len(exatos), [c.get("name") for c in candidatos_br],
    )
    return None


def _buscar_estado(graph: GraphClient, nome_estado: str) -> dict[str, Any] | None:
    data = graph.get(
        "search",
        {
            "type": "adgeolocation",
            "location_types": json.dumps(["region"]),
            "q": nome_estado,
            "country_code": "BR",
            "limit": 20,
        },
    )
    resultados = data.get("data", [])
    alvo = normalizar(nome_estado)
    encontrado = next(
        (
            local for local in resultados
            if normalizar(local.get("name", "")) == alvo and str(local.get("country_code", "")).upper() == "BR"
        ),
        None,
    )
    if not encontrado:
        logger.debug(
            "Busca estado '%s': %d resultado(s), nenhum exato — nomes retornados: %s",
            nome_estado, len(resultados), [r.get("name") for r in resultados],
        )
    return encontrado


@dataclass
class SyncResult:
    adsets_criados: list[dict[str, Any]] = field(default_factory=list)
    adsets_fallback_estado: list[dict[str, Any]] = field(default_factory=list)
    ads_criados: list[dict[str, Any]] = field(default_factory=list)
    ja_prontos: list[str] = field(default_factory=list)
    cidades_nao_encontradas: list[str] = field(default_factory=list)
    erros: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "adsets_criados": self.adsets_criados,
            "adsets_fallback_estado": self.adsets_fallback_estado,
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
        adset_suffix: str = "",
    ):
        self.graph = graph
        self.client = client
        self.ad_account_id = ad_account_id
        self.dry_run = dry_run
        # Permite criar um SEGUNDO adset pra uma cidade que já tem um (ex.:
        # aumento de investimento) sem colidir com o nome existente — o
        # sufixo entra só no nome do adset/anúncio, nunca na busca de
        # geolocalização (que precisa do nome real da cidade).
        self.adset_suffix = adset_suffix
        self._image_cache_path = Path(state_dir) / f"{client.name}_image_hashes.json"
        self._image_cache = self._load_image_cache()
        self._dry_run_counter = 0
        self._state_geo_cache: dict[str, dict[str, Any] | None] = {}

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    def load_spreadsheet(self) -> pd.DataFrame:
        df = pd.read_excel(self.client.excel_file)

        required = REQUIRED_COLUMNS_CAROUSEL if self.client.creative_type == "carousel" else REQUIRED_COLUMNS_SINGLE
        missing = [c for c in required if c not in df.columns]
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
        # Sem geo_location_types/preferred_region configurado no cliente:
        # comportamento original (só "city", heurística SP) — igual sempre
        # foi, pra não arriscar regressão nas cidades já em produção (Bebetto).
        return _buscar_cidade(
            self.graph, nome_cidade,
            location_types=self.client.geo_location_types or ["city"],
            preferred_region=self.client.preferred_region,
        )

    def search_state(self, nome_estado: str) -> dict[str, Any] | None:
        """Busca a geolocalização de um estado (region) — usado como fallback
        quando uma cidade não é encontrada com segurança."""
        if nome_estado in self._state_geo_cache:
            return self._state_geo_cache[nome_estado]

        encontrado = _buscar_estado(self.graph, nome_estado)
        self._state_geo_cache[nome_estado] = encontrado
        return encontrado

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

    def _geo_locations_for_city(self, modelo: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
        cidades_modelo = modelo.get("targeting", {}).get("geo_locations", {}).get("cities", [])
        raio = cidades_modelo[0].get("radius") if cidades_modelo else 40
        return {"cities": [{"key": str(local.get("key")), "radius": raio, "distance_unit": "kilometer"}]}

    def _geo_locations_for_state(self, estado_local: dict[str, Any]) -> dict[str, Any]:
        return {"regions": [{"key": str(estado_local.get("key"))}]}

    def _clone_adset_params(self, modelo: dict[str, Any], cidade: str, geo_locations: dict[str, Any]) -> dict[str, Any]:
        targeting = copy.deepcopy(modelo["targeting"])
        # Substitui geo_locations inteiramente (não mescla) — nunca deixar
        # cidade+região do modelo vazando junto com a localização nova.
        targeting["geo_locations"] = geo_locations

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

    def _create_creative_carousel(
        self, cidade: str, row: pd.Series, hash_card1: str, hash_card2: str,
    ) -> dict[str, Any]:
        """Carrossel de 2 cartões (ex.: Simone + Rui) — mesmo link/texto
        principal, cada cartão com sua própria imagem."""
        url = str(row["url"]).strip()
        headline = str(row["headline"]).strip()
        description = str(row["description"]).strip()

        def _attachment(image_hash: str) -> dict[str, Any]:
            return {
                "link": url,
                "image_hash": image_hash,
                "name": headline,
                "description": description,
                "call_to_action": {"type": "LEARN_MORE", "value": {"link": url}},
            }

        object_story_spec: dict[str, Any] = {
            "page_id": self.client.page_id,
            "link_data": {
                "link": url,
                "message": str(row["primary_text"]).strip(),
                "child_attachments": [_attachment(hash_card1), _attachment(hash_card2)],
                "multi_share_end_card": False,
            },
        }
        if self.client.instagram_actor_id:
            object_story_spec["instagram_actor_id"] = self.client.instagram_actor_id

        params: dict[str, Any] = {
            "name": f"{cidade} Creative (Carrossel)",
            "object_story_spec": json.dumps(object_story_spec, ensure_ascii=False),
        }
        if self.client.authorization_category:
            params["authorization_category"] = self.client.authorization_category

        if self.dry_run:
            self._dry_run_counter += 1
            return {"id": f"DRY_RUN_CREATIVE_{self._dry_run_counter}"}
        return self.graph.post(f"{self.ad_account_id}/adcreatives", params)

    def _build_creative(self, cidade: str, row: pd.Series) -> tuple[dict[str, Any] | None, str | None]:
        """Resolve a(s) imagem(ns) da linha e cria o criativo certo pro
        creative_type do cliente. Retorna (criativo, None) ou (None, erro)."""
        if self.client.creative_type == "carousel":
            hash_card1 = self.resolve_image_hash(str(row["image_card1"]).strip())
            hash_card2 = self.resolve_image_hash(str(row["image_card2"]).strip())
            if not hash_card1 or not hash_card2:
                return None, "imagem/hash ausente (carrossel: image_card1/image_card2)"
            return self._create_creative_carousel(cidade, row, hash_card1, hash_card2), None

        image_hash = self.resolve_image_hash(str(row["image"]).strip())
        if not image_hash:
            return None, "imagem/hash ausente"
        return self._create_creative(cidade, row, image_hash), None

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
            nome_adset = f"{cidade}{self.adset_suffix}"
            chave = normalizar(nome_adset)

            try:
                adset = existing_adsets.get(chave)

                if adset is None:
                    local = self.search_city(cidade)
                    usou_fallback_estado = False

                    if local and local.get("key"):
                        geo_locations = self._geo_locations_for_city(modelo, local)
                    elif self.client.fallback_state:
                        estado_local = self.search_state(self.client.fallback_state)
                        if not estado_local or not estado_local.get("key"):
                            logger.warning(
                                "Cidade '%s' não encontrada E o estado de fallback '%s' também não "
                                "resolveu — pulando.", cidade, self.client.fallback_state,
                            )
                            result.cidades_nao_encontradas.append(cidade)
                            continue
                        geo_locations = self._geo_locations_for_state(estado_local)
                        usou_fallback_estado = True
                        logger.warning(
                            "Cidade não encontrada com segurança: %s — criando com segmentação "
                            "por estado (%s) em vez de raio de cidade.", cidade, self.client.fallback_state,
                        )
                    else:
                        logger.warning("Cidade não encontrada com segurança: %s", cidade)
                        result.cidades_nao_encontradas.append(cidade)
                        continue

                    params = self._clone_adset_params(modelo, nome_adset, geo_locations)
                    criado = self._create_adset(params)
                    adset = {"id": criado["id"], "name": nome_adset, "status": "PAUSED"}
                    existing_adsets[chave] = adset
                    ads_by_adset.setdefault(str(adset["id"]), [])
                    result.adsets_criados.append({"city": nome_adset, "adset_id": adset["id"]})
                    if usou_fallback_estado:
                        result.adsets_fallback_estado.append({"city": nome_adset, "adset_id": adset["id"]})
                    logger.info("Adset criado: %s -> %s", nome_adset, adset["id"])
                    time.sleep(1)

                if ads_by_adset.get(str(adset["id"])):
                    result.ja_prontos.append(nome_adset)
                    continue

                creative, erro_imagem = self._build_creative(nome_adset, row)
                if erro_imagem:
                    result.erros.append({"city": nome_adset, "step": "image", "error": erro_imagem})
                    continue

                ad = self._create_ad(adset["id"], creative["id"])

                ads_by_adset.setdefault(str(adset["id"]), []).append(ad)
                result.ads_criados.append(
                    {"city": nome_adset, "adset_id": adset["id"], "creative_id": creative["id"], "ad_id": ad["id"]}
                )
                logger.info("Anúncio criado: %s -> %s", nome_adset, ad["id"])
                time.sleep(0.35)

            except GraphError as exc:
                logger.error("Erro na cidade %s: %s", nome_adset, exc)
                logger.error("Detalhe bruto da API: %s", json.dumps(exc.error, ensure_ascii=False))
                result.erros.append({"city": nome_adset, "error": str(exc), "raw": exc.error})

        return result

    def update_creatives(self) -> SyncResult:
        """Recria o criativo de cada cidade da planilha com os dados ATUAIS da
        linha (ex.: link corrigido, texto revisado) e troca a referência do
        anúncio já existente pro criativo novo — sem mexer em adset, imagem
        de progresso ou status. Não cria adset/anúncio novo: cidade sem
        anúncio ainda é só reportada (rode 'sync' primeiro pra ela)."""
        result = SyncResult()

        df = self.load_spreadsheet()
        existing_adsets = self.list_existing_adsets()
        ads_by_adset = self.list_ads_by_adset()

        for _, row in df.iterrows():
            cidade = str(row["city"]).strip()
            nome_adset = f"{cidade}{self.adset_suffix}"
            chave = normalizar(nome_adset)

            adset = existing_adsets.get(chave)
            if adset is None:
                result.cidades_nao_encontradas.append(nome_adset)
                continue

            ads = ads_by_adset.get(str(adset["id"]), [])
            if not ads:
                result.cidades_nao_encontradas.append(nome_adset)
                continue

            try:
                creative, erro_imagem = self._build_creative(nome_adset, row)
                if erro_imagem:
                    result.erros.append({"city": nome_adset, "step": "image", "error": erro_imagem})
                    continue

                ad = ads[0]
                if not self.dry_run:
                    self.graph.post(str(ad["id"]), {"creative": json.dumps({"creative_id": creative["id"]})})
                result.ads_criados.append(
                    {
                        "city": nome_adset,
                        "ad_id": ad["id"],
                        "creative_id": creative["id"],
                        "acao": "criativo_atualizado",
                    }
                )
                logger.info(
                    "Criativo atualizado: %s -> anúncio %s agora usa criativo %s",
                    nome_adset, ad["id"], creative["id"],
                )
                time.sleep(0.35)

            except GraphError as exc:
                logger.error("Erro atualizando criativo de %s: %s", nome_adset, exc)
                logger.error("Detalhe bruto da API: %s", json.dumps(exc.error, ensure_ascii=False))
                result.erros.append({"city": nome_adset, "error": str(exc), "raw": exc.error})

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
            nome_adset = f"{cidade}{self.adset_suffix}"
            chave = normalizar(nome_adset)

            adset = existing_adsets.get(chave)
            if adset is None:
                result.erros.append({"city": nome_adset, "step": "adset", "error": "adset não existe ainda"})
                continue

            ads = ads_by_adset.get(str(adset["id"]), [])
            if not ads:
                result.erros.append({"city": nome_adset, "step": "ad", "error": "anúncio não existe ainda"})
                continue

            try:
                if str(adset.get("status", "")).upper() != "ACTIVE":
                    if not self.dry_run:
                        self.graph.post(str(adset["id"]), {"status": "ACTIVE"})
                    adset["status"] = "ACTIVE"
                    logger.info("Adset ativado: %s -> %s", nome_adset, adset["id"])
                    time.sleep(1)

                ad = ads[0]
                if str(ad.get("status", "")).upper() != "ACTIVE":
                    if not self.dry_run:
                        self.graph.post(str(ad["id"]), {"status": "ACTIVE"})
                    ad["status"] = "ACTIVE"
                    result.ads_criados.append({"city": nome_adset, "ad_id": ad["id"], "acao": "ativado"})
                    logger.info("Anúncio ativado: %s -> %s", nome_adset, ad["id"])
                    time.sleep(1)
                else:
                    result.ja_prontos.append(nome_adset)
                    logger.info("Já estava ativo: %s", nome_adset)

            except GraphError as exc:
                logger.error("Erro ativando %s: %s", nome_adset, exc)
                logger.error("Detalhe bruto da API: %s", json.dumps(exc.error, ensure_ascii=False))
                result.erros.append({"city": nome_adset, "error": str(exc), "raw": exc.error})

        return result


class CampaignBootstrapper:
    """Cria do zero: campanha (CBO) -> 1º adset -> imagem -> criativo -> anúncio.

    Só existe pra dar o "start" numa conta nova, sem campanha ainda. Depois de
    rodar, o campaign_id/model_adset_id resultantes viram um clients/<nome>.json
    normal, e o CitySync (sync/activate) assume dali em diante — igual a
    qualquer outro cliente que já tinha campanha pronta.
    """

    def __init__(self, graph: GraphClient, ad_account_id: str, dry_run: bool = False):
        self.graph = graph
        self.ad_account_id = ad_account_id
        self.dry_run = dry_run

    def _create_campaign(self, cfg: BootstrapConfig) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": cfg.campaign_name,
            "objective": cfg.objective,
            "status": "PAUSED",
            # CBO: orçamento na campanha, não no adset.
            "daily_budget": cfg.daily_budget_cents,
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        }
        if cfg.special_ad_categories:
            params["special_ad_categories"] = json.dumps(cfg.special_ad_categories)

        if self.dry_run:
            return {"id": "DRY_RUN_CAMPAIGN_1"}
        return self.graph.post(f"{self.ad_account_id}/campaigns", params)

    def _geo_locations(self, cfg: BootstrapConfig) -> dict[str, Any]:
        # "city" e "neighborhood": em capitais como Brasília a cidade é uma só
        # (Brasília) e as regiões administrativas (Plano Piloto, Taguatinga...)
        # são cadastradas como bairro, não como cidade própria.
        local = _buscar_cidade(
            self.graph, cfg.region, location_types=["city", "neighborhood"],
            preferred_region=cfg.preferred_region,
        )
        if local and local.get("key"):
            if local.get("type") == "neighborhood":
                # Bairro não aceita raio/distance_unit — é sempre a área exata.
                return {"neighborhoods": [{"key": str(local["key"])}]}
            return {"cities": [{"key": str(local["key"]), "radius": cfg.radius_km, "distance_unit": "kilometer"}]}

        if cfg.fallback_state:
            estado = _buscar_estado(self.graph, cfg.fallback_state)
            if estado and estado.get("key"):
                logger.warning(
                    "Região '%s' não encontrada com segurança — usando o estado '%s' inteiro no targeting inicial.",
                    cfg.region, cfg.fallback_state,
                )
                return {"regions": [{"key": str(estado["key"])}]}
            raise ValueError(
                f"Não consegui localizar '{cfg.region}' NEM o estado de fallback "
                f"'{cfg.fallback_state}' na busca de geolocalização."
            )

        raise ValueError(
            f"Não consegui localizar '{cfg.region}' na busca de geolocalização "
            "e não há fallback_state configurado no bootstrap.json pra usar o estado inteiro."
        )

    def _create_adset(self, cfg: BootstrapConfig, campaign_id: str, geo_locations: dict[str, Any]) -> dict[str, Any]:
        targeting = {
            "age_min": cfg.age_min,
            "age_max": cfg.age_max,
            "genders": cfg.genders,
            "geo_locations": geo_locations,
        }
        params: dict[str, Any] = {
            "name": cfg.region,
            "campaign_id": campaign_id,
            "status": "PAUSED",
            "optimization_goal": cfg.optimization_goal,
            "billing_event": cfg.billing_event,
            "targeting": json.dumps(targeting),
        }
        if self.dry_run:
            return {"id": "DRY_RUN_ADSET_1"}
        return self.graph.post(f"{self.ad_account_id}/adsets", params)

    def _upload_image(self, cfg: BootstrapConfig, image_name: str) -> str:
        path = os.path.join(cfg.images_folder, image_name)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Imagem não encontrada: {path}")

        if self.dry_run:
            return f"DRY_RUN_HASH::{image_name}"

        resp = self.graph.post_image(f"{self.ad_account_id}/adimages", path)
        imagens = resp.get("images") or {}
        if not imagens:
            raise ValueError("Upload de imagem não retornou hash.")
        return list(imagens.values())[0]["hash"]

    def _create_creative(self, cfg: BootstrapConfig, image_hash: str) -> dict[str, Any]:
        object_story_spec: dict[str, Any] = {
            "page_id": cfg.page_id,
            "link_data": {
                "link": cfg.url,
                "message": cfg.primary_text,
                "name": cfg.headline,
                "description": cfg.description,
                "image_hash": image_hash,
                "call_to_action": {"type": "LEARN_MORE", "value": {"link": cfg.url}},
            },
        }
        if cfg.instagram_actor_id:
            object_story_spec["instagram_actor_id"] = cfg.instagram_actor_id

        params: dict[str, Any] = {
            "name": f"{cfg.region} Creative",
            "object_story_spec": json.dumps(object_story_spec, ensure_ascii=False),
        }
        if cfg.authorization_category:
            params["authorization_category"] = cfg.authorization_category

        if self.dry_run:
            return {"id": "DRY_RUN_CREATIVE_1"}
        return self.graph.post(f"{self.ad_account_id}/adcreatives", params)

    def _create_creative_carousel(self, cfg: BootstrapConfig, hash_card1: str, hash_card2: str) -> dict[str, Any]:
        def _attachment(image_hash: str) -> dict[str, Any]:
            return {
                "link": cfg.url,
                "image_hash": image_hash,
                "name": cfg.headline,
                "description": cfg.description,
                "call_to_action": {"type": "LEARN_MORE", "value": {"link": cfg.url}},
            }

        object_story_spec: dict[str, Any] = {
            "page_id": cfg.page_id,
            "link_data": {
                "link": cfg.url,
                "message": cfg.primary_text,
                "child_attachments": [_attachment(hash_card1), _attachment(hash_card2)],
                "multi_share_end_card": False,
            },
        }
        if cfg.instagram_actor_id:
            object_story_spec["instagram_actor_id"] = cfg.instagram_actor_id

        params: dict[str, Any] = {
            "name": f"{cfg.region} Creative (Carrossel)",
            "object_story_spec": json.dumps(object_story_spec, ensure_ascii=False),
        }
        if cfg.authorization_category:
            params["authorization_category"] = cfg.authorization_category

        if self.dry_run:
            return {"id": "DRY_RUN_CREATIVE_1"}
        return self.graph.post(f"{self.ad_account_id}/adcreatives", params)

    def _create_ad(self, cfg: BootstrapConfig, adset_id: str, creative_id: str) -> dict[str, Any]:
        params = {
            "name": cfg.ad_name,
            "adset_id": adset_id,
            "creative": json.dumps({"creative_id": creative_id}),
            "status": "PAUSED",
        }
        if self.dry_run:
            return {"id": "DRY_RUN_AD_1"}
        return self.graph.post(f"{self.ad_account_id}/ads", params)

    def bootstrap(self, cfg: BootstrapConfig) -> dict[str, Any]:
        campaign = self._create_campaign(cfg)
        logger.info("Campanha criada: %s -> %s", cfg.campaign_name, campaign["id"])

        geo_locations = self._geo_locations(cfg)
        adset = self._create_adset(cfg, campaign["id"], geo_locations)
        logger.info("Adset-modelo criado: %s -> %s", cfg.region, adset["id"])

        if cfg.creative_type == "carousel":
            hash_card1 = self._upload_image(cfg, cfg.image_card1)
            hash_card2 = self._upload_image(cfg, cfg.image_card2)
            creative = self._create_creative_carousel(cfg, hash_card1, hash_card2)
        else:
            image_hash = self._upload_image(cfg, cfg.image)
            creative = self._create_creative(cfg, image_hash)
        ad = self._create_ad(cfg, adset["id"], creative["id"])
        logger.info("Anúncio-modelo criado: %s -> %s", cfg.region, ad["id"])

        return {
            "campaign_id": campaign["id"],
            "model_adset_id": adset["id"],
            "creative_id": creative["id"],
            "ad_id": ad["id"],
        }
