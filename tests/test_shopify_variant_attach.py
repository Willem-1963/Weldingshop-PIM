import sqlite3
from types import SimpleNamespace

import pytest

from app.shopify import sync


def test_target_uses_selected_options_from_matching_product_variant(monkeypatch):
    class Client:
        @classmethod
        def from_settings(cls):
            return cls()

        def graphql(self, query, variables):
            return {"productVariants": {"nodes": [{
                "id": "gid://shopify/ProductVariant/2",
                "sku": "7812873",
                "selectedOptions": [],
                "product": {
                    "id": "gid://shopify/Product/1",
                    "variants": {"nodes": [{
                        "sku": "7812873",
                        "selectedOptions": [{
                            "name": "Selecteer de juiste gasklep:",
                            "value": "Magneetventiel 42 volt model 2",
                        }],
                    }]},
                },
            }]}}

    monkeypatch.setattr(sync, "ShopifyClient", Client)

    target = sync.get_shopify_variant_target("7812873")

    assert target["_target_selected_options"] == {
        "Selecteer de juiste gasklep:": "Magneetventiel 42 volt model 2"
    }


def _target(existing_value="12 V AC"):
    return {
        "id": "gid://shopify/Product/1",
        "title": "Magneetventiel",
        "handle": "magneetventiel",
        "vendor": "Tecweld",
        "status": "ACTIVE",
        "_target_selected_options": {"Spanning": existing_value},
        "options": [{"name": "Spanning"}],
        "variants": {"nodes": [{
            "title": existing_value,
            "sku": "OLD-SKU",
            "selectedOptions": [{"name": "Spanning", "value": existing_value}],
        }]},
    }


def test_blank_option_keeps_target_sku_value(monkeypatch, tmp_path):
    target = _target("12 V AC")
    target["options"].append({"name": "Gasklep"})
    target["_target_selected_options"]["Gasklep"] = "Standaard"
    target["variants"]["nodes"][0]["selectedOptions"].append(
        {"name": "Gasklep", "value": "Standaard"}
    )
    database = _prepare(monkeypatch, tmp_path, target)

    result = sync.add_pim_product_as_shopify_variant(
        "tecweld", "7812873", "ZCQ-20-B2-AC-12-V",
        {"Spanning": "42 V AC", "Gasklep": ""},
    )

    assert result["sku"] == "7812873"
    variant_input = sync.ShopifyClient.last_variables["variants"][0]
    assert variant_input["optionValues"] == [
        {"optionName": "Spanning", "name": "42 V AC"},
        {"optionName": "Gasklep", "name": "Standaard"},
    ]


def _prepare(monkeypatch, tmp_path, target, existing_variant=None):
    database = tmp_path / "tecweld.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE products(sku TEXT PRIMARY KEY,shopify_handle TEXT,"
            "shopify_status TEXT,updated_at TEXT)"
        )
        connection.execute("INSERT INTO products(sku) VALUES('7812873')")

    class Client:
        shop_domain = "example.myshopify.com"
        last_variables = None

        @classmethod
        def from_settings(cls):
            return cls()

        def find_variant_by_sku(self, sku):
            return existing_variant

        def graphql(self, query, variables=None):
            type(self).last_variables = variables
            if "productCreateMedia" in query:
                return {"productCreateMedia": {
                    "media": [{"id": "gid://shopify/MediaImage/9", "status": "UPLOADED"}],
                    "mediaUserErrors": [],
                }}
            if "productVariantsBulkUpdate" in query:
                return {"productVariantsBulkUpdate": {
                    "productVariants": [{"id": variables["variants"][0]["id"]}],
                    "userErrors": [],
                }}
            return {"productVariantsBulkCreate": {
                "productVariants": [{
                    "id": "gid://shopify/ProductVariant/2",
                    "title": "42 V AC", "sku": "7812873", "price": "5.37",
                }],
                "userErrors": [],
            }}

    monkeypatch.setattr(sync, "ShopifyClient", Client)
    monkeypatch.setattr(sync, "get_shopify_settings", lambda: {"enabled": 1})
    monkeypatch.setattr(sync, "get_supplier", lambda slug: {"name": "Tecweld"})
    monkeypatch.setattr(sync, "supplier_route", lambda slug: SimpleNamespace(
        shopify_vendor_names=("Tecweld",)
    ))
    monkeypatch.setattr(sync, "supplier_database_path", lambda slug: database)
    monkeypatch.setattr(sync, "_source_products", lambda slug: [{
        "sku": "7812873", "raw_data_json": "{}",
        "source_title": "Elektromagnetische klep VZCT 6,5FS",
        "images": [{
            "image_url": "https://tecweld.pl/42v-ac.jpg",
            "alt_text": "42V AC magneetventiel",
        }],
    }])
    monkeypatch.setattr(sync, "get_shopify_variant_target", lambda sku: target)
    monkeypatch.setattr(sync, "_input", lambda source, existing: {"variants": [{
        **({"id": existing["variant"]["id"]} if existing else {}),
        "sku": source["sku"], "price": "5.37",
        "optionValues": [{"optionName": "Title", "name": "Default Title"}],
        "file": {
            "originalSource": "https://tecweld.pl/42v-ac.jpg",
            "alt": "42V AC magneetventiel",
            "contentType": "IMAGE",
        },
    }]})
    return database


def test_add_pim_product_as_variant_without_rewriting_product(monkeypatch, tmp_path):
    database = _prepare(monkeypatch, tmp_path, _target())
    result = sync.add_pim_product_as_shopify_variant(
        "tecweld", "7812873", "ZCQ-20-B2-AC-12-V", {"Spanning": "42 V AC"}
    )
    assert result["sku"] == "7812873"
    variant_input = sync.ShopifyClient.last_variables["variants"][0]
    assert "sku" not in variant_input
    assert variant_input["inventoryItem"]["sku"] == "7812873"
    assert "file" not in variant_input
    assert variant_input["mediaId"] == "gid://shopify/MediaImage/9"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT shopify_handle,shopify_status FROM products WHERE sku='7812873'"
        ).fetchone() == ("magneetventiel", "active")


def test_add_pim_product_as_variant_rejects_duplicate_option(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, _target("42 V AC"))
    with pytest.raises(ValueError, match="bestaat al als variant OLD-SKU"):
        sync.add_pim_product_as_shopify_variant(
            "tecweld", "7812873", "ZCQ-20-B2-AC-12-V", {"Spanning": "42 V AC"}
        )


def test_existing_variant_without_media_is_repaired(monkeypatch, tmp_path):
    existing = {
        "id": "gid://shopify/ProductVariant/2",
        "sku": "7812873",
        "title": "42 V AC model 2",
        "product": {"id": "gid://shopify/Product/1"},
        "media": {"nodes": []},
    }
    target = _target()
    target["variants"]["nodes"][0]["sku"] = "7812873"
    _prepare(monkeypatch, tmp_path, target, existing_variant=existing)

    result = sync.add_pim_product_as_shopify_variant(
        "tecweld", "7812873", "ZCQ-20-B2-AC-12-V", {"Spanning": "42 V AC"}
    )

    assert result["media_repaired"] is True
    variant_input = sync.ShopifyClient.last_variables["variants"][0]
    assert variant_input["id"] == "gid://shopify/ProductVariant/2"
    assert variant_input["mediaId"] == "gid://shopify/MediaImage/9"
    assert variant_input["price"] == "5.37"


def test_existing_variant_with_media_is_updated_and_media_preserved(
    monkeypatch, tmp_path
):
    existing = {
        "id": "gid://shopify/ProductVariant/2",
        "sku": "7812873",
        "title": "42 V AC model 2",
        "product": {"id": "gid://shopify/Product/1"},
        "media": {"nodes": [{"id": "gid://shopify/MediaImage/8"}]},
    }
    target = _target()
    target["variants"]["nodes"][0]["sku"] = "7812873"
    _prepare(monkeypatch, tmp_path, target, existing_variant=existing)

    result = sync.add_pim_product_as_shopify_variant(
        "tecweld", "7812873", "ZCQ-20-B2-AC-12-V", {"Spanning": ""}
    )

    assert result["updated"] is True
    assert result["media_repaired"] is False
    variant_input = sync.ShopifyClient.last_variables["variants"][0]
    assert variant_input["id"] == "gid://shopify/ProductVariant/2"
    assert "mediaId" not in variant_input
    assert variant_input["optionValues"] == [
        {"optionName": "Spanning", "name": "12 V AC"}
    ]
