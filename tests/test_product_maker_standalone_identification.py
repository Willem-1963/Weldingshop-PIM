import pytest

from app.product_maker_standalone import page
from app.product_maker_standalone.shopify import _product_handle


def test_pim_identification_uses_exact_sku_without_web_research(monkeypatch):
    supplier = {"name": "Kentie", "sync_slug": "kentie"}
    products = {
        "ABC-1": {"sku": "ABC-1", "ean": "8712345678901"},
        "ABC-10": {"sku": "ABC-10", "ean": "8712345678999"},
    }
    monkeypatch.setattr(
        page, "search_supplier_products",
        lambda slug, identifier, limit: [{"sku": sku} for sku in products],
    )
    monkeypatch.setattr(
        page, "get_supplier_product", lambda slug, sku: products.get(sku),
    )
    monkeypatch.setattr(
        page, "probe_product_page",
        lambda *args, **kwargs: pytest.fail("PIM-identificatie mag geen webpagina openen"),
    )

    assert page._pim_product_for_identifier(supplier, "SKU", "abc-1")["sku"] == "ABC-1"


def test_pim_identification_matches_normalized_ean(monkeypatch):
    supplier = {"name": "Kentie", "sync_slug": "kentie"}
    product = {"sku": "ABC-1", "ean": "8712345678901"}
    monkeypatch.setattr(
        page, "search_supplier_products",
        lambda slug, identifier, limit: [{"sku": "ABC-1"}],
    )
    monkeypatch.setattr(
        page, "get_supplier_product", lambda slug, sku: product,
    )

    assert page._pim_product_for_identifier(
        supplier, "EAN", "8 712345 678901",
    ) == product


def test_pim_identification_rejects_non_exact_match(monkeypatch):
    supplier = {"name": "Kentie", "sync_slug": "kentie"}
    monkeypatch.setattr(
        page, "search_supplier_products",
        lambda slug, identifier, limit: [{"sku": "ABC-10"}],
    )
    monkeypatch.setattr(
        page, "get_supplier_product",
        lambda slug, sku: {"sku": "ABC-10", "ean": "8712345678999"},
    )

    with pytest.raises(ValueError, match="Geen exacte SKU-match"):
        page._pim_product_for_identifier(supplier, "SKU", "ABC-1")


def test_shopify_handle_uses_supplier_name_without_vowels():
    assert _product_handle({
        "supplier_name": "Kentie", "vendor": "Kentie Apparatenfabriek B.V.",
        "sku": "998042",
    }) == "knt-998042"


def test_automatic_publish_location_prefers_supplier_configuration(monkeypatch):
    monkeypatch.setattr(
        page, "_synced_supplier_for_draft",
        lambda draft: {"shopify_location_id": "gid://shopify/Location/42"},
    )
    monkeypatch.setattr(
        page, "shopify_locations",
        lambda: [{"id": "gid://shopify/Location/42", "name": "Magazijn-Kentie"}],
    )
    assert page._automatic_publish_location({}) == "gid://shopify/Location/42"


def test_pim_identification_reuses_active_draft(monkeypatch):
    calls = {}

    class Service:
        def list_drafts(self, query):
            return [{"id": 9, "sku": "998042", "status": "active"}]

        def save_draft(self, draft_id=None, **values):
            calls["draft_id"] = draft_id
            calls["sku"] = values["sku"]
            return draft_id

        def mark_incidental(self, draft_id, incidental):
            pass

        def clear_source_material(self, draft_id, **options):
            calls["cleared"] = draft_id
            calls["preserve_manual_uploads"] = options.get("preserve_manual_uploads")

        def save_automation_settings(self, draft_id, **settings):
            pass

    monkeypatch.setattr(page, "_build_product_directly", lambda service, draft_id: None)
    result = page._create_pim_identification(
        Service(), {"id": 18, "name": "Kentie"},
        {"sku": "998042", "ean": "8719349021097", "brand": "Kentie"},
    )
    assert result == 9
    assert calls == {
        "draft_id": 9, "sku": "998042", "cleared": 9,
        "preserve_manual_uploads": True,
    }


def test_website_build_does_not_hydrate_from_supplier_pim(monkeypatch):
    calls = []
    draft = {
        "id": 9, "sku": "998044", "supplier_sync_slug": "kentie",
        "source_url": "https://www.kentie.shop/nl/product", "evidence": [],
        "tags": [], "metafields": [], "title": "Titel van website",
        "product_type": "", "category_id": "",
    }

    class Service:
        def get_draft(self, draft_id):
            return draft

        def automation_settings(self, draft_id):
            return {
                "source_research": True, "evidence_enrichment": False,
                "category_suggestion": False, "asset_collection": False,
            }

        def save_draft(self, draft_id, **values):
            draft.update(values)
            return draft_id

    monkeypatch.setattr(
        page, "inspect_official_page",
        lambda service, draft_id, url: calls.append(("website", url)) or {"url": url},
    )
    monkeypatch.setattr(
        page, "_hydrate_from_supplier_sync",
        lambda *args: pytest.fail("Website-opbouw mag de leveranciers-PIM niet laden"),
    )

    page._build_product_directly(Service(), 9)
    assert calls == [("website", "https://www.kentie.shop/nl/product")]
