from __future__ import annotations

import json
from typing import Any, Iterable

from app.suppliers.hub import REGISTRY_PATH, _connect, utc_now


def init_canonical_product_fields() -> None:
    with _connect(REGISTRY_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS canonical_product_brands (
                product_gid TEXT PRIMARY KEY,
                shopify_handle TEXT NOT NULL,
                brand TEXT NOT NULL,
                evidence TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS canonical_product_brand_skus (
                sku TEXT PRIMARY KEY,
                product_gid TEXT NOT NULL,
                FOREIGN KEY(product_gid) REFERENCES canonical_product_brands(product_gid)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_canonical_brand_handle
                ON canonical_product_brands(shopify_handle);
            """
        )


def upsert_canonical_brand(
    product_gid: str, shopify_handle: str, skus: Iterable[str], brand: str,
    evidence: str = "",
) -> dict[str, Any]:
    init_canonical_product_fields()
    product_gid = str(product_gid or "").strip()
    shopify_handle = str(shopify_handle or "").strip()
    brand = str(brand or "").strip()
    sku_values = list(dict.fromkeys(
        str(value or "").strip() for value in skus if str(value or "").strip()
    ))
    if not product_gid.startswith("gid://shopify/Product/"):
        raise ValueError("Een geldig Shopify-product-GID is verplicht.")
    if not shopify_handle or not sku_values or not brand:
        raise ValueError("Handle, minimaal één SKU en merk zijn verplicht.")
    now = utc_now()
    with _connect(REGISTRY_PATH) as conn:
        collisions = conn.execute(
            "SELECT sku,product_gid FROM canonical_product_brand_skus "
            f"WHERE sku IN ({','.join('?' for _ in sku_values)}) AND product_gid<>?",
            (*sku_values, product_gid),
        ).fetchall()
        if collisions:
            raise ValueError("SKU hoort al bij een ander product: " + ", ".join(
                f"{row['sku']} ({row['product_gid']})" for row in collisions
            ))
        conn.execute(
            """INSERT INTO canonical_product_brands(
                   product_gid,shopify_handle,brand,evidence,updated_at
               ) VALUES(?,?,?,?,?) ON CONFLICT(product_gid) DO UPDATE SET
                   shopify_handle=excluded.shopify_handle,
                   brand=excluded.brand,evidence=excluded.evidence,
                   updated_at=excluded.updated_at""",
            (product_gid, shopify_handle, brand, str(evidence or ""), now),
        )
        conn.execute(
            "DELETE FROM canonical_product_brand_skus WHERE product_gid=?",
            (product_gid,),
        )
        conn.executemany(
            "INSERT INTO canonical_product_brand_skus(sku,product_gid) VALUES(?,?)",
            [(sku, product_gid) for sku in sku_values],
        )
    return canonical_brand(product_gid)


def canonical_brand(product_gid: str) -> dict[str, Any]:
    init_canonical_product_fields()
    with _connect(REGISTRY_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM canonical_product_brands WHERE product_gid=?",
            (product_gid,),
        ).fetchone()
        if not row:
            return {}
        result = dict(row)
        result["skus"] = [r["sku"] for r in conn.execute(
            "SELECT sku FROM canonical_product_brand_skus "
            "WHERE product_gid=? ORDER BY sku COLLATE NOCASE", (product_gid,),
        ).fetchall()]
        return result


def canonical_brand_for_skus(*skus: Any) -> str:
    values = list(dict.fromkeys(
        str(value or "").strip() for value in skus if str(value or "").strip()
    ))
    if not values:
        return ""
    init_canonical_product_fields()
    with _connect(REGISTRY_PATH) as conn:
        rows = conn.execute(
            """SELECT s.sku,b.brand FROM canonical_product_brand_skus s
               JOIN canonical_product_brands b ON b.product_gid=s.product_gid
               WHERE s.sku IN (""" + ",".join("?" for _ in values) + ")",
            tuple(values),
        ).fetchall()
    brands = {str(row["brand"]).strip() for row in rows if str(row["brand"]).strip()}
    if len(brands) > 1:
        raise ValueError(
            "Conflicterende canonieke merken voor SKU-set: " + ", ".join(sorted(brands))
        )
    return next(iter(brands), "")


def list_canonical_brands(query: str = "") -> list[dict[str, Any]]:
    init_canonical_product_fields()
    with _connect(REGISTRY_PATH) as conn:
        rows = conn.execute(
            """SELECT b.*,GROUP_CONCAT(s.sku, ' | ') AS skus
               FROM canonical_product_brands b
               LEFT JOIN canonical_product_brand_skus s
                 ON s.product_gid=b.product_gid
               WHERE ?='' OR b.shopify_handle LIKE ? OR b.brand LIKE ?
                    OR s.sku LIKE ?
               GROUP BY b.product_gid
               ORDER BY b.brand COLLATE NOCASE,b.shopify_handle COLLATE NOCASE""",
            (query.strip(), *(f"%{query.strip()}%",) * 3),
        ).fetchall()
    return [dict(row) for row in rows]

def sync_canonical_brands_to_shopify() -> dict[str, Any]:
    """Write only custom.merk from the canonical PIM registry and read it back."""
    from app.shopify.client import ShopifyClient

    records = list_canonical_brands()
    client = ShopifyClient.from_settings()
    query = """query($ids:[ID!]!){nodes(ids:$ids){... on Product{
      id handle metafield(namespace:"custom",key:"merk"){value}
    }}}"""
    current: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(records), 50):
        ids = [row["product_gid"] for row in records[offset:offset + 50]]
        for product in client.graphql(query, {"ids": ids}).get("nodes") or []:
            if product:
                current[product["id"]] = product
    changes = []
    for row in records:
        product = current.get(row["product_gid"])
        if not product or product.get("handle") != row["shopify_handle"]:
            raise ValueError(
                f"Shopify-identiteitsdrift voor {row['product_gid']} / {row['shopify_handle']}"
            )
        live = ((product.get("metafield") or {}).get("value") or "").strip()
        if live != row["brand"]:
            changes.append({
                "ownerId": row["product_gid"], "namespace": "custom",
                "key": "merk", "type": "single_line_text_field",
                "value": row["brand"],
            })
    mutation = """mutation($metafields:[MetafieldsSetInput!]!){
      metafieldsSet(metafields:$metafields){
        metafields{id} userErrors{field message code}
      }}"""
    for offset in range(0, len(changes), 25):
        payload = client.graphql(
            mutation, {"metafields": changes[offset:offset + 25]}
        ).get("metafieldsSet") or {}
        if payload.get("userErrors"):
            raise RuntimeError(str(payload["userErrors"]))
    readback = {}
    for offset in range(0, len(records), 50):
        ids = [row["product_gid"] for row in records[offset:offset + 50]]
        for product in client.graphql(query, {"ids": ids}).get("nodes") or []:
            if product:
                readback[product["id"]] = (
                    (product.get("metafield") or {}).get("value") or ""
                ).strip()
    bad = [
        row["product_gid"] for row in records
        if readback.get(row["product_gid"]) != row["brand"]
    ]
    if bad:
        raise RuntimeError("Merkreadback mislukt: " + ", ".join(bad))
    return {
        "status": "GREEN_CANONICAL_BRAND_SYNC",
        "records": len(records), "writes": len(changes), "bad": [],
    }

CANONICAL_FILTER_KEYS = {
    "voltage", "lasproces", "ac_dc_of_dc", "machinefuncties",
    "vermogensklasse_lasmachine", "vermogensklasse_plasma",
    "toortsfamilie", "onderdeeltype",
}


def init_canonical_filter_values() -> None:
    with _connect(REGISTRY_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS canonical_product_filter_values (
                product_gid TEXT NOT NULL,
                field_key TEXT NOT NULL,
                shopify_handle TEXT NOT NULL,
                field_type TEXT NOT NULL,
                field_value TEXT NOT NULL,
                evidence TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(product_gid,field_key)
            );
            CREATE TABLE IF NOT EXISTS canonical_product_filter_skus (
                product_gid TEXT NOT NULL,
                field_key TEXT NOT NULL,
                sku TEXT NOT NULL,
                PRIMARY KEY(field_key,sku),
                FOREIGN KEY(product_gid,field_key)
                    REFERENCES canonical_product_filter_values(product_gid,field_key)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_canonical_filter_handle
                ON canonical_product_filter_values(shopify_handle);
            """
        )


def upsert_canonical_filter_value(
    product_gid: str, shopify_handle: str, skus: Iterable[str],
    field_key: str, field_type: str, field_value: str, evidence: str = "",
) -> dict[str, Any]:
    init_canonical_filter_values()
    product_gid = str(product_gid or "").strip()
    shopify_handle = str(shopify_handle or "").strip()
    field_key = str(field_key or "").strip()
    field_type = str(field_type or "").strip()
    field_value = str(field_value or "").strip()
    sku_values = list(dict.fromkeys(
        str(value or "").strip() for value in skus if str(value or "").strip()
    ))
    if not product_gid.startswith("gid://shopify/Product/"):
        raise ValueError("Een geldig Shopify-product-GID is verplicht.")
    if field_key not in CANONICAL_FILTER_KEYS:
        raise ValueError(f"Niet-toegestaan canoniek filterveld: {field_key}")
    if not shopify_handle or not field_type or not field_value:
        raise ValueError("Handle, veldtype en veldwaarde zijn verplicht.")
    if field_type.startswith("list."):
        parsed = json.loads(field_value)
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("Een lijstveld vereist een niet-lege JSON-lijst.")
        field_value = json.dumps(
            list(dict.fromkeys(str(value).strip() for value in parsed if str(value).strip())),
            ensure_ascii=False, separators=(",", ":"),
        )
    now = utc_now()
    with _connect(REGISTRY_PATH) as conn:
        if sku_values:
            collisions = conn.execute(
                "SELECT sku,product_gid FROM canonical_product_filter_skus "
                f"WHERE field_key=? AND sku IN ({','.join('?' for _ in sku_values)}) "
                "AND product_gid<>?",
                (field_key, *sku_values, product_gid),
            ).fetchall()
            if collisions:
                raise ValueError("SKU/veld hoort al bij een ander product: " + ", ".join(
                    f"{row['sku']} ({row['product_gid']})" for row in collisions
                ))
        conn.execute(
            """INSERT INTO canonical_product_filter_values(
                   product_gid,field_key,shopify_handle,field_type,field_value,
                   evidence,updated_at
               ) VALUES(?,?,?,?,?,?,?) ON CONFLICT(product_gid,field_key) DO UPDATE SET
                   shopify_handle=excluded.shopify_handle,
                   field_type=excluded.field_type,field_value=excluded.field_value,
                   evidence=excluded.evidence,updated_at=excluded.updated_at""",
            (product_gid, field_key, shopify_handle, field_type, field_value,
             str(evidence or ""), now),
        )
        conn.execute(
            "DELETE FROM canonical_product_filter_skus "
            "WHERE product_gid=? AND field_key=?", (product_gid, field_key),
        )
        conn.executemany(
            "INSERT INTO canonical_product_filter_skus("
            "product_gid,field_key,sku) VALUES(?,?,?)",
            [(product_gid, field_key, sku) for sku in sku_values],
        )
    return {
        "product_gid": product_gid, "field_key": field_key,
        "shopify_handle": shopify_handle, "field_type": field_type,
        "field_value": field_value, "skus": sku_values,
    }


def list_canonical_filter_values(query: str = "") -> list[dict[str, Any]]:
    init_canonical_filter_values()
    q = query.strip()
    with _connect(REGISTRY_PATH) as conn:
        rows = conn.execute(
            """SELECT v.*,GROUP_CONCAT(s.sku, ' | ') AS skus
               FROM canonical_product_filter_values v
               LEFT JOIN canonical_product_filter_skus s
                 ON s.product_gid=v.product_gid AND s.field_key=v.field_key
               WHERE ?='' OR v.shopify_handle LIKE ? OR v.field_key LIKE ?
                    OR v.field_value LIKE ? OR s.sku LIKE ?
               GROUP BY v.product_gid,v.field_key
               ORDER BY v.shopify_handle COLLATE NOCASE,v.field_key COLLATE NOCASE""",
            (q, *(f"%{q}%",) * 4),
        ).fetchall()
    return [dict(row) for row in rows]


def canonical_filter_metafields_for_skus(*skus: Any) -> list[dict[str, str]]:
    values = list(dict.fromkeys(
        str(value or "").strip() for value in skus if str(value or "").strip()
    ))
    if not values:
        return []
    init_canonical_filter_values()
    with _connect(REGISTRY_PATH) as conn:
        rows = conn.execute(
            """SELECT v.field_key,v.field_type,v.field_value
               FROM canonical_product_filter_skus s
               JOIN canonical_product_filter_values v
                 ON v.product_gid=s.product_gid AND v.field_key=s.field_key
               WHERE s.sku IN (""" + ",".join("?" for _ in values) + ")",
            tuple(values),
        ).fetchall()
    grouped: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["field_key"], set()).add(
            (row["field_type"], row["field_value"])
        )
    conflicts = {key: vals for key, vals in grouped.items() if len(vals) > 1}
    if conflicts:
        raise ValueError("Conflicterende canonieke filterwaarden: " + ", ".join(conflicts))
    return [
        {"namespace": "custom", "key": key, "type": next(iter(vals))[0],
         "value": next(iter(vals))[1]}
        for key, vals in sorted(grouped.items())
    ]


def sync_canonical_filter_values_to_shopify() -> dict[str, Any]:
    from app.shopify.client import ShopifyClient

    records = list_canonical_filter_values()
    by_product: dict[str, dict[str, Any]] = {}
    for row in records:
        item = by_product.setdefault(row["product_gid"], {
            "handle": row["shopify_handle"], "fields": []})
        item["fields"].append(row)
    client = ShopifyClient.from_settings()
    query = """query($ids:[ID!]!){nodes(ids:$ids){... on Product{
      id handle metafields(first:100){nodes{namespace key type value}}
    }}}"""
    current: dict[str, dict[str, Any]] = {}
    ids = list(by_product)
    for offset in range(0, len(ids), 50):
        for product in client.graphql(query, {"ids": ids[offset:offset + 50]}).get("nodes") or []:
            if product:
                current[product["id"]] = product
    changes = []
    for product_gid, item in by_product.items():
        product = current.get(product_gid)
        if not product or product.get("handle") != item["handle"]:
            raise ValueError(f"Shopify-identiteitsdrift voor {product_gid}")
        live = {(m["namespace"], m["key"]): m for m in
                (product.get("metafields") or {}).get("nodes") or []}
        for row in item["fields"]:
            old = live.get(("custom", row["field_key"]))
            if old and old.get("type") == row["field_type"] and old.get("value") == row["field_value"]:
                continue
            changes.append({
                "ownerId": product_gid, "namespace": "custom",
                "key": row["field_key"], "type": row["field_type"],
                "value": row["field_value"],
            })
    mutation = """mutation($metafields:[MetafieldsSetInput!]!){
      metafieldsSet(metafields:$metafields){metafields{id} userErrors{field message code}}
    }"""
    for offset in range(0, len(changes), 25):
        result = client.graphql(
            mutation, {"metafields": changes[offset:offset + 25]}
        ).get("metafieldsSet") or {}
        if result.get("userErrors"):
            raise RuntimeError(str(result["userErrors"]))
    return {"status": "GREEN_CANONICAL_FILTER_SYNC", "records": len(records),
            "products": len(by_product), "writes": len(changes), "bad": []}

def canonical_filter_metafields_for_product(
    product_gid: Any, shopify_handle: Any, *skus: Any,
) -> list[dict[str, str]]:
    """Prefer exact Shopify identity; fall back to SKU only when unambiguous."""
    product_gid = str(product_gid or "").strip()
    shopify_handle = str(shopify_handle or "").strip()
    init_canonical_filter_values()
    rows = []
    with _connect(REGISTRY_PATH) as conn:
        if product_gid:
            rows = conn.execute(
                "SELECT field_key,field_type,field_value "
                "FROM canonical_product_filter_values WHERE product_gid=?",
                (product_gid,),
            ).fetchall()
        if not rows and shopify_handle:
            rows = conn.execute(
                "SELECT field_key,field_type,field_value "
                "FROM canonical_product_filter_values WHERE shopify_handle=?",
                (shopify_handle,),
            ).fetchall()
    if rows:
        return [
            {"namespace": "custom", "key": row["field_key"],
             "type": row["field_type"], "value": row["field_value"]}
            for row in sorted(rows, key=lambda row: row["field_key"])
        ]
    return canonical_filter_metafields_for_skus(*skus)
