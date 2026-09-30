"""Persist explicitly approved category imports across every supplier writer.

The SQLite guard also covers long-running workers and enrichment scripts.
Changing these categories intentionally requires updating/removing their lock.
"""
import json
import sqlite3
from typing import Any


LEGACY_CATEGORY_FIELDS = {
    "productgroep": "product_group_name",
    "uitvoering": "execution",
    "filter": "filter_values_json",
    "subcategorie_3": "subcategory_3",
    "subcategorie_4": "subcategory_4",
    "subcategorie_5": "subcategory_5",
}

CATEGORY_FIELDS = dict(LEGACY_CATEGORY_FIELDS)

ADDITIONAL_CUSTOM_METAFIELD_KEYS = (
    "productsoort",
    "aandrijving",
    "model",
    "capaciteit",
)

COLLECTION_TAG_VALUE_KEYS = (
    "collection_tags_json",
    "collection_tags",
    "tags_json",
    "tags",
)


def _raw_data(product: dict) -> dict[str, Any]:
    raw = product.get("_raw_data")
    if raw is None:
        try:
            raw = json.loads(product.get("raw_data_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _locked_values(product: dict) -> dict[str, Any] | None:
    evidence = _raw_data(product).get("category_file_import") or {}
    if not isinstance(evidence, dict) or not evidence.get("locked"):
        return None
    values = evidence.get("values") or {}
    return values if isinstance(values, dict) else None


def locked_tree_values(product: dict) -> dict[str, Any] | None:
    """Return explicit values from the existing approved import/lock record."""
    return _locked_values(product)


def _filter_value(value: Any) -> str:
    if isinstance(value, list):
        return " | ".join(str(part) for part in value if str(part or "").strip())
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        parsed = []
    if isinstance(parsed, list):
        return " | ".join(str(part) for part in parsed if str(part or "").strip())
    return str(value or "")


def locked_metafields(product: dict) -> dict[str, str] | None:
    values = _locked_values(product)
    if values is None:
        return None

    metafields: dict[str, str] = {}
    for key, field in LEGACY_CATEGORY_FIELDS.items():
        value = values.get(field)
        if key == "filter":
            value = _filter_value(value)
        elif value is None:
            value = ""
        metafields[key] = str(value)

    for key in ADDITIONAL_CUSTOM_METAFIELD_KEYS:
        value = values.get(key)
        if value not in (None, ""):
            metafields[key] = str(value)
    return metafields


def locked_collection_tags(product: dict) -> list[str]:
    values = _locked_values(product)
    if values is None:
        return []

    tags: list[str] = []
    for key in COLLECTION_TAG_VALUE_KEYS:
        source = values.get(key)
        if source in (None, ""):
            continue
        if isinstance(source, list):
            tags.extend(str(tag).strip() for tag in source)
            continue
        try:
            parsed = json.loads(str(source))
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            tags.extend(str(tag).strip() for tag in parsed)
        else:
            tags.extend(part.strip() for part in str(source).split(","))
    return list(dict.fromkeys(tag for tag in tags if tag))


def install_category_locks(conn: sqlite3.Connection, records: list[dict]) -> None:
    """Caller owns the transaction; records contain sku and import provenance."""
    conn.execute("""CREATE TABLE IF NOT EXISTS category_import_locks (
        sku TEXT PRIMARY KEY, values_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL
    )""")
    required_fields = set(LEGACY_CATEGORY_FIELDS.values())
    for record in records:
        provenance = dict(record["provenance"], locked=True)
        values = provenance["values"]
        missing = required_fields - set(values)
        if missing:
            raise ValueError(
                "A category lock must contain legacy fields: "
                + ", ".join(sorted(missing))
            )
        conn.execute("""INSERT INTO category_import_locks VALUES (?,?,?)
            ON CONFLICT(sku) DO UPDATE SET values_json=excluded.values_json,
            provenance_json=excluded.provenance_json""",
            (record["sku"], json.dumps(values, ensure_ascii=False),
             json.dumps(provenance, ensure_ascii=False)))
    fields = list(LEGACY_CATEGORY_FIELDS.values())
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
