"""Rhodius: one Shopify item represents the source VE, not the price unit."""
from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation

SLUG = "rhodius-abrasives-gmbh"


def sales_unit_rule(product: dict) -> dict | None:
    if product.get("_supplier_slug") != SLUG:
        return None
    raw = product.get("_raw_data")
    if raw is None:
        raw = json.loads(product.get("raw_data_json") or "{}")
    try:
        quantity = Decimal(1)
        for factor in str(raw.get("VE", "")).lower().replace(" ", "").split("x"):
            quantity *= Decimal(factor.replace(",", "."))
        if not quantity.is_finite() or quantity < 1 or quantity != int(quantity):
            raise ValueError
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError(f"Rhodius {product.get('sku')}: geldige VE ontbreekt.")
    quantity = int(quantity)
    piece = str(raw.get("GTIN-code") or raw.get("gtin_piece") or "").strip()
    package = str(raw.get("GTIN/verpakking") or raw.get("gtin_package") or "").strip()
    provenance = "GTIN/verpakking"
    website = raw.get("website_import") or {}
    specs = website.get("technical_specifications") or {}
    if not package and website.get("verification") == "official_page":
        # Only use an explicitly identified packaging GTIN for the same VE.
        if str(specs.get("inhoud") or "") == str(quantity):
            package = str(specs.get("gtin_code_verpakkingseenheid") or "").strip()
            provenance = "website_import.technical_specifications.gtin_code_verpakkingseenheid"
    price_unit = str(raw.get("Prijseenheid") or "").strip()
    if price_unit not in {"€/stuk", "€/pak", "€/blik"}:
        raise ValueError(f"Rhodius {product.get('sku')}: onbekende prijseenheid {price_unit!r}.")
    barcode = piece if quantity == 1 else package
    return {
        "quantity": quantity, "barcode": barcode,
        "barcode_source": "GTIN-code" if quantity == 1 else provenance,
        "price_factor": quantity if price_unit == "€/stuk" else 1,
        "label": "1 stuk" if quantity == 1 else f"Verpakking à {quantity} stuks",
        "warning": "" if barcode else "GTIN verkoopeenheid ontbreekt; controle vereist",
        "piece_weight": raw.get("Gewicht in kg/stuk"),
        "package_weight": raw.get("Gewicht/VE"),
    }


def apply_shopify_sales_unit(product: dict, payload: dict, *, price=None, cost=None, compare_at=None) -> None:
    rule = sales_unit_rule(product)
    if rule is None:
        return
    variant = payload["variants"][0]
    # Empty string also clears a stale piece barcode on a previous test upload.
    variant["barcode"] = rule["barcode"]
    if price is not None:
        variant["price"] = str(price)
    if compare_at is not None and "compareAtPrice" in variant:
        variant["compareAtPrice"] = str(compare_at)
    for key in ("price", "compareAtPrice"):
        if variant.get(key) is not None:
            variant[key] = f"{Decimal(str(variant[key])) * rule['price_factor']:.2f}"
    item = variant["inventoryItem"]
    if cost is not None:
        item["cost"] = str(cost)
    if item.get("cost") is not None:
        item["cost"] = f"{Decimal(str(item['cost'])) * rule['price_factor']:.2f}"
    if rule["package_weight"] or rule["piece_weight"] is not None:
        weight = (
            Decimal(str(rule["package_weight"]).lower().replace("kg", "").strip().replace(",", "."))
            if rule["package_weight"] else
            Decimal(str(rule["piece_weight"]).replace(",", ".")) * rule["quantity"]
        )
        item["measurement"] = {"weight": {"value": float(weight), "unit": "KILOGRAMS"}}
    label = rule["label"]
    payload["title"] = f"{payload['title']} — {label}"[:255]
    payload["descriptionHtml"] += f"<p><strong>Verkoopeenheid: {label}.</strong> Bestelaantal 1 = {rule['quantity']} stuk(s).</p>"
    if rule["warning"]:
        payload["status"] = "DRAFT"
        payload["tags"] = list(dict.fromkeys([*payload.get("tags", []), "controle_gtin_verkoopeenheid"]))


def shopify_stock_quantity(product: dict, value) -> int:
    rule = sales_unit_rule(product)
    factor = rule["price_factor"] if rule else 1
    return math.floor(float(value or 0) / factor)
