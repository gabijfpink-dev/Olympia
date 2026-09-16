import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from meta_ads_ops.clients import ClientConfig, load_secrets
from meta_ads_ops.normalize import normalizar
from meta_ads_ops.sync import CitySync


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
        self.search_results = {"cidade nova": {"key": "111", "name": "Cidade Nova", "region": "Sao Paulo", "country_code": "BR"}}

    def get(self, path, params=None):
        if path == "MODEL_ID":
            return self.model_adset
        if path == "search":
            q = normalizar(params["q"])
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
        if path.endswith("/adsets"):
            return {"id": "ADSET_NOVO"}
        if path.endswith("/adcreatives"):
            return {"id": "CREATIVE_NOVO"}
        if path.endswith("/ads"):
            return {"id": "AD_NOVO"}
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
