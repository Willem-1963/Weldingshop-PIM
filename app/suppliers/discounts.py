from __future__ import annotations

import json
from typing import Any

from app.suppliers.hub import (
    REGISTRY_PATH, _connect, get_supplier, init_supplier_database, utc_now,
)


MATCH_FIELDS = {
    "all": "Alle producten",
    "product_type": "Productgroep",
    "category": "Categorie",
    "category_full": "Volledige categorie",
    "sku": "Specifieke SKU",
}
SPECIFICITY = {"all": 0, "product_type": 1, "category": 2, "category_full": 3, "sku": 4}
BASIS_FIELDS = {
    "price": "Reguliere bronprijs",
    "sale_price": "Aanbiedingsprijs uit bron",
    "lowest_price": "Laagste van reguliere prijs en aanbiedingsprijs",
}
SALES_RULE_TYPES = {
    "none": "Doe niets — gebruik het leveranciersveld",
    "discount_from_cost": "Klantkorting als percentage van netto inkoopprijs",
    "max_discount_over_discount": "Deel van onze leverancierskorting doorgeven",
    "markup_on_cost": "Vaste opslag per product (%) op netto inkoopprijs",
    "gross_margin": "Gewenste brutomarge",
    "fixed_markup": "Vaste opslag per product (€) op netto inkoopprijs",
}


def init_discount_tables(slug: str) -> None:
    path = init_supplier_database(slug)
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS purchase_discount_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                match_field TEXT NOT NULL,
                match_value TEXT NOT NULL DEFAULT '',
                discount_percent REAL NOT NULL,
                basis_field TEXT NOT NULL DEFAULT 'price',
                priority INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS purchase_discount_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                applied_at TEXT NOT NULL,
                products_updated INTEGER NOT NULL,
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS sales_price_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                match_field TEXT NOT NULL,
                match_value TEXT NOT NULL DEFAULT '',
                rule_type TEXT NOT NULL,
                rule_value REAL NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def list_sales_price_rules(
    slug: str, include_disabled: bool = True
) -> list[dict[str, Any]]:
    init_discount_tables(slug)
    where = "" if include_disabled else "WHERE enabled=1"
    with _connect(init_supplier_database(slug)) as conn:
        rows = conn.execute(
            f"""SELECT * FROM sales_price_rules {where}
                ORDER BY enabled DESC,priority DESC,
                CASE match_field WHEN 'sku' THEN 4 WHEN 'category_full' THEN 3
                WHEN 'category' THEN 2 WHEN 'product_type' THEN 1 ELSE 0 END DESC,id"""
        ).fetchall()
    return [dict(row) for row in rows]


def save_scoped_sales_price_rule(
    slug: str, *, name: str, match_field: str, match_value: str,
    rule_type: str, rule_value: float, priority: int = 0,
) -> int:
    if match_field not in MATCH_FIELDS:
        raise ValueError("Ongeldig toepassingsniveau.")
    if rule_type not in SALES_RULE_TYPES:
        raise ValueError("Ongeldige verkoopprijsberekening.")
    if match_field != "all" and not str(match_value).strip():
        raise ValueError("Vul een productgroep, categorie of SKU in.")
    value = float(rule_value)
    if value < 0 or (rule_type != "fixed_markup" and value > 100):
        raise ValueError("Waarde moet tussen 0 en 100 liggen.")
    init_discount_tables(slug)
    now = utc_now()
    with _connect(init_supplier_database(slug)) as conn:
        cursor = conn.execute(
            """INSERT INTO sales_price_rules(
                   name,match_field,match_value,rule_type,rule_value,priority,
                   enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)""",
            (name.strip(), match_field, str(match_value).strip(), rule_type,
             value, int(priority), now, now),
        )
    return int(cursor.lastrowid)


def set_sales_price_rule_enabled(slug: str, rule_id: int, enabled: bool) -> None:
    init_discount_tables(slug)
    with _connect(init_supplier_database(slug)) as conn:
        cursor = conn.execute(
            "UPDATE sales_price_rules SET enabled=?,updated_at=? WHERE id=?",
            (int(enabled), utc_now(), int(rule_id)),
        )
        if cursor.rowcount != 1:
            raise ValueError("Verkoopprijsregel bestaat niet.")


def update_sales_price_rule(
    slug: str, rule_id: int, *, name: str, match_field: str,
    match_value: str, rule_type: str, rule_value: float, priority: int,
) -> None:
    if match_field not in MATCH_FIELDS:
        raise ValueError("Ongeldig toepassingsniveau.")
    if rule_type not in SALES_RULE_TYPES:
        raise ValueError("Ongeldige verkoopprijsberekening.")
    if match_field != "all" and not str(match_value).strip():
        raise ValueError("Vul een productgroep, categorie of SKU in.")
    value = float(rule_value)
    if value < 0 or (rule_type != "fixed_markup" and value > 100):
        raise ValueError("Waarde moet tussen 0 en 100 liggen.")
    init_discount_tables(slug)
    with _connect(init_supplier_database(slug)) as conn:
        cursor = conn.execute(
            """UPDATE sales_price_rules
                  SET name=?,match_field=?,match_value=?,rule_type=?,rule_value=?,
                      priority=?,updated_at=?
                WHERE id=?""",
            (name.strip(), match_field, str(match_value).strip(), rule_type,
             value, int(priority), utc_now(), int(rule_id)),
        )
        if cursor.rowcount != 1:
            raise ValueError("Verkoopprijsregel bestaat niet.")


def delete_sales_price_rule(slug: str, rule_id: int) -> None:
    init_discount_tables(slug)
    with _connect(init_supplier_database(slug)) as conn:
        cursor = conn.execute("DELETE FROM sales_price_rules WHERE id=?", (int(rule_id),))
        if cursor.rowcount != 1:
            raise ValueError("Verkoopprijsregel bestaat niet.")


def list_discount_rules(slug: str, include_disabled: bool = True) -> list[dict[str, Any]]:
    init_discount_tables(slug)
    where = "" if include_disabled else "WHERE enabled=1"
    with _connect(init_supplier_database(slug)) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM purchase_discount_rules {where}
            ORDER BY enabled DESC, priority DESC,
                CASE match_field
                    WHEN 'sku' THEN 4 WHEN 'category_full' THEN 3
                    WHEN 'category' THEN 2 WHEN 'product_type' THEN 1 ELSE 0
                END DESC, id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def save_discount_rule(
    slug: str,
    *,
    name: str,
    match_field: str,
    match_value: str,
    discount_percent: float,
    basis_field: str = "price",
    priority: int = 0,
) -> int:
    if match_field not in MATCH_FIELDS:
        raise ValueError("Ongeldig toepassingsniveau.")
    if basis_field not in BASIS_FIELDS:
        raise ValueError("Ongeldige prijsbasis.")
    if not 0 <= float(discount_percent) <= 100:
        raise ValueError("Korting moet tussen 0% en 100% liggen.")
    if match_field != "all" and not match_value.strip():
        raise ValueError("Vul een productgroep, categorie of SKU in.")
    init_discount_tables(slug)
    now = utc_now()
    with _connect(init_supplier_database(slug)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO purchase_discount_rules(
                name,match_field,match_value,discount_percent,basis_field,
                priority,enabled,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,1,?,?)
            """,
            (
                name.strip(), match_field, match_value.strip(),
                float(discount_percent), basis_field, int(priority), now, now,
            ),
        )
        return int(cursor.lastrowid)


def set_discount_rule_enabled(slug: str, rule_id: int, enabled: bool) -> None:
    init_discount_tables(slug)
    with _connect(init_supplier_database(slug)) as conn:
        conn.execute(
            "UPDATE purchase_discount_rules SET enabled=?,updated_at=? WHERE id=?",
            (int(enabled), utc_now(), int(rule_id)),
        )


def set_discount_rule_basis(slug: str, rule_id: int, basis_field: str) -> None:
    if basis_field not in BASIS_FIELDS:
        raise ValueError("Ongeldige prijsbasis.")
    init_discount_tables(slug)
    with _connect(init_supplier_database(slug)) as conn:
        cursor = conn.execute(
            "UPDATE purchase_discount_rules SET basis_field=?,updated_at=? WHERE id=?",
            (basis_field, utc_now(), int(rule_id)),
        )
        if cursor.rowcount != 1:
            raise ValueError("Kortingsregel bestaat niet.")


def delete_discount_rule(slug: str, rule_id: int) -> None:
    """Permanently delete exactly one purchase-discount rule."""
    init_discount_tables(slug)
    with _connect(init_supplier_database(slug)) as conn:
        cursor = conn.execute(
            "DELETE FROM purchase_discount_rules WHERE id=?",
            (int(rule_id),),
        )
        if cursor.rowcount != 1:
            raise ValueError("Kortingsregel bestaat niet.")


def distinct_match_values(slug: str, field: str) -> list[str]:
    if field not in {"product_type", "category", "category_full", "sku"}:
        return []
    with _connect(init_supplier_database(slug)) as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT {field} value FROM products
            WHERE source_present=1 AND {field} IS NOT NULL AND TRIM({field})<>''
            ORDER BY value COLLATE NOCASE
            """
        ).fetchall()
    return [row["value"] for row in rows]


def lookup_discount_product(slug: str, sku: str) -> dict[str, Any] | None:
    """Resolve one current supplier SKU for the discount-rule editor."""
    value = str(sku or "").strip()
    if not value:
        return None
    with _connect(init_supplier_database(slug)) as conn:
        row = conn.execute(
            """SELECT sku,source_title,product_type,category,available
                 FROM products
                WHERE source_present=1 AND sku=? COLLATE NOCASE
                LIMIT 1""",
            (value,),
        ).fetchone()
    return dict(row) if row else None


def discount_product_options(slug: str) -> dict[str, dict[str, Any]]:
    """Return every current SKU and its label data for the searchable editor."""
    with _connect(init_supplier_database(slug)) as conn:
        rows = conn.execute(
            """SELECT sku,source_title,product_type,category,available
                 FROM products
                WHERE source_present=1 AND sku IS NOT NULL AND TRIM(sku)<>''
                ORDER BY sku COLLATE NOCASE"""
        ).fetchall()
    return {str(row["sku"]): dict(row) for row in rows}


def _rule_matches(rule: dict[str, Any], product: dict[str, Any]) -> bool:
    field = rule["match_field"]
    if field == "all":
        return True
    return str(product.get(field) or "").strip().casefold() == str(
        rule["match_value"]
    ).strip().casefold()


def _effective_rule(rules: list[dict[str, Any]], product: dict[str, Any]) -> dict[str, Any] | None:
    matching = [rule for rule in rules if _rule_matches(rule, product)]
    if not matching:
        return None
    return max(
        matching,
        key=lambda rule: (
            int(rule["priority"]),
            SPECIFICITY.get(rule["match_field"], 0),
            int(rule["id"]),
        ),
    )


def preview_purchase_costs(slug: str, limit: int | None = 500) -> list[dict[str, Any]]:
    rules = list_discount_rules(slug, include_disabled=False)
    query = """
        SELECT sku,source_title,product_type,category,category_full,
            price,sale_price,cost_price
        FROM products WHERE source_present=1 ORDER BY sku
    """
    params: tuple[Any, ...] = ()
    if limit:
        query += " LIMIT ?"
        params = (int(limit),)
    with _connect(init_supplier_database(slug)) as conn:
        products = [dict(row) for row in conn.execute(query, params).fetchall()]

    result = []
    for product in products:
        rule = _effective_rule(rules, product)
        basis = None
        if rule:
            if rule["basis_field"] == "lowest_price":
                candidates = [
                    float(value)
                    for value in (product.get("price"), product.get("sale_price"))
                    if value is not None
                ]
                basis = min(candidates) if candidates else None
            else:
                basis = product.get(rule["basis_field"])
        calculated = (
            round(float(basis) * (1 - float(rule["discount_percent"]) / 100), 2)
            if rule and basis is not None
            else None
        )
        result.append(
            {
                "SKU": product["sku"],
                "Product": product.get("source_title") or "",
                "Productgroep": product.get("product_type") or "",
                "Categorie": product.get("category") or "",
                "Basisprijs": basis,
                "Kortingsregel": rule["name"] if rule else "",
                "Korting %": rule["discount_percent"] if rule else None,
                "Berekende inkoopprijs": calculated,
                "Huidige inkoopprijs": product.get("cost_price"),
            }
        )
    return result


def apply_purchase_costs(slug: str) -> dict[str, int]:
    preview = preview_purchase_costs(slug, limit=None)
    updated = 0
    skipped = 0
    with _connect(init_supplier_database(slug)) as conn:
        for row in preview:
            cost = row["Berekende inkoopprijs"]
            if cost is None:
                skipped += 1
                continue
            conn.execute(
                "UPDATE products SET cost_price=?,updated_at=? WHERE sku=?",
                (cost, utc_now(), row["SKU"]),
            )
            updated += 1
        conn.execute(
            """
            INSERT INTO purchase_discount_runs(applied_at,products_updated,message)
            VALUES(?,?,?)
            """,
            (utc_now(), updated, f"Overgeslagen zonder passende regel/basisprijs: {skipped}"),
        )
    return {"updated": updated, "skipped": skipped}


def preview_sales_prices(
    slug: str, max_discount_percent: float | None = None,
    limit: int | None = 500, rule_type: str | None = None,
) -> list[dict[str, Any]]:
    from app.suppliers.routes import supplier_route

    route = supplier_route(slug)
    """Bereken bruto prijs minus een percentage van de netto inkoopprijs."""
    options = (get_supplier(slug) or {}).get("request_options") or {}
    scoped_rules = (
        list_sales_price_rules(slug, include_disabled=False)
        if max_discount_percent is None and rule_type is None else []
    )
    percentage = float(
        max_discount_percent if max_discount_percent is not None
        else options.get("sales_max_discount_percent", 20)
    )
    selected_rule = rule_type or options.get(
        "sales_price_rule_type", "discount_from_cost"
    )
    if not 0 <= percentage <= 100:
        raise ValueError("De maximale klantkorting moet tussen 0% en 100% liggen.")
    query = """
        SELECT sku,source_title,product_type,category,category_full,
            price,cost_price,sale_price,
            gross_purchase_price_per_kg,kg_per_sales_unit
        FROM products WHERE source_present=1 ORDER BY sku
    """
    params: tuple[Any, ...] = ()
    if limit:
        query += " LIMIT ?"
        params = (int(limit),)
    with _connect(init_supplier_database(slug)) as conn:
        products = [dict(row) for row in conn.execute(query, params).fetchall()]
        surcharge_by_sku: dict[str, dict[str, Any]] = {}
        component_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_price_components'"
        ).fetchone()
        if route.applies_alloy_surcharges and component_table:
            surcharge_by_sku = {
                row["main_sku"]: dict(row) for row in conn.execute(
                    """SELECT main_sku,surcharge_total,base_net_price,
                              effective_gross_price,effective_net_price,
                              base_sales_price,status
                       FROM product_price_components
                       WHERE component_type='alloy_surcharge'"""
                ).fetchall()
            }
    result = []
    for product in products:
        applied_rule = _effective_rule(scoped_rules, product) if scoped_rules else None
        effective_type = (
            applied_rule["rule_type"] if applied_rule else selected_rule
        )
        effective_value = float(
            applied_rule["rule_value"] if applied_rule else percentage
        )
        gross = product.get("price")
        if (
            gross is None
            and product.get("gross_purchase_price_per_kg") is not None
            and product.get("kg_per_sales_unit") is not None
        ):
            gross = round(
                float(product["gross_purchase_price_per_kg"])
                * float(product["kg_per_sales_unit"]), 2
            )
        base_cost = product.get("cost_price")
        component = surcharge_by_sku.get(product["sku"]) or {}
        surcharge = (
            float(component.get("surcharge_total") or 0)
            if component.get("status") == "ready" else 0.0
        )
        cost = (
            component.get("effective_net_price")
            if component.get("status") == "ready"
            and component.get("effective_net_price") is not None
            else base_cost
        )
        effective_gross = (
            component.get("effective_gross_price")
            if component.get("status") == "ready"
            and component.get("effective_gross_price") is not None
            else round(float(gross) + surcharge, 2)
            if gross is not None else None
        )
        current_base = component.get("base_sales_price")
        if current_base is None and product.get("sale_price") is not None:
            current_base = float(product["sale_price"]) - surcharge
        if current_base is None and gross is not None:
            # 'Doe niets — gebruik het leveranciersveld' betekent dat de
            # reguliere bronprijs de veilige fallback is wanneer de bron geen
            # afzonderlijke aanbiedings-/verkoopprijs bevat.
            current_base = float(gross)
        underlying_rule = None
        if applied_rule and applied_rule["rule_type"] == "fixed_markup":
            # Een vaste euro-opslag is een aanvullende laag. Bereken eerst de
            # beste overige passende regel (bijv. algemene procentopslag) en
            # tel daarna het vaste bedrag op. Zo blijft de berekening
            # idempotent en wordt bij iedere synchronisatie niet opnieuw op de
            # reeds opgeslagen verkoopprijs gestapeld.
            underlying_rule = _effective_rule(
                [
                    rule for rule in scoped_rules
                    if rule["id"] != applied_rule["id"]
                    and rule["rule_type"] != "fixed_markup"
                ],
                product,
            )
        if underlying_rule:
            underlying_base = _calculated_sales_price(
                effective_gross, cost, float(underlying_rule["rule_value"]),
                underlying_rule["rule_type"],
            )
            calculated = (
                round(float(underlying_base) + effective_value, 2)
                if underlying_base is not None else None
            )
        else:
            calculated = (
                None if scoped_rules and not applied_rule else
                round(float(current_base) + surcharge, 2)
                if effective_type == "none" and current_base is not None else
                _calculated_sales_price(
                    effective_gross, cost, effective_value, effective_type
                )
            )
        calculated_base = (
            round(float(calculated) - surcharge, 2)
            if calculated is not None else None
        )
        customer_discount = (
            round(float(effective_gross) - float(calculated), 2)
            if calculated is not None and effective_gross is not None else None
        )
        preview_row = {
            "SKU": product["sku"],
            "Product": product.get("source_title") or "",
            "Bruto prijs": gross,
        }
        # Bij Certilas is de legeringstoeslag zowel een inkoop- als een
        # verkoopcomponent. Toon hem direct naast de bruto basisprijs.
        if route.applies_alloy_surcharges:
            preview_row["Legeringstoeslag"] = surcharge or None
        preview_row.update({
            "Netto inkoopprijs / kostprijs": cost,
            "Onze korting": (
                round(float(effective_gross) - float(cost), 2)
                if effective_gross is not None and cost is not None else None
            ),
            "Verkoopprijsregel": (
                f"{underlying_rule['name']} + {applied_rule['name']}"
                if underlying_rule and applied_rule else
                applied_rule["name"] if applied_rule else
                "Algemene oude instelling" if not scoped_rules else ""
            ),
            "Rekenmethode": SALES_RULE_TYPES.get(effective_type, effective_type),
            "Ingestelde waarde %": effective_value if (applied_rule or not scoped_rules) else None,
            "Klantkorting %": (
                round(
                    (float(effective_gross) - float(calculated))
                    / float(effective_gross) * 100, 2
                )
                if effective_gross is not None and calculated is not None
                and float(effective_gross) > 0 else None
            ),
            "Klantkorting": customer_discount,
            "Berekende basisverkoopprijs": calculated_base,
            "Berekende verkoopprijs": calculated,
            "Huidige verkoopprijs": product.get("sale_price"),
        })
        result.append(preview_row)
    return result


def _calculated_sales_price(
    gross: Any, cost: Any, value: float,
    rule_type: str = "discount_from_cost",
) -> float | None:
    if cost is None:
        return None
    cost_value = float(cost)
    if cost_value < 0:
        return None
    gross_value = float(gross) if gross is not None else None
    if rule_type == "markup_on_cost":
        result = cost_value * (1 + value / 100)
    elif rule_type == "max_discount_over_discount":
        if gross_value is None or gross_value <= 0:
            return None
        supplier_discount = max(0, gross_value - cost_value)
        result = gross_value - supplier_discount * value / 100
    elif rule_type == "gross_margin":
        if value >= 100:
            return None
        result = cost_value / (1 - value / 100)
    elif rule_type == "fixed_markup":
        result = cost_value + value
    else:
        if gross_value is None or gross_value <= 0:
            return None
        result = gross_value - cost_value * value / 100
    return round(max(0, result), 2)


def apply_sales_prices(
    slug: str, max_discount_percent: float | None = None,
    rule_type: str | None = None,
) -> dict[str, int]:
    from app.suppliers.routes import supplier_route

    route = supplier_route(slug)
    preview = preview_sales_prices(
        slug, max_discount_percent, limit=None, rule_type=rule_type
    )
    updated = skipped = 0
    with _connect(init_supplier_database(slug)) as conn:
        for row in preview:
            selling_price = row["Berekende verkoopprijs"]
            if selling_price is None:
                skipped += 1
                continue
            conn.execute(
                "UPDATE products SET sale_price=?,updated_at=? WHERE sku=?",
                (selling_price, utc_now(), row["SKU"]),
            )
            relation = conn.execute(
                """SELECT 1 FROM sqlite_master WHERE type='table'
                   AND name='product_price_components'"""
            ).fetchone()
            if route.applies_alloy_surcharges and relation:
                conn.execute(
                    """UPDATE product_price_components
                       SET base_sales_price=?,effective_sales_price=?,updated_at=?
                       WHERE main_sku=? AND component_type='alloy_surcharge'
                         AND status='ready'""",
                    (row.get("Berekende basisverkoopprijs"), selling_price,
                     utc_now(), row["SKU"]),
                )
            updated += 1
    return {"updated": updated, "skipped": skipped}


def save_sales_price_rule(
    slug: str, max_discount_percent: float, *,
    rule_type: str = "discount_from_cost", apply_existing: bool = True,
) -> dict[str, int]:
    percentage = float(max_discount_percent)
    if not 0 <= percentage <= 100:
        raise ValueError("De maximale klantkorting moet tussen 0% en 100% liggen.")
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    options = dict(supplier.get("request_options") or {})
    options.update({
        "sales_max_discount_percent": percentage,
        "sales_price_rule_enabled": rule_type != "none",
        "sales_price_rule_type": rule_type,
    })
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            "UPDATE suppliers SET request_options_json=?,updated_at=? WHERE slug=?",
            (json.dumps(options, ensure_ascii=False), utc_now(), slug),
        )
    return (
        apply_sales_prices(slug, percentage, rule_type)
        if apply_existing and rule_type != "none"
        else {"updated": 0, "skipped": 0}
    )
