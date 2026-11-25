import json
from io import BytesIO

import pytest
import zxingcpp
from PIL import Image
from streamlit.testing.v1 import AppTest

from app.shopify.mobile_stock_count import (
    StockCountTarget,
    add_barcode_to_variant,
    calculate_component_quantity,
    decode_barcode_image,
    lookup_barcode_link_candidate,
    resolve_barcode_stock_target,
    set_counted_stock,
)


LOCATION_ID = "gid://shopify/Location/1"


def level(quantity):
    return {
        "id": "gid://shopify/InventoryItem/1",
        "tracked": True,
        "inventoryLevel": {
            "quantities": [{"name": "available", "quantity": quantity}],
        },
    }


class ResolveClient:
    def __init__(self, variants):
        self.variants = variants

    def shop_and_locations(self):
        return {"locations": {"nodes": [{"id": LOCATION_ID, "name": "Weldingshop"}]}}

    def graphql(self, query, variables=None):
        assert "MobileStockCountByBarcode" in query
        return {"productVariants": {"nodes": self.variants}}


def product(vendor="Rhodius", status="ACTIVE"):
    return {
        "id": "gid://shopify/Product/1",
        "handle": "rhodius-test",
        "title": "Rhodius Testschijf",
        "status": status,
        "vendor": vendor,
        "media": {"nodes": [{"image": {"url": "https://cdn.example/test.jpg"}}]},
    }


def test_bundle_barcode_resolves_to_stock_component():
    component_inventory = level(82)
    component_inventory["id"] = "gid://shopify/InventoryItem/stock"
    variant = {
        "id": "gid://shopify/ProductVariant/parent",
        "sku": "303348",
        "barcode": "4011890123456",
        "title": "Verpakking (25)",
        "requiresComponents": True,
        "product": product(),
        "inventoryItem": level(3),
        "productVariantComponents": {"nodes": [{
            "quantity": 25,
            "productVariant": {
                "id": "gid://shopify/ProductVariant/stock",
                "sku": "STOCK-303348",
                "title": "Default Title",
                "inventoryItem": component_inventory,
            },
        }]},
    }

    target = resolve_barcode_stock_target(
        "4011890123456", client=ResolveClient([variant]),
    )

    assert target.scanned_sku == "303348"
    assert target.target_sku == "STOCK-303348"
    assert target.units_per_scan == 25
    assert target.target_inventory_quantity == 82
    assert target.stock_model == "component_bundle"
    assert target.product_image_url == "https://cdn.example/test.jpg"


def test_piece_barcode_bundle_resolves_with_factor_one():
    component_inventory = level(19)
    variant = {
        "id": "gid://shopify/ProductVariant/piece",
        "sku": "303348-STUK",
        "barcode": "4011890654321",
        "title": "Stuk",
        "requiresComponents": True,
        "product": product(),
        "inventoryItem": level(19),
        "productVariantComponents": {"nodes": [{
            "quantity": 1,
            "productVariant": {
                "id": "gid://shopify/ProductVariant/stock",
                "sku": "STOCK-303348",
                "title": "Default Title",
                "inventoryItem": component_inventory,
            },
        }]},
    }

    target = resolve_barcode_stock_target(
        "4011890654321", client=ResolveClient([variant]),
    )

    assert target.target_sku == "STOCK-303348"
    assert target.units_per_scan == 1
    assert target.target_inventory_quantity == 19


def test_direct_vpe_one_product_counts_own_inventory():
    variant = {
        "id": "gid://shopify/ProductVariant/direct",
        "sku": "353011",
        "barcode": "4011890072974",
        "title": "Default Title",
        "requiresComponents": False,
        "product": product(),
        "inventoryItem": level(4),
        "productVariantComponents": {"nodes": []},
    }

    target = resolve_barcode_stock_target(
        "4011890072974", client=ResolveClient([variant]),
    )

    assert target.target_sku == "353011"
    assert target.units_per_scan == 1
    assert target.stock_model == "direct"


def test_duplicate_barcode_is_blocked():
    base = {
        "id": "gid://shopify/ProductVariant/1",
        "sku": "ONE",
        "barcode": "4011890000000",
        "title": "Default",
        "requiresComponents": False,
        "product": product(),
        "inventoryItem": level(1),
        "productVariantComponents": {"nodes": []},
    }
    duplicate = {**base, "id": "gid://shopify/ProductVariant/2", "sku": "TWO"}

    with pytest.raises(ValueError, match="meerdere varianten"):
        resolve_barcode_stock_target(
            "4011890000000", client=ResolveClient([base, duplicate]),
        )


def test_non_rhodius_direct_product_counts_weldingshop_inventory():
    variant = {
        "id": "gid://shopify/ProductVariant/1",
        "sku": "VP-1",
        "barcode": "8712345678901",
        "title": "Default",
        "requiresComponents": False,
        "product": product("Valkenpower"),
        "inventoryItem": level(1),
        "productVariantComponents": {"nodes": []},
    }
    target = resolve_barcode_stock_target(
        "8712345678901", client=ResolveClient([variant]),
    )
    assert target.product_vendor == "Valkenpower"
    assert target.target_sku == "VP-1"
    assert target.units_per_scan == 1
    assert target.target_inventory_quantity == 1


def test_inactive_product_is_blocked():
    variant = {
        "id": "gid://shopify/ProductVariant/1",
        "sku": "DRAFT-1",
        "barcode": "8712345678901",
        "title": "Default",
        "requiresComponents": False,
        "product": product("Valkenpower", "DRAFT"),
        "inventoryItem": level(1),
        "productVariantComponents": {"nodes": []},
    }
    with pytest.raises(ValueError, match="niet actief"):
        resolve_barcode_stock_target(
            "8712345678901", client=ResolveClient([variant]),
        )


class BarcodeLinkClient:
    def __init__(self, *, existing_barcode="", collision=False):
        self.current_barcode = existing_barcode
        self.collision = collision

    def shop_and_locations(self):
        return {"locations": {"nodes": [{"id": LOCATION_ID, "name": "Weldingshop"}]}}

    def variant(self):
        stock_inventory = level(13)
        stock_inventory["id"] = "gid://shopify/InventoryItem/stock"
        return {
            "id": "gid://shopify/ProductVariant/package",
            "sku": "304003",
            "barcode": self.current_barcode,
            "title": "125 mm / 40 / Verpakking (25)",
            "requiresComponents": True,
            "image": None,
            "product": product(),
            "inventoryItem": level(0),
            "productVariantComponents": {"nodes": [{
                "quantity": 25,
                "productVariant": {
                    "id": "gid://shopify/ProductVariant/stock",
                    "sku": "STOCK-304003",
                    "title": "Default Title",
                    "inventoryItem": stock_inventory,
                },
            }]},
        }

    def graphql(self, query, variables=None):
        if "MobileStockBarcodeLinkCandidate" in query:
            collisions = ([{
                "id": "gid://shopify/ProductVariant/other",
                "sku": "OTHER",
                "barcode": "4011890115435",
            }] if self.collision else [])
            return {
                "barcodeMatches": {"nodes": collisions},
                "skuMatches": {"nodes": [self.variant()]},
            }
        if "MobileStockAddBarcode" in query:
            self.current_barcode = variables["variants"][0]["barcode"]
            return {"productVariantsBulkUpdate": {
                "productVariants": [{
                    "id": "gid://shopify/ProductVariant/package",
                    "sku": "304003",
                    "barcode": self.current_barcode,
                }],
                "userErrors": [],
            }}
        if "MobileStockBarcodeVariantReadback" in query:
            return {"node": self.variant()}
        if "MobileStockCountByBarcode" in query:
            return {"productVariants": {"nodes": [self.variant()]}}
        raise AssertionError(query)


def test_unknown_barcode_can_be_linked_to_empty_bundle_variant(tmp_path):
    client = BarcodeLinkClient()
    candidate = lookup_barcode_link_candidate(
        "4011890115435", "304003", client=client,
    )
    assert candidate.variant_sku == "304003"
    assert candidate.target_sku == "STOCK-304003"
    assert candidate.units_per_scan == 25

    target = add_barcode_to_variant(
        candidate, client=client, audit_path=tmp_path / "audit.jsonl",
    )
    assert target.barcode == "4011890115435"
    assert target.scanned_sku == "304003"
    assert target.target_sku == "STOCK-304003"
    assert target.units_per_scan == 25
    audit = json.loads((tmp_path / "audit.jsonl").read_text().strip())
    assert audit["action"] == "barcode_link"
    assert audit["status"] == "ok"


def test_barcode_link_blocks_invalid_or_used_gtin():
    with pytest.raises(ValueError, match="geldige GTIN"):
        lookup_barcode_link_candidate("123", "304003", client=BarcodeLinkClient())
    with pytest.raises(ValueError, match="al gekoppeld"):
        lookup_barcode_link_candidate(
            "4011890115435", "304003", client=BarcodeLinkClient(collision=True),
        )


def test_existing_barcode_requires_explicit_replace(tmp_path):
    client = BarcodeLinkClient(existing_barcode="4011890115428")
    with pytest.raises(ValueError, match="wordt niet overschreven"):
        lookup_barcode_link_candidate(
            "4011890115435", "304003", client=client,
        )

    candidate = lookup_barcode_link_candidate(
        "4011890115435", "304003", client=client, allow_replace=True,
    )
    assert candidate.current_barcode == "4011890115428"
    with pytest.raises(ValueError, match="bevestig expliciet"):
        add_barcode_to_variant(
            candidate, client=client, audit_path=tmp_path / "blocked.jsonl",
        )

    target = add_barcode_to_variant(
        candidate,
        client=client,
        audit_path=tmp_path / "audit.jsonl",
        replace_existing=True,
    )
    assert target.barcode == "4011890115435"
    assert client.current_barcode == "4011890115435"
    audit = json.loads((tmp_path / "audit.jsonl").read_text().strip())
    assert audit["action"] == "barcode_replace"
    assert audit["candidate"]["current_barcode"] == "4011890115428"


def test_barcode_replace_blocks_when_current_barcode_changed(tmp_path):
    client = BarcodeLinkClient(existing_barcode="4011890115428")
    candidate = lookup_barcode_link_candidate(
        "4011890115435", "304003", client=client, allow_replace=True,
    )
    client.current_barcode = "8712345678901"
    with pytest.raises(RuntimeError, match="wijzigde sinds de controle"):
        add_barcode_to_variant(
            candidate,
            client=client,
            audit_path=tmp_path / "audit.jsonl",
            replace_existing=True,
        )


def test_quantity_calculation():
    assert calculate_component_quantity(
        counted_units=3, units_per_scan=25, loose_units=7,
    ) == 82
    assert calculate_component_quantity(
        counted_units=19, units_per_scan=1,
    ) == 19
    with pytest.raises(ValueError, match="dubbel"):
        calculate_component_quantity(
            counted_units=19, units_per_scan=1, loose_units=1,
        )


def test_phone_camera_image_decodes_ean13():
    expected = "4011890070857"
    barcode = zxingcpp.create_barcode(expected, zxingcpp.BarcodeFormat.EAN13)
    image = Image.fromarray(barcode.to_image(scale=4))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    assert decode_barcode_image(buffer) == expected


def test_phone_camera_decodes_small_barcode_inside_large_photo():
    expected = "4011890115435"
    barcode = zxingcpp.create_barcode(expected, zxingcpp.BarcodeFormat.EAN13)
    small = Image.fromarray(barcode.to_image(scale=1)).convert("RGB")
    canvas = Image.new("RGB", (1800, 1200), "#dedbd0")
    canvas.paste(small, (1180, 760))
    buffer = BytesIO()
    canvas.save(buffer, format="JPEG", quality=72)
    buffer.seek(0)

    assert decode_barcode_image(buffer) == expected


def test_mobile_stock_page_renders_live_scanner_and_fallback():
    app = AppTest.from_string('''
from unittest.mock import patch
from app.web import label_page
with patch.object(label_page, "live_barcode_scanner", return_value=None):
    label_page._show_mobile_stock_count()
''').run()
    assert not app.exception
    assert app.expander[0].label == "Foto of handmatige invoer gebruiken"


def test_set_counted_stock_uses_compare_and_set_and_writes_audit(tmp_path):
    calls = []

    class Client:
        def graphql(self, query, variables=None):
            calls.append((query, variables))
            if "SaveMobileStockCount" in query:
                return {"inventorySetQuantities": {"userErrors": []}}
            assert "MobileStockCountReadback" in query
            return {"node": {
                "id": "gid://shopify/ProductVariant/stock",
                "sku": "STOCK-303348",
                "inventoryItem": level(82),
            }}

    target = StockCountTarget(
        barcode="4011890123456",
        product_id="gid://shopify/Product/1",
        product_handle="rhodius-test",
        product_title="Rhodius Testschijf",
        product_status="ACTIVE",
        product_vendor="Rhodius",
        product_image_url="",
        scanned_variant_id="gid://shopify/ProductVariant/parent",
        scanned_sku="303348",
        scanned_title="Verpakking (25)",
        units_per_scan=25,
        target_variant_id="gid://shopify/ProductVariant/stock",
        target_sku="STOCK-303348",
        target_inventory_item_id="gid://shopify/InventoryItem/stock",
        target_inventory_quantity=75,
        shopify_location_id=LOCATION_ID,
        shopify_location_name="Weldingshop",
        stock_model="component_bundle",
    )
    audit_path = tmp_path / "audit.jsonl"

    result = set_counted_stock(
        target,
        counted_units=3,
        loose_units=7,
        client=Client(),
        audit_path=audit_path,
    )

    quantity = calls[0][1]["input"]["quantities"][0]
    assert quantity["changeFromQuantity"] == 75
    assert quantity["quantity"] == 82
    assert result["confirmed_quantity"] == 82
    audit = json.loads(audit_path.read_text().strip())
    assert audit["status"] == "ok"
    assert audit["mode"] == "total"
    assert audit["target"]["target_sku"] == "STOCK-303348"


def test_add_counted_stock_adds_multiple_packages_and_loose_units(tmp_path):
    calls = []

    class Client:
        def graphql(self, query, variables=None):
            calls.append((query, variables))
            if "SaveMobileStockCount" in query:
                return {"inventorySetQuantities": {"userErrors": []}}
            assert "MobileStockCountReadback" in query
            return {"node": {
                "id": "gid://shopify/ProductVariant/stock",
                "sku": "STOCK-304003",
                "inventoryItem": level(118),
            }}

    target = StockCountTarget(
        barcode="4011890115435",
        product_id="gid://shopify/Product/1",
        product_handle="rhodius-kfz",
        product_title="Rhodius KFZ",
        product_status="ACTIVE",
        product_vendor="Rhodius",
        product_image_url="",
        scanned_variant_id="gid://shopify/ProductVariant/package",
        scanned_sku="304003",
        scanned_title="Verpakking (25)",
        units_per_scan=25,
        target_variant_id="gid://shopify/ProductVariant/stock",
        target_sku="STOCK-304003",
        target_inventory_item_id="gid://shopify/InventoryItem/stock",
        target_inventory_quantity=13,
        shopify_location_id=LOCATION_ID,
        shopify_location_name="Weldingshop",
        stock_model="component_bundle",
    )
    result = set_counted_stock(
        target,
        counted_units=4,
        loose_units=5,
        mode="add",
        client=Client(),
        audit_path=tmp_path / "audit.jsonl",
    )

    quantity = calls[0][1]["input"]["quantities"][0]
    assert quantity["changeFromQuantity"] == 13
    assert quantity["quantity"] == 118
    assert result["entered_quantity"] == 105
    assert result["confirmed_quantity"] == 118
    audit = json.loads((tmp_path / "audit.jsonl").read_text().strip())
    assert audit["mode"] == "add"
    assert audit["entered_quantity"] == 105


def test_add_counted_stock_rejects_zero_and_unknown_mode(tmp_path):
    target = StockCountTarget(
        barcode="4011890115435", product_id="p", product_handle="h",
        product_title="Product", product_status="ACTIVE", product_vendor="Rhodius",
        product_image_url="", scanned_variant_id="v", scanned_sku="304003",
        scanned_title="Verpakking (25)", units_per_scan=25,
        target_variant_id="stock", target_sku="STOCK-304003",
        target_inventory_item_id="inventory", target_inventory_quantity=13,
        shopify_location_id=LOCATION_ID, shopify_location_name="Weldingshop",
        stock_model="component_bundle",
    )
    with pytest.raises(ValueError, match="minimaal één"):
        set_counted_stock(
            target, counted_units=0, mode="add", client=object(),
            audit_path=tmp_path / "audit.jsonl",
        )
    with pytest.raises(ValueError, match="Onbekende voorraadmodus"):
        set_counted_stock(
            target, counted_units=1, mode="replace", client=object(),
            audit_path=tmp_path / "audit.jsonl",
        )
