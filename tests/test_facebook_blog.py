import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from app.social import facebook as fb


def article(number=1, title=None):
    return {
        "id": str(number), "title": title or "Praktische gids voor MIG lassen",
        "url": f"https://www.weldingshop.nl/blogs/news/artikel-{number}",
        "message": "Een bericht", "publishedAt": "2026-10-27T03:00:00Z",
    }


NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


class FacebookBlogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = patch.object(fb, "DB_PATH", Path(self.temp.name) / "social.sqlite")
        self.path.start()
        self.encrypt = patch.object(fb, "_encrypt", lambda value: "encrypted:" + value)
        self.decrypt = patch.object(fb, "_decrypt", lambda value: value.removeprefix("encrypted:"))
        self.encrypt.start()
        self.decrypt.start()
        fb.init_db()

    def tearDown(self):
        self.decrypt.stop()
        self.encrypt.stop()
        self.path.stop()
        self.temp.cleanup()

    def enable(self):
        with fb.connect() as db:
            db.execute(
                "UPDATE settings SET enabled=1,token_encrypted='encrypted:fake',checked_at='checked' WHERE id=1"
            )

    def test_no_token_or_disabled_never_calls_network(self):
        with patch.object(fb, "MetaClient") as client:
            self.assertEqual(fb.run_daily(NOW)["status"], "uitgeschakeld")
            client.assert_not_called()

    def test_requires_validated_connection(self):
        with self.assertRaises(ValueError):
            fb.save_planning(True, "10:00", True)

    def test_rejects_user_token_or_wrong_page(self):
        with patch.object(fb, "MetaClient") as client:
            client.return_value.request.return_value = {"id": "28708699718823924"}
            with self.assertRaises(ValueError):
                fb.save_connection("a" * 80)
        self.assertFalse(fb.settings()["connected"])

    def test_connection_encrypted_and_default_off(self):
        with patch.object(fb, "MetaClient") as client:
            client.return_value.request.return_value = {"id": fb.PAGE_ID, "name": "Weldingshop blog post"}
            client.return_value.all_posts.return_value = []
            fb.save_connection("a" * 80)
        config = fb.settings()
        self.assertTrue(config["connected"])
        self.assertFalse(config["enabled"])
        self.assertNotIn("token_encrypted", config)
        self.assertNotIn("a" * 80, str(fb.history()))

    def test_attachment_without_message_detected(self):
        a = article()
        post = {"id": "old", "attachments": {"data": [
            {"subattachments": {"data": [{"target": {
                "url": "https://weldingshop.nl/blogs/news/artikel-1?utm_source=facebook"
            }}]}}
        ]}}
        self.assertEqual(fb.find_existing(a, [post]), post)

    def test_facebook_redirect_and_text_urls(self):
        self.assertEqual(
            fb.canonical_blog_url("https://l.facebook.com/l.php?u=https%3A%2F%2Fweldingshop.nl%2Fblogs%2Fnews%2Fartikel-1%3Ffbclid%3Dx"),
            article()["url"],
        )
        self.assertTrue(fb.find_existing(article(), [
            {"message": "Lees https://weldingshop.nl/blogs/news/artikel-1?tracking=abc hier."}
        ]))
        self.assertEqual(fb.canonical_blog_url("https://example.com/blogs/news/artikel-1"), "")

    def test_title_only_existing_post_is_skipped(self):
        self.assertTrue(fb.find_existing(article(), [
            {"message": "Nieuw: Praktische gids voor MIG lassen. Lees meer!"}
        ]))

    def test_pagination_does_not_follow_token_in_next_url(self):
        meta = fb.MetaClient("secret")
        with patch.object(meta, "request", side_effect=[
            {"data": [{"id": "1"}], "paging": {"next": "https://untrusted/?access_token=secret",
                                             "cursors": {"after": "cursor"}}},
            {"data": [{"id": "2"}]},
        ]) as req:
            self.assertEqual(len(meta.all_posts()), 2)
            self.assertEqual(req.call_args.kwargs["params"]["after"], "cursor")
            self.assertEqual(req.call_args.args[1], fb.PAGE_ID + "/posts")

    def test_incomplete_history_stops_before_publish(self):
        self.enable()
        with patch.object(fb, "MetaClient") as cls:
            meta = cls.return_value
            meta.all_posts.side_effect = fb.MetaError("Geschiedenis onvolledig")
            self.assertEqual(fb.run_daily(NOW)["status"], "fout")
            meta.publish.assert_not_called()

    def test_existing_article_skipped_once_in_history(self):
        self.enable()
        a, next_a = article(), article(2, "Ander onderwerp om te delen")
        posts = [{"id": "old", "message": a["url"], "permalink_url": "https://facebook.com/old"}]
        with patch.object(fb, "fetch_articles", return_value=[a, next_a]), patch.object(fb, "MetaClient") as cls:
            meta = cls.return_value
            meta.all_posts.return_value = posts
            meta.publish.return_value = {"id": "new"}
            self.assertEqual(fb.run_daily(NOW)["status"], "geplaatst")
            self.assertEqual(meta.publish.call_args.args[0]["id"], "2")
            fb.preview()
        skipped = [r for r in fb.history() if r["status"] == "overgeslagen"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["permalink"], "https://facebook.com/old")

    def test_only_one_post_per_day_even_after_restart(self):
        self.enable()
        with patch.object(fb, "fetch_articles", return_value=[article()]), patch.object(fb, "MetaClient") as cls:
            meta = cls.return_value
            meta.all_posts.return_value = []
            meta.publish.return_value = {"id": "new"}
            self.assertEqual(fb.run_daily(NOW)["status"], "geplaatst")
            self.assertEqual(fb.run_daily(NOW)["status"], "vandaag_verwerkt")
            meta.publish.assert_called_once()
            self.assertTrue(any(r["status"] == "geplaatst" for r in fb.history()))

    def test_uncertain_write_is_not_retried_and_disables_planning(self):
        self.enable()
        with patch.object(fb, "fetch_articles", return_value=[article()]), patch.object(fb, "MetaClient") as cls:
            meta = cls.return_value
            meta.all_posts.return_value = []
            meta.publish.side_effect = TimeoutError("secret must not leak")
            self.assertEqual(fb.run_daily(NOW)["status"], "onzeker")
            self.assertFalse(fb.settings()["enabled"])
            self.assertEqual(fb.run_daily(NOW)["status"], "uitgeschakeld")
            meta.publish.assert_called_once()
            self.assertNotIn("secret must not leak", str(fb.history()))

    def test_expired_token_disables_planning(self):
        self.enable()
        with patch.object(fb, "MetaClient") as cls:
            cls.return_value.all_posts.side_effect = fb.MetaError("Token ongeldig (code 190)")
            fb.run_daily(NOW)
            self.assertFalse(fb.settings()["enabled"])

    def test_crash_before_confirmation_blocks_next_day(self):
        self.enable()
        a = article()
        with fb.connect() as db:
            db.execute(
                """INSERT INTO deliveries(page_id,article_id,title,url,message,status,started_at)
                   VALUES(?,?,?,?,?,'verzenden',?)""",
                (fb.PAGE_ID, a["id"], a["title"], a["url"], a["message"], NOW.isoformat()),
            )
        with patch.object(fb, "fetch_articles", return_value=[a, article(2)]), patch.object(fb, "MetaClient") as cls:
            cls.return_value.all_posts.return_value = []
            self.assertEqual(fb.run_daily(NOW)["status"], "onzeker")
            self.assertFalse(fb.settings()["enabled"])
            cls.return_value.publish.assert_not_called()

    def test_preview_never_posts(self):
        self.enable()
        with patch.object(fb, "fetch_articles", return_value=[article()]), patch.object(fb, "MetaClient") as cls:
            cls.return_value.all_posts.return_value = []
            self.assertEqual(fb.preview()["article"]["id"], "1")
            cls.return_value.publish.assert_not_called()

    def test_backfill_disabled_excludes_old_articles(self):
        self.enable()
        fb.save_planning(True, "10:00", False)
        with patch.object(fb, "fetch_articles", return_value=[article()]), patch.object(fb, "MetaClient") as cls:
            cls.return_value.all_posts.return_value = []
            self.assertEqual(fb.run_daily(NOW)["status"], "geen_artikel")
            cls.return_value.publish.assert_not_called()

    def test_before_local_scheduled_time(self):
        self.enable()
        with patch.object(fb, "MetaClient") as cls:
            self.assertEqual(fb.run_daily(datetime(2026, 10, 28, 8, tzinfo=timezone.utc))["status"],
                             "nog_niet_gepland")
            cls.assert_not_called()

    def test_error_payload_is_never_logged(self):
        meta = fb.MetaClient("secret")
        response = Mock(ok=False, status_code=400)
        response.json.return_value = {"error": {"code": 190, "message": "private secret"}}
        with patch.object(fb.requests, "request", return_value=response) as request:
            with self.assertRaises(fb.MetaError) as error:
                meta.all_posts()
            self.assertNotIn("secret", str(error.exception))
            self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer secret")

    def test_articles_are_published_news_only_and_all_pages_read(self):
        client = Mock()
        base = dict(article(), summary="Een uitleg", image=None, isPublished=True,
                    handle="artikel-1", blog={"handle": "news"})
        hidden = dict(base, id="hidden", isPublished=False)
        other = dict(base, id="brand", blog={"handle": "onze-merken"})
        future = dict(base, id="future", publishedAt="2027-01-01T00:00:00Z")
        client.graphql.side_effect = [
            {"articles": {"nodes": [hidden, other, future], "pageInfo": {"hasNextPage": True, "endCursor": "x"}}},
            {"articles": {"nodes": [base], "pageInfo": {"hasNextPage": False}}},
        ]
        self.assertEqual([r["id"] for r in fb.fetch_articles(client, NOW)], ["1"])
        self.assertEqual(client.graphql.call_args.args[1], {"after": "x"})


if __name__ == "__main__":
    unittest.main()
