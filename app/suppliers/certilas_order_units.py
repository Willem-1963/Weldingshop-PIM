"""Verified portal order weights, independent of imported packaging/bundle weights."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any


def load_order_units(connection: sqlite3.Connection) -> dict[str, dict]:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='certilas_order_units'"
    ).fetchone():
        units = with_pack_overrides({}, load_pack_overrides(connection))
        return with_sales_policies(units, load_sales_policies(connection))
    units = {sku: json.loads(data) for sku, data in connection.execute(
        "SELECT sku,rule_json FROM certilas_order_units"
    )}
    units = with_pack_overrides(units, load_pack_overrides(connection))
    return with_sales_policies(units, load_sales_policies(connection))


def load_pack_overrides(connection: sqlite3.Connection) -> dict[str, dict]:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='certilas_pack_overrides'"
    ).fetchone():
        return {}
    return {sku: json.loads(data) for sku, data in connection.execute(
        "SELECT sku,rule_json FROM certilas_pack_overrides"
    )}


def load_sales_policies(connection: sqlite3.Connection) -> dict[str, dict]:
    """Load traceable, operator-adjustable Certilas sales-unit decisions."""
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='certilas_sales_unit_policies'"
    ).fetchone():
        return {}
    result = {}
    for row in connection.execute(
        """SELECT sku,ean,physical_pack_kg,portal_minimum_kg,
                  supplier_minimum_packs,sales_mode,shop_packs_per_unit,
                  decision_source,decision_reason,evidence_json,policy_version,
                  evaluated_at,updated_at
             FROM certilas_sales_unit_policies"""
    ):
        (sku, ean, pack_kg, portal_kg, supplier_packs, mode, shop_packs,
         source, reason, evidence_json, version, evaluated_at, updated_at) = row
        result[sku] = {
            'sku': sku, 'ean': ean, 'physical_pack_kg': pack_kg,
            'portal_minimum_kg': portal_kg,
            'supplier_minimum_packs': supplier_packs,
            'sales_mode': mode, 'shop_packs_per_unit': shop_packs,
            'decision_source': source, 'decision_reason': reason,
            'evidence': json.loads(evidence_json or '{}'),
            'policy_version': version, 'evaluated_at': evaluated_at,
            'updated_at': updated_at,
        }
    return result


def with_sales_policies(units: dict[str, dict], policies: dict[str, dict]) -> dict[str, dict]:
    """Apply recorded policy rows; code interprets decisions but never embeds SKU choices."""
    result = dict(units)
    for sku, policy in policies.items():
        if policy['sales_mode'] == 'blocked':
            result.pop(sku, None)
            continue
        portal = units.get(sku) or {}
        if portal and str(portal.get('ean')) != str(policy['ean']):
            raise ValueError(f'Certilas {sku}: verkoopbeleid heeft afwijkend EAN.')
        pack_kg = float(policy['physical_pack_kg'] or 0)
        shop_packs = int(policy['shop_packs_per_unit'] or 0)
        supplier_packs = int(policy['supplier_minimum_packs'] or 0)
        if (not math.isfinite(pack_kg) or pack_kg <= 0 or shop_packs <= 0
                or supplier_packs <= 0):
            raise ValueError(f'Certilas {sku}: ongeldig duurzaam verkoopbeleid.')
        weight = pack_kg * shop_packs
        result[sku] = {
            **portal, 'sku': sku, 'ean': policy['ean'],
            'minimum_quantity': 1, 'quantity_step': 1,
            'kg_per_order_unit': weight, 'minimum_order_kg': weight,
            'local_sales_policy': True,
            'physical_pack_kg': pack_kg,
            'supplier_minimum_packs': supplier_packs,
            'shop_packs_per_unit': shop_packs,
            'sales_mode': policy['sales_mode'],
            'decision_source': policy['decision_source'],
            'decision_reason': policy['decision_reason'],
            'policy_version': policy['policy_version'],
            'policy_evaluated_at': policy['evaluated_at'],
            'policy_updated_at': policy['updated_at'],
            'portal_minimum_order_kg': policy['portal_minimum_kg'],
        }
    return result


def with_pack_overrides(units: dict[str, dict], overrides: dict[str, dict]) -> dict[str, dict]:
    result = dict(units)
    for sku, override in overrides.items():
        weight = float(override['kg_per_pack'])
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(f'Certilas {sku}: ongeldig gewicht winkelverpakking.')
        portal = units.get(sku) or {}
        if portal and str(portal.get('ean')) != str(override['ean']):
            raise ValueError(f'Certilas {sku}: winkeluitzondering heeft afwijkend EAN.')
        result[sku] = {
            **portal, 'sku': sku, 'ean': override['ean'],
            'minimum_quantity': 1, 'quantity_step': 1,
            'kg_per_order_unit': weight, 'minimum_order_kg': weight,
            'local_pack_override': True, 'override_reason': override['reason'],
            'override_updated_at': override['updated_at'],
            'portal_minimum_order_kg': portal.get('minimum_order_kg'),
        }
    return result


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
    if rule.get('local_sales_policy'):
        packs = int(rule['shop_packs_per_unit'])
        raw_pack_weight = float(rule['physical_pack_kg'])
        pack_weight = f"{round(raw_pack_weight, 2):g}".replace('.', ',')
        if not math.isclose(raw_pack_weight, round(raw_pack_weight, 2), abs_tol=1e-9):
            pack_weight = "ca. " + pack_weight
        if packs == 1:
            return (f"Verkoopeenheid: één pak van {pack_weight} kg. "
                    f"De getoonde prijs geldt per pak. Aantal 1 = één pak van {pack_weight} kg.")
        return (f"Verkoopeenheid: {packs} pakken van {pack_weight} kg "
                f"({weight} kg totaal). De getoonde prijs geldt voor {packs} pakken. "
                f"Aantal 1 = {packs} pakken.")
    if rule.get('local_pack_override'):
        return (f"Verkoopeenheid: één pak van {weight} kg. "
                f"De getoonde prijs geldt per pak. Aantal 1 = één pak van {weight} kg.")
    return (f"Minimale bestelhoeveelheid: {weight} kg. "
            f"De getoonde prijs geldt voor {weight} kg. Aantal 1 = {weight} kg.")


def sales_unit_packaging_label(base_label: str, rule: dict[str, Any] | None) -> str:
    """Add package count and total weight to a multi-pack variant label.

    The recorded policy remains the source of truth. The base label keeps the
    physical packaging identity, while this suffix makes the Shopify sales
    unit explicit and survives future family rebuilds.
    """
    label = str(base_label or "").strip()
    if not rule or not rule.get("local_sales_policy"):
        return label
    packs = int(rule.get("shop_packs_per_unit") or 0)
    pack_kg = float(rule.get("physical_pack_kg") or 0)
    if packs <= 1 or not math.isfinite(pack_kg) or pack_kg <= 0:
        return label

    def number(value: float) -> str:
        rounded = round(value, 2)
        return f"{rounded:g}".replace(".", ",")

    total_kg = packs * pack_kg
    approximate = not math.isclose(pack_kg, round(pack_kg, 2), abs_tol=1e-9)
    physical = f"{'ca. ' if approximate else ''}{number(pack_kg)} kg"
    total = f"{number(total_kg)} kg"
    sales = f"{packs} × {physical} ({total} totaal)"
    # Re-running against an already decorated label must be idempotent.
    def clean_separators(value: str) -> str:
        value = re.sub(r"\s*·\s*·\s*", " · ", value)
        value = re.sub(r"–\s*·\s*", "– ", value)
        return re.sub(r"\s+", " ", value).strip()

    if label.casefold().endswith(sales.casefold()):
        return clean_separators(label)
    label, decorated_removed = re.subn(
        r"\s+\d+\s*[×x]\s*(?:ca\.\s*)?\d+(?:[.,]\d+)?\s*kg\s*"
        r"\(\d+(?:[.,]\d+)?\s*kg\s+totaal\)\s*$",
        "", label, flags=re.IGNORECASE,
    )
    label = label.strip()
    if decorated_removed:
        return clean_separators(f"{label} {sales}")
    for trailing_weight in dict.fromkeys((physical, total)):
        if re.search(rf"{re.escape(trailing_weight)}\s*$", label, flags=re.IGNORECASE):
            return re.sub(
                rf"{re.escape(trailing_weight)}\s*$", sales, label,
                flags=re.IGNORECASE,
            )
    if re.search(r"\d+(?:[.,]\d+)?\s*kg\s*$", label, flags=re.IGNORECASE):
        return re.sub(
            r"\d+(?:[.,]\d+)?\s*kg\s*$", sales, label,
            flags=re.IGNORECASE,
        )
    return f"{label} · {sales}" if label else sales


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
