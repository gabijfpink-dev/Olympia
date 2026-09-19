import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from promo_bot.affiliate import AffiliateRuleMissingError, build_affiliate_link
from promo_bot.caption_generator import Promo, generate_caption, generate_script
from promo_bot.config import AffiliateRule, MonitorConfig, load_monitor_config
from promo_bot.link_extractor import extract_urls
from promo_bot.pipeline import PromoResult, process_message
from promo_bot.price_extractor import extract_price_info
from promo_bot.stores import detect_store
from promo_bot.whatsapp_publisher import format_whatsapp_message


class LinkExtractorTests(unittest.TestCase):
    def test_extracts_multiple_links_and_strips_trailing_punctuation(self):
        text = "Olha isso: https://shopee.com.br/produto-i.1.2, e também https://amzn.to/abc123."
        self.assertEqual(
            extract_urls(text),
            ["https://shopee.com.br/produto-i.1.2", "https://amzn.to/abc123"],
        )

    def test_no_links_returns_empty_list(self):
        self.assertEqual(extract_urls("promoção sem link nenhum"), [])

    def test_deduplicates_repeated_links(self):
        text = "link: https://amazon.com.br/dp/X repetido https://amazon.com.br/dp/X"
        self.assertEqual(extract_urls(text), ["https://amazon.com.br/dp/X"])


class DetectStoreTests(unittest.TestCase):
    def test_detects_known_stores(self):
        self.assertEqual(detect_store("https://www.amazon.com.br/dp/XYZ"), "amazon")
        self.assertEqual(detect_store("https://shopee.com.br/produto-i.1.2"), "shopee")
        self.assertEqual(detect_store("https://amzn.to/abc"), "amazon")
        self.assertEqual(detect_store("https://s.shopee.com.br/abc"), "shopee")
        self.assertEqual(detect_store("https://produto.mercadolivre.com.br/MLB-1"), "mercadolivre")

    def test_unknown_domain_returns_none(self):
        self.assertIsNone(detect_store("https://exemplo-generico.com/produto"))

    def test_no_hostname_returns_none(self):
        self.assertIsNone(detect_store("not-a-url"))


class PriceExtractorTests(unittest.TestCase):
    def test_de_por_pattern(self):
        info = extract_price_info("de R$ 199,90 por R$ 89,90 (55% OFF)")
        self.assertEqual(info.original_price, "R$ 199,90")
        self.assertEqual(info.price, "R$ 89,90")
        self.assertEqual(info.discount_pct, "55%")

    def test_single_price(self):
        info = extract_price_info("Só R$ 49,90 hoje!")
        self.assertEqual(info.price, "R$ 49,90")
        self.assertIsNone(info.original_price)

    def test_two_prices_without_de_por_assumes_lowest_is_final(self):
        info = extract_price_info("R$ 89,90 (antes R$ 199,90)")
        self.assertEqual(info.price, "R$ 89,90")
        self.assertEqual(info.original_price, "R$ 199,90")

    def test_no_price_returns_all_none(self):
        info = extract_price_info("promoção boa, corre lá")
        self.assertIsNone(info.price)
        self.assertIsNone(info.original_price)
        self.assertIsNone(info.discount_pct)


class AffiliateTests(unittest.TestCase):
    def test_params_are_merged_into_url(self):
        rules = {"amazon": AffiliateRule(params={"tag": "meunome-20"})}
        link = build_affiliate_link("https://amazon.com.br/dp/XYZ", "amazon", rules)
        self.assertIn("tag=meunome-20", link)
        self.assertTrue(link.startswith("https://amazon.com.br/dp/XYZ"))

    def test_params_override_existing_query_param(self):
        rules = {"amazon": AffiliateRule(params={"tag": "novo-tag"})}
        link = build_affiliate_link("https://amazon.com.br/dp/XYZ?tag=velho-tag", "amazon", rules)
        self.assertIn("tag=novo-tag", link)
        self.assertNotIn("velho-tag", link)

    def test_template_wraps_url(self):
        rules = {"shopee": AffiliateRule(template="https://rede.com/redirect?url={url}&aff_id=123")}
        link = build_affiliate_link("https://shopee.com.br/produto", "shopee", rules)
        self.assertEqual(link, "https://rede.com/redirect?url=https://shopee.com.br/produto&aff_id=123")

    def test_missing_rule_raises(self):
        with self.assertRaises(AffiliateRuleMissingError):
            build_affiliate_link("https://shein.com/produto", "shein", {})


class CaptionGeneratorTests(unittest.TestCase):
    def test_caption_includes_link_title_and_price(self):
        promo = Promo(
            store="shopee",
            link="https://rede.com/redirect?url=x",
            title="Fone Bluetooth XYZ",
            price="R$ 89,90",
            original_price="R$ 199,90",
            discount_pct="55%",
        )
        caption = generate_caption(promo, seed=1)
        self.assertIn("Fone Bluetooth XYZ", caption)
        self.assertIn("R$ 89,90", caption)
        self.assertIn("R$ 199,90", caption)
        self.assertIn("55%", caption)
        self.assertIn(promo.link, caption)
        self.assertIn("Shopee", caption)

    def test_caption_without_price_still_works(self):
        promo = Promo(store="shein", link="https://x", title="Vestido")
        caption = generate_caption(promo, seed=2)
        self.assertIn("Vestido", caption)
        self.assertIn("https://x", caption)

    def test_script_mentions_store_and_link_placeholder_sections(self):
        promo = Promo(store="amazon", link="https://x", title="Produto", price="R$ 10,00")
        script = generate_script(promo, seed=3)
        self.assertIn("Amazon", script)
        self.assertIn("[GANCHO", script)
        self.assertIn("[CTA", script)
        self.assertIn("R$ 10,00", script)


class LoadMonitorConfigTests(unittest.TestCase):
    def test_loads_valid_config(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "promo.json"
            path.write_text(
                json.dumps(
                    {
                        "name": "teste",
                        "telegram_channels": ["@grupo1"],
                        "allowed_stores": ["Amazon"],
                        "affiliates": {"amazon": {"params": {"tag": "x-20"}}},
                    }
                ),
                encoding="utf-8",
            )
            config = load_monitor_config(str(path))
            self.assertEqual(config.allowed_stores, ["amazon"])
            self.assertEqual(config.affiliates["amazon"].params["tag"], "x-20")

    def test_missing_required_field_raises(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "promo.json"
            path.write_text(json.dumps({"name": "teste"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_monitor_config(str(path))


class ProcessMessageTests(unittest.TestCase):
    def _config(self, **overrides) -> MonitorConfig:
        base = dict(
            name="teste",
            telegram_channels=["@grupo1"],
            allowed_stores=["amazon", "shopee"],
            affiliates={
                "amazon": AffiliateRule(params={"tag": "meunome-20"}),
                "shopee": AffiliateRule(template="https://rede.com/redirect?url={url}"),
            },
            keywords_include=[],
            keywords_exclude=[],
            review_chat=None,
            output_log="output/promos.jsonl",
        )
        base.update(overrides)
        return MonitorConfig(**base)

    def test_full_pipeline_generates_result_with_affiliate_link(self):
        text = "Fone incrível\nde R$ 199,90 por R$ 89,90\nhttps://amazon.com.br/dp/XYZ"
        results = process_message(text, self._config(), seed=1)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.store, "amazon")
        self.assertIn("tag=meunome-20", result.affiliate_link)
        self.assertIn("R$ 89,90", result.caption)

    def test_store_not_allowed_is_skipped(self):
        text = "Vestido lindo https://shein.com/produto-x"
        results = process_message(text, self._config(), seed=1)
        self.assertEqual(results, [])

    def test_store_without_affiliate_rule_is_skipped(self):
        config = self._config(allowed_stores=["amazon", "shein"], affiliates={"amazon": AffiliateRule(params={"tag": "x"})})
        text = "Vestido lindo https://shein.com/produto-x"
        results = process_message(text, config, seed=1)
        self.assertEqual(results, [])

    def test_exclude_keyword_blocks_message(self):
        config = self._config(keywords_exclude=["esgotado"])
        text = "ESGOTADO: https://amazon.com.br/dp/XYZ"
        results = process_message(text, config, seed=1)
        self.assertEqual(results, [])

    def test_include_keyword_required(self):
        config = self._config(keywords_include=["relampago"])
        text = "promoção qualquer https://amazon.com.br/dp/XYZ"
        results = process_message(text, config, seed=1)
        self.assertEqual(results, [])

        text_com_keyword = "oferta relampago https://amazon.com.br/dp/XYZ"
        results_ok = process_message(text_com_keyword, config, seed=1)
        self.assertEqual(len(results_ok), 1)

    def test_multiple_links_from_different_stores(self):
        text = (
            "Duas ofertas hoje:\n"
            "https://amazon.com.br/dp/AAA\n"
            "https://shopee.com.br/produto-i.1.2"
        )
        results = process_message(text, self._config(), seed=1)
        stores = sorted(r.store for r in results)
        self.assertEqual(stores, ["amazon", "shopee"])


class WhatsAppMessageFormatTests(unittest.TestCase):
    def test_format_uses_caption_only_not_script(self):
        result = PromoResult(
            store="amazon",
            original_url="https://amazon.com.br/dp/XYZ",
            affiliate_link="https://amazon.com.br/dp/XYZ?tag=x-20",
            caption="🔥 Legenda pronta\nhttps://amazon.com.br/dp/XYZ?tag=x-20",
            script="[GANCHO - 0-3s]\nroteiro que não deve ir pro WhatsApp",
            source_text="texto original",
        )
        message = format_whatsapp_message(result)
        self.assertEqual(message, result.caption)
        self.assertNotIn("[GANCHO", message)


if __name__ == "__main__":
    unittest.main()
