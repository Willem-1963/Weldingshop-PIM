import pytest
from streamlit.testing.v1 import AppTest

from app.web.label_page import LABEL_FORMATS, build_free_label_document


def test_free_label_print_layout_and_escaping():
    document = build_free_label_document([
        {"text": "<script> & tekst", "size": 18, "alignment": "left"},
        {"text": "Midden", "size": 12, "alignment": "center"},
        {"text": "Rechts", "size": 9, "alignment": "right"},
    ], list(LABEL_FORMATS)[1], 2)
    assert document.count('<section class="label ') == 2
    assert "size: 57mm 32mm" in document
    assert "&lt;script&gt; &amp; tekst" in document
    for size, alignment in [(18, "left"), (12, "center"), (9, "right")]:
        assert f"font-size:{size}pt;text-align:{alignment}" in document
    with pytest.raises(ValueError):
        build_free_label_document([{}] * 6, list(LABEL_FORMATS)[0], 1)


def test_tabs_and_free_label_controls():
    app = AppTest.from_string('''
from unittest.mock import patch
from app.web import label_page
with patch.object(label_page, "get_active_template", return_value=""), patch.object(label_page, "get_last_settings", return_value={}):
    label_page.show_label_page()
''').run()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Productlabels", "Vrije labels"]
    app.text_input(key="free_label_1_text").set_value("Magazijn")
    app.number_input(key="free_label_1_size").set_value(24)
    app.selectbox(key="free_label_1_alignment").set_value("Rechts").run()
    assert not app.exception
    assert len(app.get("download_button")) == 1
    app.number_input(key="free_label_count").set_value(1).run()
    assert not app.exception
    assert app.text_input(key="free_label_1_text").value == "Magazijn"
