import pytest

from app.shopify import sync
from app.suppliers.routes import ROUTES
from app.product_maker_standalone.shopify import _protect_existing_tree


@pytest.mark.parametrize("slug", [*ROUTES, "future-supplier"])
def test_every_supplier_preserves_existing_tree_and_upserts_commercial_fields(slug):
    product = {
        "sku": "TEST-1", "_supplier_slug": slug, "price": 25, "images": [],
        "source_title": "Artikel", "ai_tags_json": '["Afgeleide tag"]',
        "_raw_data": {"source": "Bronwaarde", "category_file_import": {
            "locked": True, "values": {"product_group_name": "Oude import",
                                       "collection_tags": ["cat-oude-import"]}}},
        "_shopify_metafield_mapping": {
            key: {"owner": "product", "namespace": "custom", "key": key,
                  "type": "single_line_text_field", "source_field": "source"}
            for key in sync.COLLECTION_CUSTOM_METAFIELD_KEYS
        },
    }
    product["_shopify_metafield_mapping"]["spec"] = {
        "owner": "product", "namespace": "pim", "key": "specificaties",
        "type": "single_line_text_field", "source_field": "source",
    }
    if slug == "rhodius-abrasives-gmbh":
        product["_raw_data"].update({"VE": 1, "GTIN-code": "123", "GTIN/verpakking": "123",
                                     "Gewicht in kg/stuk": 0.1, "Prijseenheid": "€/stuk"})
    existing = {"TEST-1": {
        "product": {"id": "product-1", "handle": "artikel", "tags": ["Mijn structuur"]},
        "variant": {"id": "variant-1", "sku": "TEST-1"},
    }}
    updates = []
    rows = sync._grouped_product_rows([product], existing, group_variants=False,
                                     metafield_updates=updates)
    payload = rows[0]["input"]
    assert not any(key in payload for key in ("tags", "category", "metafields"))
    assert payload["variants"][0]["price"] == "25.00"
    fields = [f for row in sync._metafield_upsert_rows(updates) for f in row["metafields"]]
    assert any(f["namespace"] == "pim" and f["key"] == "specificaties" for f in fields)
    assert not any(f["namespace"] == "custom" and f["key"] in sync.COLLECTION_CUSTOM_METAFIELD_KEYS
                   for f in fields)
    sync._assert_supplier_tree_safe(rows, "productSet")
    sync._assert_supplier_tree_safe(sync._metafield_upsert_rows(updates), "metafieldsSet")


@pytest.mark.parametrize("key", sorted(sync.COLLECTION_CUSTOM_METAFIELD_KEYS))
@pytest.mark.parametrize("mutation", ["metafieldsSet", "metafieldsDelete"])
def test_bulk_guard_blocks_tree_writes_and_deletes_before_upload(key, mutation, monkeypatch):
    def unexpected(*args):
        raise AssertionError("No unsafe mutation may reach Shopify")
    monkeypatch.setattr(sync, "_stage", unexpected)
    row = {"metafields": [{"ownerId": "product-1", "namespace": "custom", "key": key}]}
    with pytest.raises(ValueError, match="boomvelden"):
        sync._run_rows(None, [row], mutation, "test.jsonl", "test", None, 0, 100, "test")


@pytest.mark.parametrize("field", ["tags", "category", "metafields"])
def test_bulk_guard_rejects_replacement_of_existing_tree(field):
    with pytest.raises(ValueError, match="boomlaag"):
        sync._assert_supplier_tree_safe([{"input": {"id": "product-1", field: []}}], "productSet")


def test_product_maker_protects_non_vp_products_too():
    payload = {"tags": [], "category": "ha", "metafields": [
        {"namespace": "custom", "key": "filter", "value": "Afgeleid"},
        {"namespace": "pim", "key": "afmetingen", "value": "10 cm"},
    ]}
    allowed = _protect_existing_tree("KNT-1", {"product": {"id": "product-1"}}, payload)
    assert not payload
    assert allowed == [{"namespace": "pim", "key": "afmetingen", "value": "10 cm"}]


def test_certilas_family_keeps_bundle_and_combined_packaging_fields():
    products = []
    matches = {}
    for number, code in enumerate(("D-100", "D-200"), start=1):
        sku = f"CERT-{number}"
        products.append({
            "sku": sku, "_supplier_slug": "certilas", "sale_price": 10,
            "source_title": "Lasdraad", "images": [],
            "_raw_data": {"KG Ceweld Unit": "2,4", "KG Ceweld Bundle": "14,4"},
            "_family_variant_options": {
                "family_key": "wire", "diameter": "1,0 mm", "packaging": code,
                "packaging_code": code, "packaging_name": "Spoel", "package_weight_label": "2,4 kg",
            },
        })
        matches[sku] = {"product": {"id": "product-1", "handle": "wire"},
                        "variant": {"id": f"variant-{number}", "sku": sku}}
    updates = []
    rows = sync._grouped_product_rows(products, matches, group_variants=True,
                                     metafield_updates=updates)
    assert len(rows) == 1 and "metafields" not in rows[0]["input"]
    assert all("metafields" not in v for v in rows[0]["input"]["variants"])
    fields = {(f["ownerId"], f["key"]): f["value"]
              for row in sync._metafield_upsert_rows(updates) for f in row["metafields"]}
    explanation = fields[("product-1", "verpakkingsopmerking")]
    assert "D-100" in explanation and "D-200" in explanation
    assert ("variant-1", "verplichte_bundel") in fields
    assert ("variant-2", "verplichte_bundel") in fields


def test_certilas_bundle_cleanup_never_deletes_tree_fields():
    match = {"product": {"id": "p1", "metafields": {"nodes": [
        {"key": key} for key in sync.COLLECTION_CUSTOM_METAFIELD_KEYS]}},
        "variant": {"id": "v1", "metafields": {"nodes": [
        {"key": key} for key in sync.COLLECTION_CUSTOM_METAFIELD_KEYS]}}}
    assert sync._certilas_stale_bundle_metafield_identifiers(
        [{"sku": "CERT-1", "_supplier_slug": "certilas", "_raw_data": {}}],
        {"CERT-1": match},
    ) == []
