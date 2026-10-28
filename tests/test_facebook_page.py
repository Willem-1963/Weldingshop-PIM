import tempfile
import unittest
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
            app = AppTest.from_string(source).run(timeout=30)
            self.assertEqual(list(app.exception), [])
            self.assertEqual([tab.label for tab in app.tabs],
                             ["Overzicht", "Verbinding", "Planning", "Historie"])
            self.assertEqual(app.metric[0].value, "Nog koppelen")
            self.assertEqual(app.metric[1].value, "Uit")
            self.assertTrue(app.button[0].disabled)
            token = next(field for field in app.text_input if field.label == "Nieuw paginatoegangstoken")
            self.assertEqual(token.proto.type, 1)
            self.assertEqual(app.dataframe[0].value.iloc[0]["Artikel"], "Lasdraad kiezen")
            self.assertEqual(app.dataframe[0].value.iloc[0]["Facebook-bericht"], "https://www.facebook.com/page_post")


if __name__ == "__main__":
    unittest.main()
