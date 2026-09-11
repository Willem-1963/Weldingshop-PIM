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
    assert [tab.label for tab in app.tabs] == ["Productlabels", "Vrije labels", "Mobiel"]
    app.text_input(key="free_label_1_text").set_value("Magazijn")
    app.number_input(key="free_label_1_size").set_value(24)
    app.selectbox(key="free_label_1_alignment").set_value("Rechts").run()
    assert not app.exception
    assert len(app.get("download_button")) == 1
    app.number_input(key="free_label_count").set_value(1).run()
    assert not app.exception
    assert app.text_input(key="free_label_1_text").value == "Magazijn"


def test_product_free_line_follows_location():
    from app.web.label_page import FieldSetting, build_label_document
    document = build_label_document(
        {"sku": "TEST", "custom_location": "A-12", "free_label_text": "Extra <tekst>",
         "free_label_size": 18},
        [FieldSetting("custom_location", 12, "1 regel", 1),
         FieldSetting("sku", 12, "1 regel", 2)],
        list(LABEL_FORMATS)[0], 1,
    )
    assert document.index("Locatie: A-12") < document.index("Extra &lt;tekst&gt;")
    assert document.index("Extra &lt;tekst&gt;") < document.index(">TEST</div>")
    assert 'font-size:18pt">Extra &lt;tekst&gt;' in document


def test_product_template_survives_free_text_size_and_quantity_changes():
    app = AppTest.from_string('''
from contextlib import ExitStack
from unittest.mock import patch
from app.web import label_page as page
page.st.session_state.setdefault("label_search_query", "TEST")
settings = {"label_format": list(page.LABEL_FORMATS)[1], "fields": {
    "title": {"enabled": True, "size_label": "Groot — 18 pt", "display": "2 regels", "position": 2},
    "custom_location": {"enabled": True, "size_label": "Klein — 9 pt", "display": "1 regel", "position": 1},
}}
product = {"sku": "TEST", "supplier": "Test", "source_title": "Testproduct"}
with ExitStack() as stack:
    for name, value in {
        "get_active_template": "Magazijn", "get_template": settings,
        "get_last_settings": {}, "list_templates": ["Magazijn"],
        "list_workstations": [], "save_last_settings": None,
        "_search_all_suppliers": [product],
        "shopify_label_values_for_sku": {"custom_location": "A-1", "ean": "", "inventory_quantity": 1},
    }.items():
        stack.enter_context(patch.object(page, name, return_value=value))
    page.show_label_page()
''').run()
    assert not app.exception
    for key, value in [("product_label_free_text", "Vrije tekst"),
                       ("product_label_free_size", "Extra groot — 28 pt"),
                       ("label_quantity", 3)]:
        if key.endswith("text"):
            app.text_input(key=key).set_value(value).run()
        elif key.endswith("size"):
            app.selectbox(key=key).set_value(value).run()
        else:
            app.number_input(key=key).set_value(value).run()
        assert not app.exception
        assert app.selectbox(key="label_saved_template").value == "Magazijn"
        assert app.selectbox(key="label_format").value == list(LABEL_FORMATS)[1]
        assert app.selectbox(key="label_position_custom_location").value == 1
        assert app.selectbox(key="label_size_custom_location").value == "Klein — 9 pt"
        assert app.text_input(key="product_label_free_text").value == "Vrije tekst"


def test_generate_ean_replaces_existing_number_in_field_and_preview(monkeypatch):
    from app.web import label_page as page
    new_ean = "2900000000015"
    monkeypatch.setattr(page, "generate_unique_ean", lambda: new_ean)
    monkeypatch.setattr(page, "get_active_template", lambda: "")
    monkeypatch.setattr(page, "get_last_settings", lambda: {})
    monkeypatch.setattr(page, "list_templates", lambda: [])
    monkeypatch.setattr(page, "list_workstations", lambda: [])
    monkeypatch.setattr(page, "save_last_settings", lambda settings: None)
    monkeypatch.setattr(page, "_search_all_suppliers", lambda query: [
        {"sku": "TEST", "supplier": "Test", "source_title": "Testproduct"},
    ])
    monkeypatch.setattr(page, "shopify_label_values_for_sku", lambda sku: {
        "custom_location": "A-1", "ean": "2900000000008", "inventory_quantity": 1,
    })
    previews = []
    locations = []
    original = page.build_label_document
    def capture(product, *args):
        previews.append(product["ean"])
        locations.append(product["custom_location"])
        return original(product, *args)
    monkeypatch.setattr(page, "build_label_document", capture)
    app = AppTest.from_string('''
from app.web import label_page as page
page.st.session_state.setdefault("label_search_query", "TEST")
page.show_label_page()
''').run()
    assert not app.exception
    assert app.text_input(key="label_ean_value_TEST").value == "2900000000008"
    next(button for button in app.button if button.label == "Genereer EAN").click().run()
    assert not app.exception
    assert app.text_input(key="label_ean_value_TEST").value == new_ean
    assert previews[-1] == new_ean
    assert any("Klik op Opslaan" in item.value for item in app.info)

    app.text_input(key="label_location_value_TEST").set_value("B-22").run()
    assert not app.exception
    assert locations[-1] == "B-22"
    assert previews[-1] == new_ean
    app.text_input(key="label_location_value_TEST").set_value("").run()
    assert not app.exception
    assert locations[-1] == ""


def test_mobile_labels_keep_print_size_and_quantity(monkeypatch):
    from app.web import label_page as page
    monkeypatch.setattr(page, "_search_all_suppliers", lambda query: [
        {"sku": "MOB-1", "supplier": "Test", "source_title": "Mobiel product"},
    ])
    monkeypatch.setattr(page, "shopify_label_values_for_sku", lambda sku: {
        "custom_location": "B-12", "ean": "2900000000008",
    })
    documents = []
    monkeypatch.setattr(page.components, "html", lambda document, **kwargs: documents.append(document))
    app = AppTest.from_string('''
from app.web.label_page import _show_mobile_labels
_show_mobile_labels()
''').run()
    assert not app.exception
    assert app.selectbox(key="mobile_label_printer").value == "Gprinter GP-1324D"
    app.text_input(key="mobile_label_search").set_value("MOB-1").run()
    assert not app.exception
    app.number_input(key="mobile_label_quantity").set_value(3).run()
    assert not app.exception
    document = documents[-1]
    assert document.count('<section class="label ') == 3
    assert "@page { size: 6in 4in; margin: 0; }" in document
    assert "Gprinter GP-1324D" in document
    assert "Locatie: B-12" in document
    assert "--preview-scale" in document
    assert "label_format" not in app.session_state
