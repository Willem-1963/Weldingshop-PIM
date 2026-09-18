"""Create independent draft sale units linked by Shopify's native inventory."""
from __future__ import annotations

import json
from decimal import Decimal

from app.shopify.client import ShopifyClient
from app.suppliers.hub import _connect, get_supplier_product, supplier_database_path, utc_now
from app.suppliers.rhodius_sales_unit import SLUG, sales_unit_rule


RELATION_QUERY = """
query RhodiusUnitRelationship($id:ID!){
  productVariant(id:$id){
    id requiresComponents price
    productVariantComponents(first:30){nodes{quantity productVariant{id}}}
  }
}
"""

RELATION_MUTATION = """
mutation RhodiusPackageComponents($input:[ProductVariantRelationshipUpdateInput!]!){
  productVariantRelationshipBulkUpdate(input:$input){
    parentProductVariants{
      id requiresComponents
      productVariantComponents(first:30){nodes{quantity productVariant{id}}}
    }
    userErrors{field message code}
  }
}
"""


def link_package_inventory(client, package_id: str, piece_id: str, quantity: int, package_price: str | None = None) -> dict:
    if quantity <= 1 or package_id == piece_id:
        raise ValueError("Een VE-koppeling vereist verschillende varianten en VE > 1.")
    current = client.graphql(RELATION_QUERY, {"id": package_id})["productVariant"]
    if not current:
        raise ValueError("Verpakkingsvariant ontbreekt in Shopify.")
    components = current["productVariantComponents"]["nodes"]
    expected = [{"quantity": quantity, "productVariant": {"id": piece_id}}]
    price_matches = package_price is None or Decimal(current["price"]) == Decimal(package_price)
    if current["requiresComponents"] and components == expected and price_matches:
        return current
    if components and (
        len(components) != 1 or components[0]["productVariant"]["id"] != piece_id
    ):
        raise ValueError("Testverpakking heeft andere componenten; koppeling niet overschreven.")
    operation = "productVariantRelationshipsToUpdate" if components else "productVariantRelationshipsToCreate"
    response = client.graphql(RELATION_MUTATION, {"input": [{
        "parentProductVariantId": package_id,
        operation: [{"id": piece_id, "quantity": quantity}],
        "priceInput": ({"calculation": "FIXED", "price": package_price}
                       if package_price is not None else {"calculation": "NONE"}),
    }]})["productVariantRelationshipBulkUpdate"]
    if response.get("userErrors"):
        raise RuntimeError("Shopify-voorraadkoppeling: " + json.dumps(response["userErrors"], ensure_ascii=False))
    verified = client.graphql(RELATION_QUERY, {"id": package_id})["productVariant"]
    if not verified["requiresComponents"] or verified["productVariantComponents"]["nodes"] != expected:
        raise RuntimeError("Shopify heeft de voorraadkoppeling niet bevestigd.")
    if package_price is not None and Decimal(verified["price"]) != Decimal(package_price):
        raise RuntimeError("Shopify heeft de PIM-verpakkingsprijs niet behouden.")
    return verified


def _save_unit(sku: str, result: dict, component_id: str = "") -> None:
    rule = result["unit_rule"]
    with _connect(supplier_database_path(SLUG)) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS rhodius_test_sales_units(
            sku TEXT NOT NULL, unit TEXT NOT NULL, units_per_item INTEGER NOT NULL,
            gtin TEXT NOT NULL, barcode_source TEXT NOT NULL,
            shopify_product_id TEXT NOT NULL, shopify_variant_id TEXT NOT NULL,
            component_variant_id TEXT NOT NULL DEFAULT '',
            online_policy TEXT NOT NULL DEFAULT 'undecided',
            warning TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            PRIMARY KEY(sku,unit))""")
        connection.execute("""INSERT INTO rhodius_test_sales_units(
            sku,unit,units_per_item,gtin,barcode_source,shopify_product_id,
            shopify_variant_id,component_variant_id,warning,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(sku,unit) DO UPDATE SET
            units_per_item=excluded.units_per_item,gtin=excluded.gtin,
            barcode_source=excluded.barcode_source,shopify_product_id=excluded.shopify_product_id,
            shopify_variant_id=excluded.shopify_variant_id,component_variant_id=excluded.component_variant_id,
            warning=excluded.warning,updated_at=excluded.updated_at""", (
            sku, rule["unit"], rule["units_per_item"], rule["barcode"], rule["barcode_source"],
            result["id"], result["variant"]["id"], component_id, rule["warning"], utc_now(),
        ))


def sales_unit_preview(product: dict) -> list[dict]:
    """PIM screen data, including previously saved test links and scan quantities."""
    product = {**product, "_supplier_slug": SLUG}
    piece_rule = sales_unit_rule({**product, "_rhodius_sales_unit": "piece"})
    rules = [piece_rule]
    if piece_rule["quantity"] > 1:
        rules.append(sales_unit_rule({**product, "_rhodius_sales_unit": "package"}))
    stored = {}
    with _connect(supplier_database_path(SLUG)) as connection:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='rhodius_test_sales_units'").fetchone():
            stored = {row["unit"]: dict(row) for row in connection.execute(
                "SELECT * FROM rhodius_test_sales_units WHERE sku=?", (product["sku"],),
            )}
    return [{**rule, "test_product_id": (stored.get(rule["unit"]) or {}).get("shopify_product_id", ""),
             "shared_inventory": bool((stored.get(rule["unit"]) or {}).get("component_variant_id"))}
            for rule in rules]


def upload_test_units(sku: str, upload_unit, description_html=None) -> dict:
    product = get_supplier_product(SLUG, sku)
    if not product:
        raise ValueError(f"Rhodius {sku} ontbreekt in het PIM.")
    rule = sales_unit_rule({**product, "_supplier_slug": SLUG})
    piece = upload_unit(SLUG, sku, description_html=description_html, rhodius_unit="piece")
    _save_unit(sku, piece)
    units = [piece]
    relationship = None
    if rule["quantity"] > 1:
        package = upload_unit(SLUG, sku, description_html=description_html, rhodius_unit="package")
        _save_unit(sku, package)
        relationship = link_package_inventory(
            ShopifyClient.from_settings(), package["variant"]["id"], piece["variant"]["id"], rule["quantity"],
            package_price=package["variant"]["price"],
        )
        _save_unit(sku, package, piece["variant"]["id"])
        units.append(package)
    return {**piece, "units": units, "inventory_relationship": relationship, "online_policy": "undecided"}
