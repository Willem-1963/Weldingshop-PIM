"""Verified portal order weights, independent of imported packaging/bundle weights."""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any


def load_order_units(connection: sqlite3.Connection) -> dict[str, dict]:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='certilas_order_units'"
    ).fetchone():
        return {}
    return {sku: json.loads(data) for sku, data in connection.execute(
        "SELECT sku,rule_json FROM certilas_order_units"
    )}


def verified_order_unit(product: dict[str, Any]) -> dict | None:
    if str(product.get('_supplier_slug') or '').lower() != 'certilas':
        return None
    rule = product.get('_certilas_order_unit')
    if not isinstance(rule, dict) or str(rule.get('ean')) != str(product.get('ean')):
        raise ValueError(f"Certilas {product.get('sku')}: gecontroleerde minimale bestelhoeveelheid ontbreekt.")
    for key in ('minimum_quantity', 'quantity_step', 'kg_per_order_unit', 'minimum_order_kg'):
        value = float(rule.get(key) or 0)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Certilas {product.get('sku')}: ongeldig bestelveld {key}.")
    if not math.isclose(float(rule['minimum_order_kg']),
                        float(rule['minimum_quantity']) * float(rule['kg_per_order_unit'])):
        raise ValueError('Certilas: minimumgewicht klopt niet met de portalbesteleenheid.')
    # Shopify quantity increments are whole minimum sale units. An unrelated
    # portal step cannot be represented by multiplying this minimum unit.
    ratio = float(rule['minimum_quantity']) / float(rule['quantity_step'])
    if not math.isclose(ratio, round(ratio)):
        raise ValueError('Certilas: bestelstap past niet in de minimale verkoopeenheid.')
    return rule


def order_price_factor(product: dict[str, Any]) -> float:
    rule = verified_order_unit(product)
    if rule is None:
        return 1.0
    package_weight = float(product.get('kg_per_sales_unit') or 0)
    if not math.isfinite(package_weight) or package_weight <= 0:
        raise ValueError(f"Certilas {product.get('sku')}: prijsverpakkingsgewicht ontbreekt.")
    return float(rule['minimum_order_kg']) / package_weight


def order_notice(product: dict[str, Any]) -> str | None:
    rule = verified_order_unit(product)
    if rule is None:
        return None
    weight = f"{float(rule['minimum_order_kg']):g}".replace('.', ',')
    return (f"Minimale bestelhoeveelheid: {weight} kg. "
            f"De getoonde prijs geldt voor {weight} kg. Aantal 1 = {weight} kg.")


def checked_order_products(products: list[dict], *, preserve_families: bool) -> tuple[list[dict], list[str]]:
    """Isolate unknown orders; full product replacement must keep whole families."""
    checked, skipped, blocked_families = [], [], set()
    for product in products:
        try:
            order_price_factor(product)
            checked.append(product)
        except (ValueError, TypeError):
            skipped.append(str(product['sku']))
            family = (product.get('_family_variant_options') or {}).get('family_key')
            if family:
                blocked_families.add(family)
    if preserve_families:
        kept = []
        for product in checked:
            if (product.get('_family_variant_options') or {}).get('family_key') in blocked_families:
                skipped.append(str(product['sku']))
            else:
                kept.append(product)
        checked = kept
    return checked, skipped
