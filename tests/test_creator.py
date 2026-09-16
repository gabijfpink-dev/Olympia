import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from meta_ads_automation.creator import (
    Creator,
    build_ad_params,
    build_adset_params,
    build_campaign_params,
    build_creative_params,
)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "example_campaign.json"


class BuildParamsTests(unittest.TestCase):
    def test_build_campaign_params_uses_defaults_and_settings_override(self):
        params = build_campaign_params(
            {
                "name": "Minha Campanha",
                "settings": {"objective": "OUTCOME_SALES"},
            }
        )
        self.assertEqual(params["name"], "Minha Campanha")
        self.assertEqual(params["status"], "PAUSED")
        # settings deve sobrescrever o valor padrão de objective
        self.assertEqual(params["objective"], "OUTCOME_SALES")

    def test_build_adset_params_includes_targeting_and_budget(self):
        params = build_adset_params(
            {
                "name": "Conjunto 1",
                "daily_budget": 5000,
                "targeting": {"geo_locations": {"countries": ["BR"]}},
            },
            campaign_id="123",
        )
        self.assertEqual(params["campaign_id"], "123")
        self.assertEqual(params["daily_budget"], 5000)
        self.assertEqual(params["targeting"]["geo_locations"]["countries"], ["BR"])

    def test_build_creative_params_builds_link_data_with_caption_and_image(self):
        params = build_creative_params(
            {
                "message": "Texto do anúncio",
                "caption": "loja.com.br",
                "link": "https://loja.com.br",
                "call_to_action_type": "SHOP_NOW",
            },
            page_id="999",
            image_hash="abc123",
        )
        spec = params["object_story_spec"]
        self.assertEqual(spec["page_id"], "999")
        self.assertEqual(spec["link_data"]["caption"], "loja.com.br")
        self.assertEqual(spec["link_data"]["image_hash"], "abc123")
        self.assertEqual(spec["link_data"]["call_to_action"]["type"], "SHOP_NOW")

    def test_build_creative_params_respects_raw_object_story_spec(self):
        raw_spec = {"page_id": "999", "video_data": {"video_id": "111"}}
        params = build_creative_params({"object_story_spec": raw_spec}, page_id="999", image_hash=None)
        self.assertEqual(params["object_story_spec"], raw_spec)

    def test_build_ad_params_links_adset_and_creative(self):
        params = build_ad_params({"name": "Anúncio 1"}, adset_id="adset_1", creative_id="creative_1")
        self.assertEqual(params["adset_id"], "adset_1")
        self.assertEqual(params["creative"], {"creative_id": "creative_1"})
        self.assertEqual(params["status"], "PAUSED")


class DryRunCreatorTests(unittest.TestCase):
    def test_example_config_runs_end_to_end_in_dry_run(self):
        with CONFIG_PATH.open(encoding="utf-8") as f:
            config = json.load(f)

        fake_account = MagicMock()
        fake_account.get_id.return_value = "act_1234"

        creator = Creator(fake_account, dry_run=True)
        result = creator.run(config)

        self.assertEqual(len(result.errors), 0)
        self.assertEqual(len(result.campaigns), 1)
        campaign = result.campaigns[0]
        self.assertTrue(campaign["id"].startswith("DRY_RUN_CAMPANHA"))
        self.assertEqual(len(campaign["ad_sets"]), 1)
        adset = campaign["ad_sets"][0]
        self.assertEqual(len(adset["ads"]), 2)
        for ad in adset["ads"]:
            self.assertIn("id", ad)
            self.assertIn("creative_id", ad)

        # Em dry-run nenhuma chamada real deve ter sido feita à API.
        fake_account.create_campaign.assert_not_called()
        fake_account.create_ad_set.assert_not_called()
        fake_account.create_ad_creative.assert_not_called()
        fake_account.create_ad.assert_not_called()

    def test_missing_page_id_raises_creation_error_without_continue_on_error(self):
        config = {
            "campaigns": [
                {
                    "name": "Campanha sem page_id",
                    "ad_sets": [
                        {
                            "name": "Conjunto 1",
                            "ads": [{"name": "Anúncio 1", "creative": {"message": "oi"}}],
                        }
                    ],
                }
            ]
        }
        fake_account = MagicMock()
        creator = Creator(fake_account, dry_run=True)
        with self.assertRaises(Exception):
            creator.run(config)


if __name__ == "__main__":
    unittest.main()
