import pytest

from app.shopify.sync import (
    _input, _grouped_product_rows, _preserve_vp_shopify_fields, _vp_metafield_rows,
    COLLECTION_CUSTOM_METAFIELD_KEYS,
)
from app.suppliers import complementary_products


def product(slug="valkenpower", sku="VP-TEST"):
    return {
        "_supplier_slug": slug, "sku": sku, "vendor": "Valkenpower",
        "source_title": "Gereedschap", "price": 25, "cost_price": 10,
        "available": True, "images": [], "ai_tags_json": '["PIM-tag"]',
    }


def existing(sku="VP-TEST"):
    return {
        "product": {"id": "gid://shopify/Product/1", "handle": "gereedschap",
                    "tags": ["Mijn tag"]},
        "variant": {"id": "gid://shopify/ProductVariant/1", "sku": sku},
    }


def test_existing_vp_omits_tags_and_metafields_but_updates_price():
    result = _input(product(), existing())
    assert "tags" not in result
    assert "metafields" not in result
    assert "metafields" not in result["variants"][0]
    assert result["variants"][0]["price"] == "25.00"


@pytest.mark.parametrize("value", ["", "Afgeleide VP-categorie"])
def test_existing_vp_taxonomy_and_custom_tree_are_never_sent(value):
    keys = ["productgroep", "productsoort", "uitvoering", "filter",
            "subcategorie_3", "subcategorie_4", "subcategorie_5",
            "aandrijving", "model", "capaciteit"]
    payload = {
        "category": "gid://shopify/TaxonomyCategory/ha",
        "metafields": [{"namespace": "custom", "key": key, "value": value}
                       for key in keys],
        "variants": [{"id": "variant-1", "price": "25.00"}],
    }
    _preserve_vp_shopify_fields(product(), existing(), payload)
    assert "category" not in payload
    assert "metafields" not in payload
    assert payload["variants"][0]["price"] == "25.00"


@pytest.mark.parametrize("slug,match", [("valkenpower", None), ("kentie", None)])
def test_new_products_keep_initial_fields(slug, match):
    result = _input(product(slug), match)
    assert "tags" in result
    assert "metafields" in result


def test_existing_vp_family_preserves_fields_for_every_variant():
    products = [product(sku="VP-A"), product(sku="VP-B")]
    matches = {p["sku"]: existing(p["sku"]) for p in products}
    matches["VP-B"]["variant"]["id"] = "gid://shopify/ProductVariant/2"
    rows = _grouped_product_rows(products, matches, group_variants=False)
    assert len(rows) == 1
    result = rows[0]["input"]
    assert "tags" not in result
    assert "metafields" not in result
    assert len(result["variants"]) == 2
    assert all("metafields" not in v for v in result["variants"])


def test_vp_recommendations_do_not_access_shopify(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("VP recommendations must remain untouched")

    monkeypatch.setattr(complementary_products.ShopifyClient, "from_settings", unexpected)
    monkeypatch.setattr(complementary_products, "import_shopify_complementary_products", unexpected)
    assert complementary_products.synchronize_complementary_products("valkenpower")["skipped"]


@pytest.mark.parametrize("value", ["", "Afgeleid uit VP"])
def test_commercial_metafields_use_upserts_without_tree_fields(value):
    p = product()
    p["_raw_data"] = {"source": value, "table": "Voorraadtabel", "dimensions": "10 x 20 cm"}
    mappings = [("custom", key, "source") for key in COLLECTION_CUSTOM_METAFIELD_KEYS]
    mappings += [("custom", "voorraad_tabel", "table"), ("pim", "afmetingen", "dimensions")]
    p["_shopify_metafield_mapping"] = {
        f"{ns}.{key}": {"owner": "product", "namespace": ns, "key": key,
                        "type": "single_line_text_field", "source_field": source}
        for ns, key, source in mappings
    }
    p["_delivery_time_notice"] = "Levertijd kan afwijken"
    rows = _vp_metafield_rows([p], {p["sku"]: existing()})
    fields = [field for row in rows for field in row["metafields"]]
    assert all(not (f["namespace"] == "custom" and f["key"] in COLLECTION_CUSTOM_METAFIELD_KEYS)
               for f in fields)
    assert any(f["namespace"] == "custom" and f["key"] == "voorraad_tabel" for f in fields)
    assert any(f["namespace"] == "pim" and f["key"] == "afmetingen" for f in fields)
    assert any(f["key"] == "verwachte_product_levertijd" and "Levertijd" in f["value"] for f in fields)
    assert all(f["ownerId"] == existing()["product"]["id"] for f in fields)
    assert "metafields" not in _input(p, existing())


def test_existing_lock_does_not_authorize_automatic_tree_writes():
    p = product()
    p["_raw_data"] = {"category_file_import": {"locked": True, "values": {
        "product_group_name": "Goedgekeurd", "execution": "",
        "collection_tags": ["cat-goedgekeurd"],
        "category": "gid://shopify/TaxonomyCategory/ha",
    }}}
    payload = _input(p, existing())
    assert "tags" not in payload
    assert "category" not in payload
    assert "metafields" not in payload
    fields = [f for row in _vp_metafield_rows([p], {p["sku"]: existing()}) for f in row["metafields"]]
    tree = {f["key"]: f["value"] for f in fields
            if f["namespace"] == "custom" and f["key"] in COLLECTION_CUSTOM_METAFIELD_KEYS}
    assert tree == {}
    # Approval must be explicit for this operation, never inherited by sync.
    _preserve_vp_shopify_fields(p, existing(), payload, allow_approved_tree=True)
    assert payload["tags"] == ["cat-goedgekeurd"]
    assert payload["category"] == "gid://shopify/TaxonomyCategory/ha"


def test_lock_without_tags_or_category_preserves_shopify_tags_and_category():
    p = product()
    p["_raw_data"] = {"category_file_import": {"locked": True, "values": {
        "product_group_name": "Goedgekeurd",
    }}}
    payload = _input(p, existing())
    assert "tags" not in payload
    assert "category" not in payload


def test_normal_vp_sync_never_replaces_merchant_managed_tree():
    """VP tags/category/custom tree are merchant-managed without an approved lock."""
    p = product()
    p["_raw_data"] = {"derived": "VP-bronwaarde"}
    p["_shopify_metafield_mapping"] = {
        key: {"owner": "product", "namespace": "custom", "key": key,
              "type": "single_line_text_field", "source_field": "derived"}
        for key in COLLECTION_CUSTOM_METAFIELD_KEYS
    }
    match = existing()
    rows = _grouped_product_rows([p], {p["sku"]: match}, group_variants=False)
    assert all("tags" not in row["input"] and "category" not in row["input"]
               and "metafields" not in row["input"] for row in rows)
    assert not any(
        field["namespace"] == "custom" and field["key"] in COLLECTION_CUSTOM_METAFIELD_KEYS
        for row in _vp_metafield_rows([p], {p["sku"]: match})
        for field in row["metafields"]
    )


def test_commercial_metafields_are_batched_with_correct_variant_owner():
    p = product()
    p["_raw_data"] = {"source": "waarde"}
    p["_shopify_metafield_mapping"] = {
        str(i): {"owner": "product", "namespace": "pim", "key": f"spec_{i}",
                 "type": "single_line_text_field", "source_field": "source"}
        for i in range(26)
    }
    p["_shopify_metafield_mapping"]["variant"] = {
        "owner": "variant", "namespace": "pim", "key": "variant_spec",
        "type": "single_line_text_field", "source_field": "source",
    }
    rows = _vp_metafield_rows([p], {p["sku"]: existing()})
    assert all(len(row["metafields"]) <= 25 for row in rows)
    fields = [f for row in rows for f in row["metafields"]]
    assert len(fields) == 27
    assert next(f for f in fields if f["key"] == "variant_spec")["ownerId"] == existing()["variant"]["id"]


def test_product_maker_cannot_replace_existing_vp_tree():
    from app.product_maker_standalone.shopify import _protect_existing_vp_tree
    payload = {
        "tags": ["Fout"], "category": "Fout", "title": "Nieuwe titel",
        "metafields": [{"namespace": "custom", "key": key, "value": "Fout"}
                       for key in COLLECTION_CUSTOM_METAFIELD_KEYS]
        + [{"namespace": "pim", "key": "afmetingen", "value": "10 cm"}],
    }
    allowed = _protect_existing_vp_tree("VP-TEST", existing(), payload)
    assert "tags" not in payload and "category" not in payload and "metafields" not in payload
    assert payload["title"] == "Nieuwe titel"
    assert allowed == [{"namespace": "pim", "key": "afmetingen", "value": "10 cm"}]
