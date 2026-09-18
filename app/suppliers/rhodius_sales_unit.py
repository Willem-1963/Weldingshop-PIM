"""Rhodius has two scannable units; online publication is a separate choice."""
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
    unit = product.get("_rhodius_sales_unit", "piece")
    if unit not in {"piece", "package"}:
        raise ValueError(f"Onbekende Rhodius-eenheid: {unit}")
    units_per_item = quantity if unit == "package" else 1
    barcode = piece if units_per_item == 1 else package
    warning = "" if barcode else "GTIN verkoopeenheid ontbreekt; controle vereist"
    if quantity > 1 and unit == "package" and package and package == piece:
        barcode = ""
        warning = "Stuk en verpakking hebben dezelfde GTIN bij VE > 1; controle vereist"
    price_quantity = 1 if price_unit == "€/stuk" else quantity
    return {
        "quantity": quantity, "unit": unit, "units_per_item": units_per_item,
        "gtin_piece": piece, "gtin_package": package, "barcode": barcode,
        "barcode_source": "GTIN-code" if units_per_item == 1 else provenance,
        "price_quantity": price_quantity,
        "price_factor": units_per_item / price_quantity,
        "label": "1 stuk" if units_per_item == 1 else f"Verpakking à {quantity} stuks",
        "warning": warning,
        "online_policy": "undecided",
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
            variant[key] = f"{Decimal(str(variant[key])) * rule['units_per_item'] / rule['price_quantity']:.2f}"
    item = variant["inventoryItem"]
    if cost is not None:
        item["cost"] = str(cost)
    if item.get("cost") is not None:
        item["cost"] = f"{Decimal(str(item['cost'])) * rule['units_per_item'] / rule['price_quantity']:.2f}"
    if rule["package_weight"] or rule["piece_weight"] is not None:
        weight = (
            Decimal(str(rule["package_weight"]).lower().replace("kg", "").strip().replace(",", "."))
            if rule["package_weight"] and rule["units_per_item"] > 1 else
            Decimal(str(rule["piece_weight"]).replace(",", ".")) * rule["units_per_item"]
            if rule["piece_weight"] is not None else
            Decimal(str(rule["package_weight"]).lower().replace("kg", "").strip().replace(",", ".")) / rule["quantity"]
        )
        item["measurement"] = {"weight": {"value": float(weight), "unit": "KILOGRAMS"}}
    label = rule["label"]
    payload["title"] = f"{payload['title']} — {label}"[:255]
    payload["descriptionHtml"] += f"<p><strong>Verkoopeenheid: {label}.</strong> Bestelaantal 1 = {rule['units_per_item']} stuk(s).</p>"
    if rule["units_per_item"] == 1 and rule["quantity"] > 1:
        payload["descriptionHtml"] += f"<p>Ook beschikbaar als verpakking à {rule['quantity']} stuks.</p>"
    if rule["warning"]:
        payload["status"] = "DRAFT"
        payload["tags"] = list(dict.fromkeys([*payload.get("tags", []), "controle_gtin_verkoopeenheid"]))


def shopify_stock_quantity(product: dict, value) -> int:
    rule = sales_unit_rule(product)
    if rule:
        return math.floor(Decimal(str(value or 0)) * rule["price_quantity"] / rule["units_per_item"])
    return int(value or 0)
