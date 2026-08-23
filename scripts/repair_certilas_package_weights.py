from __future__ import annotations

import json

from app.suppliers.hub import (
    _connect, _package_weight, get_supplier, init_supplier_database, utc_now,
)


def main() -> None:
    supplier = get_supplier("certilas") or {}
    mapping = supplier.get("field_mapping") or {}
    purchase_field = mapping.get("kg_per_purchase_unit")
    sales_field = mapping.get("kg_per_sales_unit")
    updated = 0
    with _connect(init_supplier_database("certilas")) as conn:
        rows = conn.execute(
            "SELECT sku,raw_data_json,net_purchase_price_per_kg FROM products "
            "WHERE source_present=1"
        ).fetchall()
        for row in rows:
            raw = json.loads(row["raw_data_json"] or "{}")
            purchase_weight = _package_weight(raw.get(purchase_field))
            sales_weight = _package_weight(raw.get(sales_field))
            # Prijs altijd één verpakking; bundels worden afzonderlijk opgebouwd.
            if purchase_weight:
                sales_weight = purchase_weight
            net_per_kg = row["net_purchase_price_per_kg"]
            cost = (
                round(float(net_per_kg) * sales_weight, 2)
                if net_per_kg is not None and sales_weight else net_per_kg
            )
            conn.execute(
                "UPDATE products SET kg_per_purchase_unit=?,kg_per_sales_unit=?,"
                "cost_price=?,updated_at=? WHERE sku=?",
                (purchase_weight, sales_weight, cost, utc_now(), row["sku"]),
            )
            updated += 1
    print({"updated": updated})


if __name__ == "__main__":
    main()
