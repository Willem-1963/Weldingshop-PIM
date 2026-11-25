from app.shopify.sync import _price_only_variant_rows


def test_price_only_cost_uses_commercial_half_up_rounding():
    products = [{"sku": "916998", "sale_price": 124.97, "cost_price": 62.485}]
    existing = {"916998": {
        "product": {"id": "product"},
        "variant": {"id": "variant"},
    }}

    rows = _price_only_variant_rows(products, existing)

    assert rows[0]["variants"][0]["inventoryItem"]["cost"] == "62.49"
