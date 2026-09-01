from __future__ import annotations

import json
import sqlite3
from typing import Any


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def apply_certilas_alloy_surcharge_bundles(
    connection: sqlite3.Connection, updated_at: str,
) -> dict[str, int]:
    """Koppel ZZ-toeslagen en bereken hoofdprijs + gewicht × ZZ-prijs."""
    connection.executescript(
        """CREATE TABLE IF NOT EXISTS product_price_components(
             main_sku TEXT NOT NULL,
             component_sku TEXT NOT NULL,
             component_type TEXT NOT NULL DEFAULT 'alloy_surcharge',
             quantity REAL,
             quantity_source TEXT NOT NULL DEFAULT 'kg_per_sales_unit',
             base_gross_price REAL,
             base_net_price REAL,
             component_unit_price REAL,
             surcharge_total REAL,
             effective_gross_price REAL,
             effective_net_price REAL,
             base_sales_price REAL,
             effective_sales_price REAL,
             status TEXT NOT NULL,
             message TEXT NOT NULL DEFAULT '',
             updated_at TEXT NOT NULL,
             PRIMARY KEY(main_sku,component_sku),
             FOREIGN KEY(main_sku) REFERENCES products(sku) ON DELETE CASCADE
           );
           CREATE INDEX IF NOT EXISTS idx_price_components_component
             ON product_price_components(component_sku);"""
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(product_price_components)")}
    if "base_sales_price" not in columns:
        connection.execute("ALTER TABLE product_price_components ADD COLUMN base_sales_price REAL")
    if "effective_sales_price" not in columns:
        connection.execute("ALTER TABLE product_price_components ADD COLUMN effective_sales_price REAL")
    connection.execute("DELETE FROM product_price_components WHERE component_type='alloy_surcharge'")
    rows = connection.execute(
        """SELECT sku,raw_data_json,kg_per_sales_unit
           FROM products
           WHERE CAST(json_extract(raw_data_json,'$."Alloy surcharges"') AS TEXT) LIKE 'ZZ%'"""
    ).fetchall()
    stats = {"relations": 0, "applied": 0, "missing_weight": 0, "missing_base_price": 0, "missing_component": 0}
    for row in rows:
        main_sku = row["sku"] if isinstance(row, sqlite3.Row) else row[0]
        raw_json = row["raw_data_json"] if isinstance(row, sqlite3.Row) else row[1]
        weight_value = row["kg_per_sales_unit"] if isinstance(row, sqlite3.Row) else row[2]
        raw = json.loads(raw_json or "{}")
        component_sku = str(raw.get("Alloy surcharges") or "").strip().upper()
        weight = _number(weight_value)
        base_gross_per_kg = _number(raw.get("Gross price per KG"))
        base_net_per_kg = _number(raw.get("Net price per KG"))
        component = connection.execute(
            "SELECT gross_purchase_price_per_kg FROM products WHERE sku=?",
            (component_sku,),
        ).fetchone()
        component_price = _number(component[0]) if component else None
        status, message = "ready", ""
        if component_price is None:
            status, message = "missing_component", f"Prijsregel {component_sku} ontbreekt."
        elif weight is None:
            status, message = "missing_weight", "Verpakkingsgewicht ontbreekt."
        elif base_gross_per_kg is None or base_net_per_kg is None:
            status, message = "missing_base_price", "Bruto- of netto basisprijs ontbreekt."
        base_gross = base_gross_per_kg * weight if base_gross_per_kg and weight else None
        base_net = base_net_per_kg * weight if base_net_per_kg and weight else None
        surcharge = component_price * weight if component_price and weight else None
        effective_gross = base_gross + surcharge if base_gross is not None and surcharge is not None else None
        effective_net = base_net + surcharge if base_net is not None and surcharge is not None else None
        base_sales = base_gross
        effective_sales = base_sales + surcharge if base_sales is not None and surcharge is not None else None
        connection.execute(
            """INSERT INTO product_price_components(
               main_sku,component_sku,quantity,base_gross_price,base_net_price,
               component_unit_price,surcharge_total,effective_gross_price,
               effective_net_price,base_sales_price,effective_sales_price,
               status,message,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (main_sku,component_sku,weight,base_gross,base_net,component_price,
             surcharge,effective_gross,effective_net,base_sales,effective_sales,
             status,message,updated_at),
        )
        stats["relations"] += 1
        if status == "ready":
            # De ZZ-toeslag betalen we ook aan de leverancier en hoort daarom
            # in de effectieve kostprijs van het verkoopartikel.
            connection.execute(
                """UPDATE products SET gross_purchase_price_per_kg=?,
                   net_purchase_price_per_kg=?,sale_price=?,cost_price=?,updated_at=?
                   WHERE sku=?""",
                (base_gross_per_kg, base_net_per_kg, round(effective_sales, 2),
                 round(effective_net, 2), updated_at, main_sku),
            )
            stats["applied"] += 1
        else:
            stats[status] += 1
    return stats
