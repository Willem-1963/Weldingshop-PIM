from app.web import label_page


def test_incidental_shopify_product_is_searchable_for_labels(monkeypatch):
    class Client:
        @classmethod
        def from_settings(cls):
            return cls()

        def graphql(self, query, variables=None):
            return {"products": {"nodes": [{
                "title": "Incidentele boormachine",
                "vendor": "Voorbeeldmerk",
                "productType": "Boormachine",
                "variants": {"nodes": [{
                    "sku": "INC-100", "barcode": "8712345678901", "price": "99.95",
                }]},
            }]}}

    monkeypatch.setattr(label_page, "list_suppliers", lambda: [])
    monkeypatch.setattr(label_page, "ShopifyClient", Client)

    matches = label_page._search_all_suppliers("INC-100")
    assert matches == [{
        "sku": "INC-100", "ean": "8712345678901",
        "source_title": "Incidentele boormachine", "sale_price": "99.95",
        "brand": "Voorbeeldmerk", "vendor": "Voorbeeldmerk",
        "product_type": "Boormachine", "supplier_slug": "",
        "supplier": "Shopify (incidenteel)",
    }]


def test_location_is_rendered_on_label():
    document = label_page.build_label_document(
        {"sku": "TEST-1", "custom_location": "A-12-03"},
        [label_page.FieldSetting("custom_location", 12, "1 regel", 1)],
        "DYMO 11354 — 57 × 32 mm",
        1,
    )

    assert "Locatie: A-12-03" in document


def test_location_save_uses_product_metafield(monkeypatch):
    calls = []

    class Client:
        @classmethod
        def from_settings(cls):
            return cls()

        def graphql(self, query, variables=None):
            calls.append((query, variables))
            if "LabelLocationOwner" in query:
                return {"productVariants": {"nodes": [{
                    "sku": "TEST-1", "product": {"id": "gid://shopify/Product/1"},
                }]}}
            return {"metafieldsSet": {
                "metafields": [{
                    "namespace": "custom", "key": "locatie", "value": "A-12-03",
                }],
                "userErrors": [],
            }}

    monkeypatch.setattr(label_page, "ShopifyClient", Client)

    assert label_page.save_shopify_location_for_sku("TEST-1", " A-12-03 ") == "A-12-03"
    metafield = calls[1][1]["metafields"][0]
    assert metafield["ownerId"] == "gid://shopify/Product/1"
    assert metafield["key"] == "locatie"


def test_empty_initial_location_does_not_disable_form_submit():
    source = label_page.__file__
    text = open(source, encoding="utf-8").read()
    submit = text[text.index("save_product_settings = st.form_submit_button"):]
    submit = submit[:submit.index(")\n        if save_product_settings:")]
    assert "disabled=" not in submit


def test_empty_location_deletes_product_metafield(monkeypatch):
    calls = []

    class Client:
        @classmethod
        def from_settings(cls):
            return cls()

        def graphql(self, query, variables=None):
            calls.append((query, variables))
            if "LabelLocationOwner" in query:
                return {"productVariants": {"nodes": [{
                    "sku": "TEST-1", "product": {"id": "gid://shopify/Product/1"},
                }]}}
            return {"metafieldsDelete": {"deletedMetafields": [{
                "ownerId": "gid://shopify/Product/1", "namespace": "custom", "key": "locatie",
            }], "userErrors": []}}

    monkeypatch.setattr(label_page, "ShopifyClient", Client)

    assert label_page.save_shopify_location_for_sku("TEST-1", "   ") == ""
    assert "metafieldsDelete" in calls[1][0]
    assert "userErrors{field message code}" not in calls[1][0]
    assert calls[1][1]["metafields"][0]["key"] == "locatie"
