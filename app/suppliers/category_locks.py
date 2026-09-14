"""Persist explicitly approved category imports across every supplier writer.

The SQLite guard also covers long-running workers and enrichment scripts.
Changing these categories intentionally requires updating/removing their lock.
"""
import json
import sqlite3

CATEGORY_FIELDS = {
    "productgroep": "product_group_name",
    "uitvoering": "execution",
    "filter": "filter_values_json",
    "subcategorie_3": "subcategory_3",
    "subcategorie_4": "subcategory_4",
    "subcategorie_5": "subcategory_5",
}


def locked_metafields(product: dict) -> dict[str, str] | None:
    raw = product.get("_raw_data")
    if raw is None:
        raw = json.loads(product.get("raw_data_json") or "{}")
    evidence = raw.get("category_file_import") or {}
    if not evidence.get("locked"):
        return None
    values = evidence["values"]
    return {
        key: (" | ".join(json.loads(values[field])) if key == "filter" else values[field])
        for key, field in CATEGORY_FIELDS.items()
    }


def install_category_locks(conn: sqlite3.Connection, records: list[dict]) -> None:
    """Caller owns the transaction; records contain sku and import provenance."""
    conn.execute("""CREATE TABLE IF NOT EXISTS category_import_locks (
        sku TEXT PRIMARY KEY, values_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL
    )""")
    for record in records:
        provenance = dict(record["provenance"], locked=True)
        values = provenance["values"]
        if set(values) != set(CATEGORY_FIELDS.values()):
            raise ValueError("A category lock must contain all six fields")
        conn.execute("""INSERT INTO category_import_locks VALUES (?,?,?)
            ON CONFLICT(sku) DO UPDATE SET values_json=excluded.values_json,
            provenance_json=excluded.provenance_json""",
            (record["sku"], json.dumps(values, ensure_ascii=False),
             json.dumps(provenance, ensure_ascii=False)))
    fields = list(CATEGORY_FIELDS.values())
    assignments = ",".join(
        f"{field}=json_extract((SELECT values_json FROM category_import_locks WHERE sku=NEW.sku),'$.{field}')"
        for field in fields
    )
    differences = " OR ".join(
        f"NEW.{field} IS NOT json_extract(l.values_json,'$.{field}')" for field in fields
    )
    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS preserve_category_import
        AFTER UPDATE OF {','.join(fields)},raw_data_json ON products
        WHEN EXISTS(SELECT 1 FROM category_import_locks l WHERE l.sku=NEW.sku
            AND ({differences} OR
                json_extract(NEW.raw_data_json,'$.category_file_import') IS NOT
                json(l.provenance_json)))
        BEGIN
            UPDATE products SET {assignments},
                raw_data_json=json_set(COALESCE(raw_data_json,'{{}}'),
                    '$.category_file_import', json((SELECT provenance_json
                    FROM category_import_locks WHERE sku=NEW.sku)))
                WHERE sku=NEW.sku;
        END""")
    # Apply/repair the approved values and evidence immediately.
    conn.execute("""UPDATE products SET raw_data_json=COALESCE(raw_data_json,'{}')
        WHERE sku IN (SELECT sku FROM category_import_locks)""")
