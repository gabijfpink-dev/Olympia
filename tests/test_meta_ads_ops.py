import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
import requests

from meta_ads_ops.clients import BootstrapConfig, ClientConfig, load_secrets
from meta_ads_ops.graph import GraphClient, GraphError
from meta_ads_ops.normalize import normalizar
from meta_ads_ops.sync import CampaignBootstrapper, CitySync


class GraphClientRetryTests(unittest.TestCase):
    def _fake_response(self, payload):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps(payload).encode("utf-8")
        return resp

    @patch("meta_ads_ops.graph.time.sleep")
    @patch("meta_ads_ops.graph.requests.get")
    def test_retries_network_error_then_succeeds(self, mock_get, mock_sleep):
        mock_get.side_effect = [
            requests.exceptions.ConnectTimeout("timeout"),
            self._fake_response({"id": "123"}),
        ]

        client = GraphClient("token", "v21.0")
        result = client.get("123")

        self.assertEqual(result, {"id": "123"})
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("meta_ads_ops.graph.time.sleep")
    @patch("meta_ads_ops.graph.requests.get")
    def test_gives_up_after_max_attempts(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.exceptions.ConnectTimeout("timeout")

        client = GraphClient("token", "v21.0")

        with self.assertRaises(GraphError):
            client.get("123")

        self.assertEqual(mock_get.call_count, 3)


class LoadSecretsTests(unittest.TestCase):
    def test_missing_config_is_auto_created_from_example_and_raises_clear_error(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.py"
            self.assertFalse(config_path.exists())

            with self.assertRaises(FileNotFoundError) as ctx:
                load_secrets(str(config_path))

            self.assertTrue(config_path.exists(), "config.py deveria ter sido criado a partir do modelo")
            self.assertIn("preencha", str(ctx.exception).lower())

    def test_config_with_blank_fields_raises_value_error(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.py"
            config_path.write_text('ACCESS_TOKEN = ""\nAD_ACCOUNT_ID = ""\nAPI_VERSION = ""\n', encoding="utf-8")

            with self.assertRaises(ValueError):
                load_secrets(str(config_path))

    def test_config_with_real_values_loads_and_normalizes_account_id(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.py"
            config_path.write_text(
                'ACCESS_TOKEN = "abc"\nAD_ACCOUNT_ID = "123"\nAPI_VERSION = "v21.0"\n', encoding="utf-8"
            )

            secrets = load_secrets(str(config_path))

            self.assertEqual(secrets.access_token, "abc")
            self.assertEqual(secrets.ad_account_id, "act_123")
            self.assertEqual(secrets.api_version, "v21.0")


class NormalizeTests(unittest.TestCase):
    def test_removes_accents_and_lowercases(self):
        self.assertEqual(normalizar("São Bernardo do Campo"), "sao bernardo do campo")
        self.assertEqual(normalizar("  Águas de São Pedro  "), "aguas de sao pedro")

    def test_empty_and_none(self):
        self.assertEqual(normalizar(None), "")
        self.assertEqual(normalizar("   "), "")


class FakeGraph:
    """Substitui o GraphClient real: nenhuma chamada de rede é feita."""

    def __init__(self):
        self.posts = []
        self.model_adset = {
            "id": "MODEL_1",
            "optimization_goal": "LINK_CLICKS",
            "billing_event": "IMPRESSIONS",
            "targeting": {
                "geo_locations": {
                    "cities": [{"key": "999", "radius": 40, "distance_unit": "kilometer"}]
                }
            },
        }
        self.adsets = [{"id": "ADSET_EXISTENTE", "name": "Cidade Pronta", "status": "PAUSED"}]
        self.ads = [{"id": "AD_1", "name": "01", "status": "PAUSED", "adset_id": "ADSET_EXISTENTE"}]
        self.search_results = {
            "cidade nova": {"key": "111", "name": "Cidade Nova", "region": "Sao Paulo", "country_code": "BR"},
            "cidade pronta": {"key": "222", "name": "Cidade Pronta", "region": "Sao Paulo", "country_code": "BR"},
            "brasilia": {"key": "333", "name": "Brasília", "region": "Distrito Federal", "country_code": "BR"},
        }
        self.state_results = {"sao paulo": {"key": "SP_STATE_KEY", "name": "São Paulo", "country_code": "BR"}}

    def get(self, path, params=None):
        if path == "MODEL_ID":
            return self.model_adset
        if path == "search":
            q = normalizar(params["q"])
            if "region" in params.get("location_types", ""):
                local = self.state_results.get(q)
            else:
                local = self.search_results.get(q)
            return {"data": [local] if local else []}
        raise AssertionError(f"GET inesperado: {path}")

    def paginate(self, path, params=None):
        if path.endswith("/adsets"):
            return self.adsets
        if path.endswith("/ads"):
            return self.ads
        raise AssertionError(f"paginate inesperado: {path}")

    def post(self, path, data=None):
        self.posts.append((path, data))
        if path.endswith("/campaigns"):
            return {"id": "CAMPANHA_NOVA"}
        if path.endswith("/adsets"):
            self.adsets.append({"id": "ADSET_NOVO", "name": data.get("name"), "status": "PAUSED"})
            return {"id": "ADSET_NOVO"}
        if path.endswith("/adcreatives"):
            return {"id": "CREATIVE_NOVO"}
        if path.endswith("/ads"):
            self.ads.append({
                "id": "AD_NOVO", "name": data.get("name"),
                "status": data.get("status", "PAUSED"), "adset_id": data.get("adset_id"),
            })
            return {"id": "AD_NOVO"}
        # Atualização de status (usado pelo activate): reflete no adset/anúncio
        # correspondente pra uma chamada de activate() logo depois enxergar o estado novo.
        for adset in self.adsets:
            if adset["id"] == path:
                adset["status"] = data.get("status", adset["status"])
                return {"success": True}
        for ad in self.ads:
            if ad["id"] == path:
                ad["status"] = data.get("status", ad["status"])
                return {"success": True}
        return {"success": True}

    def post_image(self, path, file_path):
        return {"images": {"x": {"hash": "HASH_FAKE"}}}


from meta_ads_ops.normalize import normalizar as _normalizar  # noqa: E402


class CitySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        images_folder = Path(self.tmpdir.name) / "images"
        images_folder.mkdir()
        (images_folder / "cidade-nova.jpg").write_bytes(b"fake-image-bytes")

        excel_path = Path(self.tmpdir.name) / "planilha.xlsx"
        pd.DataFrame(
            [
                {
                    "city": "Cidade Pronta",
                    "url": "https://exemplo.com",
                    "image": "cidade-pronta.jpg",
                    "primary_text": "Texto",
                    "headline": "Título",
                    "description": "Descrição",
                },
                {
                    "city": "Cidade Nova",
                    "url": "https://exemplo.com",
                    "image": "cidade-nova.jpg",
                    "primary_text": "Texto novo",
                    "headline": "Título novo",
                    "description": "Descrição nova",
                },
            ]
        ).to_excel(excel_path, index=False)

        self.client = ClientConfig(
            name="teste",
            campaign_id="CAMPANHA_1",
            model_adset_id="MODEL_ID",
            page_id="PAGE_1",
            excel_file=str(excel_path),
            images_folder=str(images_folder),
        )
        self.graph = FakeGraph()
        self.sync = CitySync(
            self.graph, self.client, ad_account_id="act_1", dry_run=False,
            state_dir=str(Path(self.tmpdir.name) / ".state"),
        )

    def test_skips_city_that_already_has_ad(self):
        result = self.sync.sync()
        self.assertIn("Cidade Pronta", result.ja_prontos)

    def test_creates_adset_and_ad_for_new_city(self):
        result = self.sync.sync()

        self.assertEqual(len(result.adsets_criados), 1)
        self.assertEqual(result.adsets_criados[0]["city"], "Cidade Nova")

        self.assertEqual(len(result.ads_criados), 1)
        ad = result.ads_criados[0]
        self.assertEqual(ad["city"], "Cidade Nova")
        self.assertEqual(ad["adset_id"], "ADSET_NOVO")
        self.assertEqual(ad["creative_id"], "CREATIVE_NOVO")
        self.assertEqual(ad["ad_id"], "AD_NOVO")

        self.assertEqual(result.cidades_nao_encontradas, [])
        self.assertEqual(result.erros, [])

    def test_creative_uses_instagram_actor_id_field_not_user_id(self):
        client = ClientConfig(
            name="teste-ig",
            campaign_id="CAMPANHA_1",
            model_adset_id="MODEL_ID",
            page_id="PAGE_1",
            excel_file=self.client.excel_file,
            images_folder=self.client.images_folder,
            instagram_actor_id="IG_123",
        )
        sync = CitySync(
            self.graph, client, ad_account_id="act_1", dry_run=False,
            state_dir=str(Path(self.tmpdir.name) / ".state2"),
        )
        sync.sync()

        creative_calls = [data for path, data in self.graph.posts if path.endswith("/adcreatives")]
        self.assertTrue(creative_calls)
        spec = json.loads(creative_calls[-1]["object_story_spec"])
        self.assertEqual(spec["instagram_actor_id"], "IG_123")
        self.assertNotIn("instagram_user_id", json.dumps(spec))

    def test_authorization_category_only_set_when_configured(self):
        creative_calls = [data for path, data in self.graph.posts if path.endswith("/adcreatives")]
        # cliente de teste não define authorization_category (caso comercial, ex. Bebetto)
        self.sync.sync()
        creative_calls = [data for path, data in self.graph.posts if path.endswith("/adcreatives")]
        self.assertTrue(creative_calls)
        self.assertNotIn("authorization_category", creative_calls[-1])


class AdsetSuffixTests(unittest.TestCase):
    """--adset-suffix: criar um SEGUNDO adset numa cidade que já tem um
    (ex.: aumento de investimento), sem colidir com o nome existente."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        images_folder = Path(self.tmpdir.name) / "images"
        images_folder.mkdir()
        (images_folder / "cidade-pronta-v2.jpg").write_bytes(b"fake-image-bytes")

        excel_path = Path(self.tmpdir.name) / "planilha.xlsx"
        pd.DataFrame(
            [{
                "city": "Cidade Pronta",
                "url": "https://exemplo.com",
                "image": "cidade-pronta-v2.jpg",
                "primary_text": "Texto novo",
                "headline": "Título novo",
                "description": "Descrição nova",
            }]
        ).to_excel(excel_path, index=False)

        client = ClientConfig(
            name="teste-sufixo",
            campaign_id="CAMPANHA_1",
            model_adset_id="MODEL_ID",
            page_id="PAGE_1",
            excel_file=str(excel_path),
            images_folder=str(images_folder),
        )
        self.graph = FakeGraph()
        self.sync = CitySync(
            self.graph, client, ad_account_id="act_1", dry_run=False,
            state_dir=str(Path(self.tmpdir.name) / ".state"),
            adset_suffix=" - Aumento",
        )

    def test_creates_second_adset_instead_of_skipping_city_that_already_has_one(self):
        result = self.sync.sync()

        self.assertEqual(result.ja_prontos, [])
        self.assertEqual(len(result.adsets_criados), 1)
        self.assertEqual(result.adsets_criados[0]["city"], "Cidade Pronta - Aumento")
        self.assertEqual(len(result.ads_criados), 1)
        self.assertEqual(result.ads_criados[0]["city"], "Cidade Pronta - Aumento")

        adset_calls = [data for path, data in self.graph.posts if path.endswith("/adsets")]
        self.assertEqual(adset_calls[-1]["name"], "Cidade Pronta - Aumento")

    def test_geo_search_uses_plain_city_name_not_the_suffixed_one(self):
        with patch.object(self.sync, "search_city", wraps=self.sync.search_city) as spy:
            self.sync.sync()
        spy.assert_called_once_with("Cidade Pronta")

    def test_activate_matches_the_suffixed_adset_too(self):
        self.sync.sync()
        result = self.sync.activate()

        self.assertEqual(result.erros, [])
        ativados = [c["city"] for c in result.ads_criados]
        self.assertIn("Cidade Pronta - Aumento", ativados)


class CampaignBootstrapperTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.images_folder = Path(self.tmpdir.name) / "images"
        self.images_folder.mkdir()
        (self.images_folder / "brasilia.jpg").write_bytes(b"fake-image-bytes")

        self.graph = FakeGraph()
        self.cfg = BootstrapConfig(
            campaign_name="Eleição 2026 Leandro Grass Governador",
            page_id="PAGE_1",
            region="Brasília",
            url="https://exemplo.com",
            image="brasilia.jpg",
            images_folder=str(self.images_folder),
            primary_text="Texto",
            headline="Título",
            description="Descrição",
            daily_budget_cents=500000,
            instagram_actor_id="17841400340263472",
            authorization_category="POLITICAL",
            special_ad_categories=["ISSUES_ELECTIONS_POLITICS"],
        )

    def test_creates_campaign_adset_creative_and_ad_in_order(self):
        bootstrapper = CampaignBootstrapper(self.graph, ad_account_id="act_1", dry_run=False)
        result = bootstrapper.bootstrap(self.cfg)

        self.assertEqual(result["campaign_id"], "CAMPANHA_NOVA")
        self.assertEqual(result["model_adset_id"], "ADSET_NOVO")
        self.assertEqual(result["creative_id"], "CREATIVE_NOVO")
        self.assertEqual(result["ad_id"], "AD_NOVO")

        campaign_calls = [data for path, data in self.graph.posts if path.endswith("/campaigns")]
        self.assertEqual(len(campaign_calls), 1)
        self.assertEqual(campaign_calls[0]["daily_budget"], 500000)
        self.assertEqual(
            json.loads(campaign_calls[0]["special_ad_categories"]), ["ISSUES_ELECTIONS_POLITICS"],
        )

        adset_calls = [data for path, data in self.graph.posts if path.endswith("/adsets")]
        self.assertEqual(adset_calls[-1]["campaign_id"], "CAMPANHA_NOVA")
        self.assertNotIn("daily_budget", adset_calls[-1])  # CBO: orçamento fica na campanha
        targeting = json.loads(adset_calls[-1]["targeting"])
        self.assertEqual(targeting["geo_locations"], {"cities": [{"key": "333", "radius": 40, "distance_unit": "kilometer"}]})

        creative_calls = [data for path, data in self.graph.posts if path.endswith("/adcreatives")]
        spec = json.loads(creative_calls[-1]["object_story_spec"])
        self.assertEqual(spec["instagram_actor_id"], "17841400340263472")
        self.assertEqual(creative_calls[-1]["authorization_category"], "POLITICAL")

    def test_dry_run_never_calls_post_and_returns_placeholder_ids(self):
        bootstrapper = CampaignBootstrapper(self.graph, ad_account_id="act_1", dry_run=True)
        result = bootstrapper.bootstrap(self.cfg)

        self.assertEqual(self.graph.posts, [])
        self.assertEqual(result["campaign_id"], "DRY_RUN_CAMPAIGN_1")
        self.assertEqual(result["model_adset_id"], "DRY_RUN_ADSET_1")

    def test_neighborhood_match_uses_neighborhoods_key_not_cities(self):
        # Em capitais como Brasília, a região administrativa (Plano Piloto,
        # Taguatinga...) é cadastrada como bairro, não como cidade própria —
        # não pode virar {"cities": [...]} com raio, tem que ser {"neighborhoods": [...]}.
        self.graph.search_results["plano piloto"] = {
            "key": "444", "name": "Plano Piloto", "type": "neighborhood", "country_code": "BR",
        }
        cfg = BootstrapConfig(
            campaign_name="Campanha",
            page_id="PAGE_1",
            region="Plano Piloto",
            url="https://exemplo.com",
            image="brasilia.jpg",
            images_folder=str(self.images_folder),
            primary_text="Texto",
            headline="Título",
            description="Descrição",
            daily_budget_cents=500000,
        )
        bootstrapper = CampaignBootstrapper(self.graph, ad_account_id="act_1", dry_run=False)
        bootstrapper.bootstrap(cfg)

        adset_calls = [data for path, data in self.graph.posts if path.endswith("/adsets")]
        targeting = json.loads(adset_calls[-1]["targeting"])
        self.assertEqual(targeting["geo_locations"], {"neighborhoods": [{"key": "444"}]})

    def test_falls_back_to_state_when_region_not_found(self):
        cfg = BootstrapConfig(
            campaign_name="Campanha",
            page_id="PAGE_1",
            region="Região Fantasma",
            url="https://exemplo.com",
            image="brasilia.jpg",
            images_folder=str(self.images_folder),
            primary_text="Texto",
            headline="Título",
            description="Descrição",
            daily_budget_cents=500000,
            fallback_state="São Paulo",
        )
        bootstrapper = CampaignBootstrapper(self.graph, ad_account_id="act_1", dry_run=False)
        bootstrapper.bootstrap(cfg)

        adset_calls = [data for path, data in self.graph.posts if path.endswith("/adsets")]
        targeting = json.loads(adset_calls[-1]["targeting"])
        self.assertEqual(targeting["geo_locations"], {"regions": [{"key": "SP_STATE_KEY"}]})

    def test_raises_clear_error_when_region_and_fallback_both_fail(self):
        cfg = BootstrapConfig(
            campaign_name="Campanha",
            page_id="PAGE_1",
            region="Região Fantasma",
            url="https://exemplo.com",
            image="brasilia.jpg",
            images_folder=str(self.images_folder),
            primary_text="Texto",
            headline="Título",
            description="Descrição",
            daily_budget_cents=500000,
        )
        bootstrapper = CampaignBootstrapper(self.graph, ad_account_id="act_1", dry_run=False)
        with self.assertRaises(ValueError):
            bootstrapper.bootstrap(cfg)


class FallbackStateTests(unittest.TestCase):
    def _build(self, fallback_state):
        tmpdir = TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        images_folder = Path(tmpdir.name) / "images"
        images_folder.mkdir()
        (images_folder / "fantasma.jpg").write_bytes(b"x")

        excel_path = Path(tmpdir.name) / "planilha.xlsx"
        pd.DataFrame(
            [{
                "city": "Cidade Fantasma",
                "url": "https://exemplo.com",
                "image": "fantasma.jpg",
                "primary_text": "t",
                "headline": "h",
                "description": "d",
            }]
        ).to_excel(excel_path, index=False)

        client = ClientConfig(
            name="fallback",
            campaign_id="CAMPANHA_1",
            model_adset_id="MODEL_ID",
            page_id="PAGE_1",
            excel_file=str(excel_path),
            images_folder=str(images_folder),
            fallback_state=fallback_state,
        )
        graph = FakeGraph()
        sync = CitySync(graph, client, ad_account_id="act_1", dry_run=False, state_dir=str(Path(tmpdir.name) / ".state"))
        return graph, sync

    def test_city_not_found_falls_back_to_state_targeting_when_configured(self):
        graph, sync = self._build(fallback_state="São Paulo")

        result = sync.sync()

        self.assertEqual(result.cidades_nao_encontradas, [])
        self.assertEqual(len(result.adsets_fallback_estado), 1)
        self.assertEqual(result.adsets_fallback_estado[0]["city"], "Cidade Fantasma")

        adset_calls = [data for path, data in graph.posts if path.endswith("/adsets")]
        self.assertTrue(adset_calls)
        targeting = json.loads(adset_calls[-1]["targeting"])
        self.assertEqual(targeting["geo_locations"], {"regions": [{"key": "SP_STATE_KEY"}]})
        self.assertNotIn("cities", targeting["geo_locations"])

    def test_city_not_found_without_fallback_state_configured_is_reported_as_before(self):
        graph, sync = self._build(fallback_state=None)

        result = sync.sync()

        self.assertEqual(result.cidades_nao_encontradas, ["Cidade Fantasma"])
        self.assertEqual(result.adsets_fallback_estado, [])
        self.assertEqual(graph.posts, [])


class DryRunTests(unittest.TestCase):
    def test_dry_run_never_calls_post(self):
        with TemporaryDirectory() as tmp:
            images_folder = Path(tmp) / "images"
            images_folder.mkdir()
            (images_folder / "img.jpg").write_bytes(b"x")

            excel_path = Path(tmp) / "planilha.xlsx"
            pd.DataFrame(
                [{
                    "city": "Cidade Nova",
                    "url": "https://exemplo.com",
                    "image": "img.jpg",
                    "primary_text": "t",
                    "headline": "h",
                    "description": "d",
                }]
            ).to_excel(excel_path, index=False)

            client = ClientConfig(
                name="dry",
                campaign_id="CAMPANHA_1",
                model_adset_id="MODEL_ID",
                page_id="PAGE_1",
                excel_file=str(excel_path),
                images_folder=str(images_folder),
            )
            graph = FakeGraph()
            sync = CitySync(
                graph, client, ad_account_id="act_1", dry_run=True,
                state_dir=str(Path(tmp) / ".state"),
            )
            result = sync.sync()

            self.assertEqual(graph.posts, [])
            self.assertEqual(len(result.adsets_criados), 1)
            self.assertTrue(result.adsets_criados[0]["adset_id"].startswith("DRY_RUN_"))

    def test_dry_run_hash_never_poisons_cache_for_a_later_real_run(self):
        """Regressão: um --dry-run rodado antes não pode fazer uma rodada real
        depois enviar um image_hash falso pra Meta (bug real encontrado em produção)."""
        with TemporaryDirectory() as tmp:
            images_folder = Path(tmp) / "images"
            images_folder.mkdir()
            (images_folder / "img.jpg").write_bytes(b"x")

            excel_path = Path(tmp) / "planilha.xlsx"
            pd.DataFrame(
                [{
                    "city": "Cidade Pronta",
                    "url": "https://exemplo.com",
                    "image": "img.jpg",
                    "primary_text": "t",
                    "headline": "h",
                    "description": "d",
                }]
            ).to_excel(excel_path, index=False)

            client = ClientConfig(
                name="regressao",
                campaign_id="CAMPANHA_1",
                model_adset_id="MODEL_ID",
                page_id="PAGE_1",
                excel_file=str(excel_path),
                images_folder=str(images_folder),
            )
            state_dir = str(Path(tmp) / ".state")

            # Cidade Pronta já tem adset+ad no FakeGraph, então o dry-run não
            # chegaria a resolver a imagem por esse caminho — forço a
            # resolução direta, como uma rodada de dry-run teria feito antes
            # de alguém já ter anúncio pronto para outra cidade.
            dry_sync = CitySync(FakeGraph(), client, ad_account_id="act_1", dry_run=True, state_dir=state_dir)
            fake_hash = dry_sync.resolve_image_hash("img.jpg")
            self.assertTrue(fake_hash.startswith("DRY_RUN_HASH::"))

            cache_file = Path(state_dir) / "regressao_image_hashes.json"
            if cache_file.exists():
                self.assertNotIn("DRY_RUN_HASH::", cache_file.read_text(encoding="utf-8"))

            real_graph = FakeGraph()
            real_sync = CitySync(real_graph, client, ad_account_id="act_1", dry_run=False, state_dir=state_dir)
            real_hash = real_sync.resolve_image_hash("img.jpg")

            self.assertEqual(real_hash, "HASH_FAKE")  # vem do post_image real do FakeGraph, não do cache

    def test_poisoned_cache_from_before_the_fix_self_heals(self):
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / ".state"
            state_dir.mkdir()
            (state_dir / "cliente_image_hashes.json").write_text(
                json.dumps({"img.jpg": "DRY_RUN_HASH::img.jpg", "outra.jpg": "HASH_REAL_OK"}),
                encoding="utf-8",
            )

            client = ClientConfig(
                name="cliente",
                campaign_id="C",
                model_adset_id="M",
                page_id="P",
                excel_file="nao-usado.xlsx",
                images_folder=str(tmp),
            )
            sync = CitySync(FakeGraph(), client, ad_account_id="act_1", dry_run=False, state_dir=str(state_dir))

            self.assertNotIn("img.jpg", sync._image_cache)
            self.assertEqual(sync._image_cache.get("outra.jpg"), "HASH_REAL_OK")


if __name__ == "__main__":
    unittest.main()
