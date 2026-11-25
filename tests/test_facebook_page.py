import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from streamlit.testing.v1 import AppTest


class FacebookPageTests(unittest.TestCase):
    def test_disconnected_screen_and_visible_history(self):
        with tempfile.TemporaryDirectory() as directory:
            source = f"""
from pathlib import Path
import app.social.facebook as service
service.DB_PATH = Path({str(Path(directory) / "facebook.sqlite")!r})
service.init_db()
service.record("geplaatst", "Testplaatsing", {{"id":"test", "title":"Lasdraad kiezen", "url":"https://www.weldingshop.nl/blogs/news/test"}},
               {{"id":"page_post", "permalink_url":"https://www.facebook.com/page_post"}})
from app.web.facebook_page import show_facebook_page
show_facebook_page()
"""
            with patch("app.social.facebook._oauth_config", side_effect=ValueError("Facebook-koppelen is nog niet ingesteld")):
                app = AppTest.from_string(source).run(timeout=30)
            self.assertEqual(list(app.exception), [])
            self.assertEqual([tab.label for tab in app.tabs],
                             ["Overzicht", "Verbinding", "Planning", "Historie"])
            self.assertEqual(app.metric[0].value, "Nog koppelen")
            self.assertEqual(app.metric[1].value, "Uit")
            self.assertTrue(app.button[0].disabled)
            publish = next(button for button in app.button if button.label == "Plaats dit bericht")
            self.assertTrue(publish.disabled)
            self.assertFalse(any(field.label == "Nieuw paginatoegangstoken" for field in app.text_input))
            self.assertTrue(any("Facebook-koppelen is nog niet ingesteld" in warning.value
                                for warning in app.warning))
            self.assertEqual(app.dataframe[0].value.iloc[0]["Artikel"], "Lasdraad kiezen")
            self.assertEqual(app.dataframe[0].value.iloc[0]["Facebook-bericht"], "https://www.facebook.com/page_post")


if __name__ == "__main__":
    unittest.main()
