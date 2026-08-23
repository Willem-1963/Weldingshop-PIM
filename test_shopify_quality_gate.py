import pytest

from app.shopify.sync import _validate_product_content_gate


def product(sku: str, *, price: float = 10.0, family: str = "family") -> dict:
    return {
        "sku": sku,
        "sale_price": price,
        "images": [{"image_url": "https://example.test/product.jpg"}],
        "html_description": "<h2>Product</h2>" + ("Volledige tekst " * 30),
        "ai_tags_json": '["Merk","Productgroep","Toepassing"]',
        "_family_variant_options": {"family_key": family},
        "_raw_data": {},
    }


def test_quality_gate_blocks_zero_price():
    item = product("SKU-1", price=0)
    with pytest.raises(ValueError, match="verkoopprijs ontbreekt of is nul"):
        _validate_product_content_gate([item], [item])


def test_quality_gate_blocks_incomplete_family():
    first = product("SKU-1")
    second = product("SKU-2")
    with pytest.raises(ValueError, match="varianten ontbreken: SKU-2"):
        _validate_product_content_gate([first], [first, second])


def test_quality_gate_ignores_unpriced_family_member():
    first = product("SKU-1")
    unpriced = product("SKU-2", price=0)
    _validate_product_content_gate([first], [first, unpriced])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("images", [], "minder dan 1 goedgekeurde productafbeelding"),
        ("html_description", "kort", "producttekst ontbreekt"),
        ("ai_tags_json", '["Merk"]', "minder dan 3"),
    ],
)
def test_quality_gate_blocks_missing_content(field, value, message):
    item = product("SKU-1")
    item[field] = value
    with pytest.raises(ValueError, match=message):
        _validate_product_content_gate([item], [item])


def test_quality_gate_blocks_missing_required_position_icon():
    item = product("SKU-1")
    item["vendor"] = "Certilas"
    item["_raw_data"] = {
        "website_enrichment": {"facts": {
            "welding_positions": ["Alle posities", "PA"],
            "welding_position_images": {"PA": "https://example.test/pa.png"},
        }}
    }
    with pytest.raises(ValueError, match="laspositie-iconen ontbreken: PA"):
        _validate_product_content_gate([item], [item])


def test_quality_gate_does_not_require_icons_for_position_labels():
    item = product("SKU-1")
    item["_raw_data"] = {
        "website_enrichment": {"facts": {
            "welding_positions": ["Alle posities", "Verticaal opgaand"],
            "welding_position_images": {},
        }}
    }
    _validate_product_content_gate([item], [item])


def test_quality_gate_accepts_complete_family():
    first = product("SKU-1")
    second = product("SKU-2")
    _validate_product_content_gate([first, second], [first, second])


def test_quality_gate_uses_sp_tools_supplier_specific_content_fallback():
    item = product("SKU-1")
    item["html_description"] = ""
    item["source_description"] = "kort"
    item["vendor"] = "SP Tools"
    _validate_product_content_gate([item], [item])


def test_quality_gate_accepts_two_relevant_sp_tools_tags():
    item = product("BIT-H2")
    item["vendor"] = "SP Tools"
    item["ai_tags_json"] = '["SP Tools", "Bits"]'
    _validate_product_content_gate([item], [item])


def test_quality_gate_does_not_apply_sp_tools_fallback_to_other_supplier():
    item = product("SKU-1")
    item["html_description"] = ""
    item["source_description"] = "kort"
    item["vendor"] = "Andere leverancier"
    with pytest.raises(ValueError, match="producttekst ontbreekt"):
        _validate_product_content_gate([item], [item])


def test_quality_gate_blocks_certilas_filter_contract_violation():
    item = product("CERTILAS-1")
    item["vendor"] = "Certilas"
    item["filter_values_json"] = '["Lasproces: MIG", "Maat: 1,2 mm"]'
    with pytest.raises(ValueError, match="filtercontract vereist exact"):
        _validate_product_content_gate([item], [item])


def test_quality_gate_accepts_fixed_certilas_filters():
    item = product("CERTILAS-1")
    item["vendor"] = "Certilas"
    item["filter_values_json"] = '["Lasproces: MIG", "Materiaal: Koper"]'
    _validate_product_content_gate([item], [item])
