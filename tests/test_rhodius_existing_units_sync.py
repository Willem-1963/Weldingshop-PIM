import pytest

from app.shopify import sync
from app.shopify.rhodius_sync import plan_existing_units


def product(price_unit="€/stuk", **raw):
    return {"sku": "200140", "_supplier_slug": "rhodius-abrasives-gmbh",
            "cost_price": "2.35", "sale_price": 9,
            "_raw_data": {"VE": 10, "Prijseenheid": price_unit,
                          "GTIN-code": "piece-code", "GTIN/verpakking": "pack-code", **raw}}


def existing():
    return {sku: {"product": {"id": "p1"}, "variant": {"id": sku, "price": "99"}}
            for sku in ("200140", "200140-STUK", "200140-STOCK", "OTHER")}


def test_package_piece_and_stock_keep_separate_roles():
    plan = plan_existing_units([product()], existing())
    variants = {v["id"]: v for row in plan["rows"] for v in row["variants"]}
    assert variants == {
        "200140": {"id": "200140", "barcode": "pack-code", "inventoryItem": {"cost": "23.50"}},
        "200140-STUK": {"id": "200140-STUK", "barcode": "piece-code", "inventoryItem": {"cost": "2.35"}},
    }
    assert plan["protected_stock_skus"] == ["200140-STOCK"]
    assert all(set(v) <= {"id", "barcode", "inventoryItem"} for v in variants.values())


def test_package_pricelist_cost_is_not_multiplied_twice():
    variants = plan_existing_units([product("€/pak")], existing())["rows"][0]["variants"]
    assert variants[0]["inventoryItem"]["cost"] == "2.35"
    assert variants[1]["inventoryItem"]["cost"] == "0.24"


def test_missing_barcode_keeps_cost_update_without_clearing_existing_code():
    plan = plan_existing_units([product(**{"GTIN/verpakking": None})], existing())
    assert plan["rows"][0]["variants"][0] == {"id": "200140", "inventoryItem": {"cost": "23.50"}}
    assert plan["warnings"][0]["sku"] == "200140"


def test_missing_cost_does_not_erase_existing_cost():
    p = product(); p["cost_price"] = None
    assert all("inventoryItem" not in v for v in plan_existing_units([p], existing())["rows"][0]["variants"])


def test_no_new_units_or_stock_writes_and_no_website_barcode_fallback():
    p = product(**{"GTIN/verpakking": None, "website_import": {
        "verification": "official_page", "technical_specifications": {
            "inhoud": "10", "gtin_code_verpakkingseenheid": "website-code"}}})
    matches = existing(); matches.pop("200140-STUK")
    plan = plan_existing_units([p], matches)
    assert plan["missing_units"] == ["200140-STUK"]
    assert "barcode" not in plan["rows"][0]["variants"][0]


@pytest.mark.parametrize("cost", [-1, "nan", "inf", "bad"])
def test_invalid_cost_blocks_unit_without_touching_prices(cost):
    p = product(); p["cost_price"] = cost
    assert not plan_existing_units([p], existing())["rows"]


def test_stock_source_rows_cannot_be_treated_as_packages():
    p = product(); p["sku"] = "200140-STOCK"
    assert not plan_existing_units([p], existing())["rows"]


def test_normal_sync_bypasses_productset_and_inventory_cleanup(monkeypatch):
    monkeypatch.setattr(sync, "get_shopify_settings", lambda: {"enabled": True})
    monkeypatch.setattr(sync, "get_supplier", lambda slug: {"name": "Rhodius"})
    monkeypatch.setattr(sync, "_source_products", lambda slug: [product()])
    monkeypatch.setattr(sync.ShopifyClient, "from_settings", lambda: object())
    monkeypatch.setattr(sync, "_shopify_products", lambda *a, **kw: existing())
    def forbidden(*a, **kw):
        raise AssertionError("Structure/inventory must not be written")
    monkeypatch.setattr(sync, "_grouped_product_rows", forbidden)
    monkeypatch.setattr(sync, "_apply_collection_inventory_policies", forbidden)
    monkeypatch.setattr(sync, "_missing_products", forbidden)
    calls = []
    monkeypatch.setattr(sync, "_run_rows", lambda client, rows, mutation, *a: calls.append((rows, mutation)))
    result = sync.sync_all_products("rhodius-abrasives-gmbh")
    assert result["variants"] == 2
    assert len(calls) == 1
    assert "productVariantsBulkUpdate" in calls[0][1]
    assert "productSet" not in calls[0][1]
    assert result["sales_prices_changed"] == result["inventory_quantities_changed"] == 0
