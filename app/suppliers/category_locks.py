"""Persist explicitly approved category imports across every supplier writer.

The SQLite guard also covers long-running workers and enrichment scripts.
Changing these categories intentionally requires updating/removing their lock.
"""
import json
import hashlib
import sqlite3
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping


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

# Typed tree fields are opt-in and identified by their exact Shopify identifier.
# Registry membership protects a field; it does not wire it into normal sync.
TYPED_CUSTOM_METAFIELD_TYPES = MappingProxyType({
    "custom.filter_hg_type": "list.single_line_text_field",
})
STOREFRONT_FILTER_HG_TYPE_VALUES_KEY = "storefront_filter_hg_type_values_json"
TYPED_CUSTOM_VALUE_KEYS = MappingProxyType({
    # Existing explicit typed-lock contract retained for backwards compatibility.
    "filter_hg_type": "custom.filter_hg_type",
    # Near-30 bounded optional contract. Presence is never inferred or backfilled.
    STOREFRONT_FILTER_HG_TYPE_VALUES_KEY: "custom.filter_hg_type",
})

# Shopify-compatible safety limits for list.single_line_text_field values.
TYPED_LIST_MAX_ITEMS = 128
TYPED_LIST_MAX_ITEM_UTF8_BYTES = 255
TYPED_LIST_MAX_ENCODED_UTF8_BYTES = 32768

# Fail closed for Unicode controls and explicit line/paragraph separators.
# Other categories, including letters, combining marks, symbols and ordinary
# space separators, remain valid so normal international text is preserved.
TYPED_LIST_DISALLOWED_UNICODE_CATEGORIES = frozenset({
    "Cc", "Cf", "Cs", "Zl", "Zp",
})


@dataclass(frozen=True)
class CategoryImportLockAuthority:
    sku: str
    supplier_slug: str
    supplier_vendor: str
    values_json: str
    provenance_json: str
    values: Mapping[str, Any]
    provenance: Mapping[str, Any]
    row_digest: str

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


def _canonical_string_list(
    value: Any, identifier: str, *, require_canonical: bool = True,
) -> str:
    """Validate and canonically encode one approved Shopify text-list value."""
    if not isinstance(value, str):
        raise ValueError(f"{identifier} must be serialized JSON")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{identifier} must be a JSON array") from exc
    if not isinstance(parsed, list) or not parsed:
        raise ValueError(f"{identifier} must be a non-empty JSON array")
    if len(parsed) > TYPED_LIST_MAX_ITEMS:
        raise ValueError(f"{identifier} exceeds {TYPED_LIST_MAX_ITEMS} items")
    normalized: list[str] = []
    for item in parsed:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{identifier} items must be non-empty strings")
        if any(
            unicodedata.category(character) in TYPED_LIST_DISALLOWED_UNICODE_CATEGORIES
            for character in item
        ):
            raise ValueError(
                f"{identifier} items must not contain Unicode controls, format/surrogate characters, or line/paragraph separators"
            )
        if "|" in item:
            raise ValueError(f"{identifier} items must not be pipe-joined")
        clean = unicodedata.normalize("NFC", item)
        if clean != item or clean != clean.strip():
            raise ValueError(f"{identifier} must use trimmed NFC-normalized strings")
        if len(clean.encode("utf-8")) > TYPED_LIST_MAX_ITEM_UTF8_BYTES:
            raise ValueError(
                f"{identifier} item exceeds {TYPED_LIST_MAX_ITEM_UTF8_BYTES} UTF-8 bytes"
            )
        if clean in normalized:
            raise ValueError(f"{identifier} items must be unique")
        normalized.append(clean)
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if require_canonical and value != encoded:
        raise ValueError(f"{identifier} must use canonical serialized JSON")
    if len(encoded.encode("utf-8")) > TYPED_LIST_MAX_ENCODED_UTF8_BYTES:
        raise ValueError(
            f"{identifier} exceeds {TYPED_LIST_MAX_ENCODED_UTF8_BYTES} encoded UTF-8 bytes"
        )
    return encoded


def _validated_typed_metafields(
    values: Mapping[str, Any], provenance: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    direct = {key: values[key] for key in TYPED_CUSTOM_VALUE_KEYS if key in values}
    envelope = values.get("typed_custom_metafields", {})
    if envelope in (None, ""):
        envelope = {}
    if not isinstance(envelope, dict):
        raise ValueError("typed_custom_metafields must be an object")
    unknown_direct = sorted(
        key for key in values
        if (
            str(key).startswith("filter_hg_")
            or str(key).startswith("storefront_filter_hg_")
        ) and key not in TYPED_CUSTOM_VALUE_KEYS
    )
    unknown_envelope = sorted(set(envelope) - set(TYPED_CUSTOM_METAFIELD_TYPES))
    unknown = [*unknown_direct, *unknown_envelope]
    if unknown:
        raise ValueError("Unknown typed custom metafield(s): " + ", ".join(unknown))
    candidates: dict[str, dict[str, str]] = {}
    for key, value in direct.items():
        identifier = TYPED_CUSTOM_VALUE_KEYS[key]
        encoded = _canonical_string_list(value, key)
        candidate = {
            "type": TYPED_CUSTOM_METAFIELD_TYPES[identifier],
            "value": encoded,
        }
        if identifier in candidates and candidates[identifier] != candidate:
            raise ValueError(f"Conflicting approved values for {identifier}")
        candidates[identifier] = candidate
    for identifier, declaration in envelope.items():
        if not isinstance(declaration, dict) or set(declaration) != {"type", "value"}:
            raise ValueError(f"{identifier} must declare exactly type and value")
        if declaration["type"] != TYPED_CUSTOM_METAFIELD_TYPES[identifier]:
            raise ValueError(f"Unknown or mismatched typed custom metafield type: {identifier}")
        candidate = {
            "type": declaration["type"],
            "value": _canonical_string_list(declaration["value"], identifier),
        }
        if identifier in candidates and candidates[identifier] != candidate:
            raise ValueError(f"Conflicting approved values for {identifier}")
        candidates[identifier] = candidate
    if candidates and not str(provenance.get("source") or "").strip():
        raise ValueError("Typed custom metafields require lock provenance source")
    return candidates


def _lock_row_digest(sku: str, values_json: str, provenance_json: str) -> str:
    payload = "\0".join((sku, values_json, provenance_json)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verified_category_import_lock(
    lock_reader: Callable[[str], Mapping[str, Any] | None],
    *, sku: str, supplier_slug: str, supplier_vendor: str,
    expected_digest: str,
) -> CategoryImportLockAuthority:
    """Build authority only from an exact category_import_locks DB row readback."""
    if not all(isinstance(value, str) and value.strip() for value in (
        sku, supplier_slug, supplier_vendor, expected_digest,
    )):
        raise ValueError("Lock authority requires exact non-empty identity and digest")
    row = lock_reader(sku)
    if not isinstance(row, Mapping) or set(row) < {
        "sku", "values_json", "provenance_json",
    }:
        raise ValueError("Verified category import lock row is missing")
    row_sku = row.get("sku")
    values_json = row.get("values_json")
    provenance_json = row.get("provenance_json")
    if row_sku != sku or not isinstance(values_json, str) or not isinstance(provenance_json, str):
        raise ValueError("Category import lock row identity mismatch")
    digest = _lock_row_digest(sku, values_json, provenance_json)
    if digest != expected_digest:
        raise ValueError("Category import lock row is stale or digest-mismatched")
    try:
        values = json.loads(values_json)
        provenance = json.loads(provenance_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Category import lock row contains invalid JSON") from exc
    if not isinstance(values, dict) or not isinstance(provenance, dict):
        raise ValueError("Category import lock row must contain JSON objects")
    if provenance.get("locked") is not True:
        raise ValueError("Category import lock row is not locked")
    if provenance.get("sku") != sku:
        raise ValueError("Category import lock provenance SKU mismatch")
    if provenance.get("supplier_slug") != supplier_slug:
        raise ValueError("Category import lock supplier slug mismatch")
    if provenance.get("supplier_vendor") != supplier_vendor:
        raise ValueError("Category import lock supplier ownership mismatch")
    if not str(provenance.get("source") or "").strip():
        raise ValueError("Category import lock provenance source is missing")
    if provenance.get("values") != values:
        raise ValueError("Category import lock values/provenance mismatch")
    _validated_typed_metafields(values, provenance)
    return CategoryImportLockAuthority(
        sku=sku, supplier_slug=supplier_slug, supplier_vendor=supplier_vendor,
        values_json=values_json, provenance_json=provenance_json,
        values=MappingProxyType(values), provenance=MappingProxyType(provenance),
        row_digest=digest,
    )


def typed_metafields_from_authority(
    authority: CategoryImportLockAuthority,
) -> dict[str, dict[str, str]]:
    if not isinstance(authority, CategoryImportLockAuthority):
        raise ValueError("Typed metafields require verified DB lock authority")
    if _lock_row_digest(
        authority.sku, authority.values_json, authority.provenance_json,
    ) != authority.row_digest:
        raise ValueError("Verified DB lock authority digest changed")
    values = json.loads(authority.values_json)
    provenance = json.loads(authority.provenance_json)
    if values != dict(authority.values) or provenance != dict(authority.provenance):
        raise ValueError("Verified DB lock authority payload changed")
    if provenance.get("sku") != authority.sku:
        raise ValueError("Verified DB lock authority SKU changed")
    if provenance.get("supplier_slug") != authority.supplier_slug:
        raise ValueError("Verified DB lock authority supplier slug changed")
    if provenance.get("supplier_vendor") != authority.supplier_vendor:
        raise ValueError("Verified DB lock authority ownership changed")
    return _validated_typed_metafields(values, provenance)


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
        if not isinstance(values, dict):
            raise ValueError("A category lock must contain an object of values")
        _validated_typed_metafields(values, provenance)
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
