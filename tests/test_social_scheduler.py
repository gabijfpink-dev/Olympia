import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import requests

from meta_ads_ops.graph import GraphError, RateLimitError
from social_scheduler.linkedin import LinkedInClient, LinkedInPublisher, escape_commentary, extract_code
from social_scheduler.meta import FacebookPublisher, InstagramPublisher
from social_scheduler.runner import run
from social_scheduler.schedule import build_post, load_schedule, parse_datetime
from social_scheduler.settings import (
    FacebookSettings,
    InstagramSettings,
    LinkedInSettings,
    MissingCredentialError,
)
from social_scheduler.state import LockedError, RunLock, StateStore

TZ = "America/Sao_Paulo"


def make_post(**overrides):
    row = {
        "id": "p1",
        "publish_at": "2026-10-10 09:00",
        "platforms": "facebook, instagram, linkedin",
        "text": "Olá",
        "images": "https://x.com/a.jpg",
    }
    row.update(overrides)
    return build_post(row, TZ, 2)


class ScheduleTests(unittest.TestCase):
    def test_parses_brazilian_date_and_applies_timezone(self):
        dt = parse_datetime("10/10/2026 09:00", TZ)
        self.assertEqual(dt.astimezone(timezone.utc).hour, 12)

    def test_iso_with_offset_keeps_offset(self):
        dt = parse_datetime("2026-10-10T09:00:00+00:00", TZ)
        self.assertEqual(dt.utcoffset(), timedelta(0))

    def test_platform_aliases_and_per_platform_text(self):
        post = make_post(platforms="IG; fb", text_instagram="só no insta")
        self.assertEqual(post.platforms, ["instagram", "facebook"])
        self.assertEqual(post.text_for("instagram"), "só no insta")
        self.assertEqual(post.text_for("facebook"), "Olá")
        self.assertTrue(post.valid, post.errors)

    def test_instagram_requires_public_media(self):
        post = make_post(platforms="instagram", images="")
        self.assertIn("instagram: precisa de 'images' ou 'video'", post.errors)

    def test_instagram_rejects_local_image(self):
        with TemporaryDirectory() as tmp:
            img = os.path.join(tmp, "a.jpg")
            open(img, "wb").close()
            post = make_post(platforms="instagram", images=img)
        self.assertTrue(any("URLs públicas" in e for e in post.errors))

    def test_linkedin_rejects_image_plus_link(self):
        post = make_post(platforms="linkedin", link="https://site")
        self.assertTrue(any("combinar imagem e link" in e for e in post.errors))

    def test_unknown_platform_and_bad_date(self):
        post = make_post(platforms="tiktok", publish_at="amanhã")
        self.assertEqual(len(post.errors), 3)  # plataforma, data, nenhuma plataforma

    def test_draft_status_disables(self):
        self.assertFalse(make_post(status="Rascunho").enabled)

    def test_load_csv_semicolon_duplicates_and_relative_images(self):
        with TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "foto.jpg"), "wb").close()
            path = os.path.join(tmp, "agenda.csv")
            with open(path, "w", encoding="utf-8-sig") as fh:
                fh.write("id;publish_at;platforms;text;images\n")
                fh.write("a;10/10/2026 09:00;facebook;oi;foto.jpg\n")
                fh.write(";;;;\n")
                fh.write("a;11/10/2026 09:00;facebook;oi de novo;\n")
            posts = load_schedule(path, TZ)
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0].images, [os.path.join(tmp, "foto.jpg")])
        self.assertTrue(all("id duplicado" in p.errors[-1] for p in posts))

    def test_load_json(self):
        with TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agenda.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"posts": [{"id": "a", "publish_at": "2026-10-10 09:00",
                                      "platforms": "linkedin", "text": "oi",
                                      "images": ["https://x/1.jpg", "https://x/2.jpg"]}]}, fh)
            posts = load_schedule(path, TZ)
        self.assertEqual(posts[0].images, ["https://x/1.jpg", "https://x/2.jpg"])
        self.assertTrue(posts[0].valid, posts[0].errors)


class FakePublisher:
    def __init__(self, platform, fail=None):
        self.platform = platform
        self.fail = fail
        self.calls = []

    def publish(self, post):
        self.calls.append(post.id)
        if self.fail:
            raise self.fail
        return f"{self.platform}-{post.id}"


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.state = StateStore(os.path.join(self.tmp.name, "state.json"))
        self.now = parse_datetime("2026-10-10 09:05", TZ)

    def tearDown(self):
        self.tmp.cleanup()

    def test_publishes_due_once_and_skips_future(self):
        posts = [make_post(), make_post(id="futuro", publish_at="2026-10-10 10:00")]
        pubs = {p: FakePublisher(p) for p in ("facebook", "instagram", "linkedin")}

        report = run(posts, self.state, self.now, pubs.__getitem__)
        self.assertEqual(len(report.published), 3)
        self.assertEqual(pubs["instagram"].calls, ["p1"])

        # Segunda rodada: nada de novo (é isso que evita post duplicado).
        report = run(posts, StateStore(self.state.path), self.now, pubs.__getitem__)
        self.assertEqual(report.published, [])
        self.assertEqual(pubs["instagram"].calls, ["p1"])

    def test_failure_on_one_platform_does_not_block_others_and_gives_up(self):
        posts = [make_post()]
        pubs = {
            "facebook": FakePublisher("facebook"),
            "instagram": FakePublisher("instagram", fail=GraphError({"message": "imagem ruim"})),
            "linkedin": FakePublisher("linkedin"),
        }
        for _ in range(3):
            run(posts, self.state, self.now, pubs.__getitem__)
        self.assertEqual(len(pubs["instagram"].calls), 3)
        self.assertEqual(pubs["facebook"].calls, ["p1"])

        report = run(posts, self.state, self.now, pubs.__getitem__)
        self.assertEqual(report.gave_up, ["p1 [instagram]"])
        self.assertEqual(len(pubs["instagram"].calls), 3)

    def test_too_late_is_not_published(self):
        report = run([make_post()], self.state, self.now + timedelta(hours=7), lambda p: FakePublisher(p))
        self.assertEqual(len(report.too_late), 3)
        self.assertEqual(report.published, [])

    def test_rate_limit_stops_round_without_burning_attempt(self):
        posts = [make_post(platforms="facebook"), make_post(id="p2", platforms="facebook")]
        pub = FakePublisher("facebook", fail=RateLimitError("calma"))
        report = run(posts, self.state, self.now, lambda p: pub)
        self.assertEqual(pub.calls, ["p1"])
        self.assertFalse(report.ok)
        self.assertEqual(self.state.attempts("p1", "facebook"), 0)

    def test_missing_credentials_does_not_burn_attempt(self):
        def factory(platform):
            raise MissingCredentialError("sem token")

        report = run([make_post(platforms="linkedin")], self.state, self.now, factory)
        self.assertEqual(len(report.failed), 1)
        self.assertEqual(self.state.attempts("p1", "linkedin"), 0)

    def test_dry_run_does_not_touch_state_or_publishers(self):
        def factory(platform):
            raise AssertionError("dry-run não deve criar publisher")

        report = run([make_post()], self.state, self.now, factory, dry_run=True)
        self.assertEqual(len(report.would_publish), 3)
        self.assertEqual(self.state.data, {})

    def test_invalid_and_disabled_posts(self):
        posts = [make_post(platforms="instagram", images=""), make_post(id="off", status="draft")]
        report = run(posts, self.state, self.now, lambda p: FakePublisher(p))
        self.assertEqual(len(report.invalid), 1)
        self.assertEqual(report.published, [])

    def test_reset_allows_republish(self):
        pub = FakePublisher("facebook")
        run([make_post(platforms="facebook")], self.state, self.now, lambda p: pub)
        self.assertTrue(self.state.reset("p1", "facebook"))
        run([make_post(platforms="facebook")], self.state, self.now, lambda p: pub)
        self.assertEqual(pub.calls, ["p1", "p1"])


class LockTests(unittest.TestCase):
    def test_second_lock_fails(self):
        with TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            with RunLock(path):
                with self.assertRaises(LockedError):
                    with RunLock(path):
                        pass
            with RunLock(path):  # liberado depois
                pass


class FacebookPublisherTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.pub = FacebookPublisher(FacebookSettings("PAGE", "tok", "v23.0"), client=self.client)

    def test_single_image_url(self):
        self.client.post.return_value = {"id": "photo", "post_id": "PAGE_1"}
        self.assertEqual(self.pub.publish(make_post()), "PAGE_1")
        self.client.post.assert_called_once_with(
            "PAGE/photos", {"published": "true", "caption": "Olá", "url": "https://x.com/a.jpg"}
        )

    def test_multiple_images_attach_to_feed(self):
        self.client.post.side_effect = [{"id": "f1"}, {"id": "f2"}, {"id": "PAGE_9"}]
        post = make_post(images="https://x/1.jpg|https://x/2.jpg")
        self.assertEqual(self.pub.publish(post), "PAGE_9")
        path, data = self.client.post.call_args.args
        self.assertEqual(path, "PAGE/feed")
        self.assertEqual(json.loads(data["attached_media[1]"]), {"media_fbid": "f2"})

    def test_local_image_uses_multipart(self):
        self.client.post_file.return_value = {"id": "x", "post_id": "PAGE_2"}
        self.pub.publish(make_post(images=__file__))
        self.client.post_file.assert_called_once()
        self.assertEqual(self.client.post_file.call_args.args[1], "source")

    def test_link_post(self):
        self.client.post.return_value = {"id": "PAGE_3"}
        self.pub.publish(make_post(images="", link="https://site"))
        self.client.post.assert_called_once_with("PAGE/feed", {"message": "Olá", "link": "https://site"})


class InstagramPublisherTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.pub = InstagramPublisher(InstagramSettings("IG", "tok", "v23.0", "graph.facebook.com"), client=self.client)

    def test_single_image_creates_then_publishes(self):
        self.client.post.side_effect = [{"id": "C1"}, {"id": "MEDIA"}]
        self.client.get.return_value = {"status_code": "FINISHED"}
        self.assertEqual(self.pub.publish(make_post()), "MEDIA")
        self.assertEqual(self.client.post.call_args_list[1].args, ("IG/media_publish", {"creation_id": "C1"}))

    def test_carousel(self):
        self.client.post.side_effect = [{"id": "c1"}, {"id": "c2"}, {"id": "P"}, {"id": "MEDIA"}]
        self.client.get.return_value = {"status_code": "FINISHED"}
        self.pub.publish(make_post(images="https://x/1.jpg|https://x/2.jpg"))
        parent = self.client.post.call_args_list[2].args[1]
        self.assertEqual(parent["media_type"], "CAROUSEL")
        self.assertEqual(parent["children"], "c1,c2")

    @patch("social_scheduler.meta.time.sleep")
    def test_reels_waits_for_processing(self, mock_sleep):
        self.client.post.side_effect = [{"id": "R"}, {"id": "MEDIA"}]
        self.client.get.side_effect = [{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}]
        self.pub.publish(make_post(images="", video="https://x/v.mp4"))
        self.assertEqual(self.client.post.call_args_list[0].args[1]["media_type"], "REELS")
        mock_sleep.assert_called_once()

    def test_processing_error_raises(self):
        self.client.post.return_value = {"id": "C"}
        self.client.get.return_value = {"status_code": "ERROR", "status": "formato inválido"}
        with self.assertRaises(GraphError):
            self.pub.publish(make_post())


def _response(status=201, payload=None, headers=None):
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(payload or {}).encode()
    resp.headers.update(headers or {})
    return resp


class LinkedInTests(unittest.TestCase):
    def setUp(self):
        self.settings = LinkedInSettings("tok", "urn:li:person:abc", "202608")

    @patch("social_scheduler.linkedin.requests.request")
    def test_text_post_sends_headers_and_returns_urn(self, mock_req):
        mock_req.return_value = _response(headers={"x-restli-id": "urn:li:share:1"})
        pub = LinkedInPublisher(self.settings)
        self.assertEqual(pub.publish(make_post(images="", text="Oi (teste) #tag")), "urn:li:share:1")
        kwargs = mock_req.call_args.kwargs
        self.assertEqual(kwargs["headers"]["LinkedIn-Version"], "202608")
        self.assertEqual(kwargs["json"]["commentary"], "Oi \\(teste\\) {hashtag|\\#|tag}")
        self.assertNotIn("content", kwargs["json"])

    @patch("social_scheduler.linkedin.requests.request")
    def test_image_post_uploads_first(self, mock_req):
        mock_req.side_effect = [
            _response(200, {"value": {"uploadUrl": "https://up", "image": "urn:li:image:9"}}),
            _response(200, {}),  # download da imagem
            _response(201, {}),  # PUT upload
            _response(201, headers={"x-restli-id": "urn:li:share:2"}),
        ]
        LinkedInPublisher(self.settings).publish(make_post())
        body = mock_req.call_args.kwargs["json"]
        self.assertEqual(body["content"]["media"]["id"], "urn:li:image:9")

    @patch("social_scheduler.linkedin.requests.request")
    def test_expired_token_message(self, mock_req):
        mock_req.return_value = _response(401, {"message": "Invalid access token"})
        with self.assertRaisesRegex(Exception, "linkedin-auth"):
            LinkedInClient("tok", "202608").post_json("posts", {})

    def test_escape_keeps_hashtags(self):
        self.assertEqual(escape_commentary("a_b #oi"), "a\\_b {hashtag|\\#|oi}")

    def test_extract_code_checks_state(self):
        self.assertEqual(extract_code("http://localhost:8000/callback?code=XYZ&state=s1", "s1"), "XYZ")
        with self.assertRaises(Exception):
            extract_code("http://localhost:8000/callback?code=XYZ&state=outro", "s1")


if __name__ == "__main__":
    unittest.main()
