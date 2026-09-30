"""Update existing Rhodius sale units without replacing their Shopify structure."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.suppliers.rhodius_sales_unit import sales_unit_rule


UPDATE_MUTATION = """
mutation RhodiusSaleUnits($productId:ID!,$variants:[ProductVariantsBulkInput!]!){
  productVariantsBulkUpdate(productId:$productId,variants:$variants){
    product{id}
    productVariants{id sku barcode}
    userErrors{field message code}
  }
}
"""


def plan_existing_units(products: list[dict], existing: dict) -> dict:
    """SKU is a package, SKU-STUK a piece; SKU-STOCK is never written.

    Costs use the price list's price unit. Sales prices and discount rules are
    Shopify-owned. Missing GTINs never clear an existing barcode. The normal
    sync neither creates units nor updates stock or bundle relationships.
    """
    grouped: dict[str, dict[str, dict]] = {}
    warnings = []
    missing = []
    units = []
    for product in products:
        base = str(product.get("sku") or "").strip().upper()
        if not base or base.endswith(("-STUK", "-STOCK")) or base.startswith("TEST-"):
            continue
        raw = product.get("_raw_data")
        if raw is None:
            raw = json.loads(product.get("raw_data_json") or "{}")
        # Barcodes for production come from the price list, not website fallback.
        raw = {**raw, "website_import": {}}
        for unit, sku in (("package", base), ("piece", base + "-STUK")):
            match = existing.get(sku)
            if not match:
                missing.append(sku)
                continue
            try:
                rule = sales_unit_rule({**product, "_raw_data": raw,
                                        "_rhodius_sales_unit": unit})
                if rule is None:
                    raise ValueError("Rhodius-leveranciersroute ontbreekt")
                cost = product.get("_rhodius_source_cost", product.get("cost_price"))
                cost_value = None
                if cost is not None:
                    cost_value = Decimal(str(cost))
                    if not cost_value.is_finite() or cost_value < 0:
                        raise ValueError("ongeldige bronkostprijs")
                    cost_value = (cost_value * Decimal(rule["units_per_item"])
                                  / Decimal(rule["price_quantity"])).quantize(
                                      Decimal("0.01"), rounding=ROUND_HALF_UP)
            except (ValueError, InvalidOperation, TypeError) as error:
                warnings.append({"sku": sku, "error": str(error)})
                continue
            variant = {"id": match["variant"]["id"]}
            if rule["warning"]:
                warnings.append({"sku": sku, "error": rule["warning"]})
            else:
                barcode = rule["barcode"]
                # A VE=1 package may still have its own price-list barcode.
                if unit == "package" and rule["quantity"] == 1:
                    barcode = str(raw.get("GTIN/verpakking") or raw.get("gtin_package")
                                  or barcode).strip()
                variant["barcode"] = barcode
            if cost_value is not None:
                variant["inventoryItem"] = {"cost": str(cost_value)}
            else:
                warnings.append({"sku": sku, "error": "bronkostprijs ontbreekt; bestaande kostprijs behouden"})
            if len(variant) > 1:
                grouped.setdefault(match["product"]["id"], {})[variant["id"]] = variant
                units.append({"sku": sku, "unit": unit, "variant": variant})
    rows = []
    for product_id, variants in grouped.items():
        values = list(variants.values())
        for offset in range(0, len(values), 100):
            rows.append({"productId": product_id, "variants": values[offset:offset + 100]})
    return {"rows": rows, "units": units, "warnings": warnings,
            "missing_units": missing,
            "protected_stock_skus": sorted(sku for sku in existing if sku.endswith("-STOCK"))}
