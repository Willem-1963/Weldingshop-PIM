from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.shopify.client import ShopifyClient
from app.suppliers.hub import (
    REGISTRY_PATH, _connect, init_registry, supplier_database_path, utc_now,
)


REQUIRED_COLUMNS = {
    "bundle_product_id",
    "bundle_variant_id",
    "bundle_product_title",
    "bundle_product_status",
    "bundle_variant_title",
    "bundle_variant_price",
    "bundle_variant_sku",
    "bundle_item_quantity",
    "bundle_item_product_id",
    "bundle_item_variant_id",
    "bundle_item_product_title",
    "bundle_item_variant_title",
    "bundle_item_variant_price",
    "bundle_item_variant_sku",
}


def init_bundle_store() -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bundle_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                bundle_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bundle_variants (
                bundle_variant_id TEXT PRIMARY KEY,
                bundle_product_id TEXT NOT NULL,
                product_title TEXT NOT NULL,
                product_status TEXT NOT NULL,
                variant_title TEXT,
                variant_sku TEXT,
                variant_price REAL,
                sync_price INTEGER NOT NULL DEFAULT 0,
                selected INTEGER NOT NULL DEFAULT 0,
                valid INTEGER NOT NULL DEFAULT 1,
                validation_message TEXT,
                image_url TEXT,
                import_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(import_id) REFERENCES bundle_imports(id)
            );
            CREATE TABLE IF NOT EXISTS bundle_components (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bundle_variant_id TEXT NOT NULL,
                component_product_id TEXT NOT NULL,
                component_variant_id TEXT NOT NULL,
                product_title TEXT NOT NULL,
                variant_title TEXT,
                variant_sku TEXT,
                quantity INTEGER NOT NULL,
                variant_price REAL,
                image_url TEXT,
                UNIQUE(bundle_variant_id, component_variant_id),
                FOREIGN KEY(bundle_variant_id)
                    REFERENCES bundle_variants(bundle_variant_id) ON DELETE CASCADE
            );
            """
        )
        columns = {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(bundle_variants)"
            ).fetchall()
        }
        if "manual_override" not in columns:
            conn.execute(
                "ALTER TABLE bundle_variants ADD COLUMN manual_override INTEGER NOT NULL DEFAULT 0"
            )


def _number(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def import_bundle_csv(content: bytes, source_name: str) -> dict[str, Any]:
    init_bundle_store()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    missing = REQUIRED_COLUMNS.difference(reader.fieldnames or [])
    if missing:
        raise ValueError("Verplichte kolommen ontbreken: " + ", ".join(sorted(missing)))
    rows = list(reader)
    if not rows:
        raise ValueError("Het bundelbestand bevat geen regels.")

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("bundle_variant_id"):
            grouped[row["bundle_variant_id"]].append(row)

    now = utc_now()
    source_hash = hashlib.sha256(content).hexdigest()
    with _connect(REGISTRY_PATH) as conn:
        existing_selection = {
            row["bundle_variant_id"]: int(row["selected"])
            for row in conn.execute(
                "SELECT bundle_variant_id,selected FROM bundle_variants"
            ).fetchall()
        }
        cursor = conn.execute(
            """
            INSERT INTO bundle_imports(
                source_name,source_hash,imported_at,row_count,bundle_count
            ) VALUES(?,?,?,?,?)
            """,
            (source_name, source_hash, now, len(rows), len(grouped)),
        )
        import_id = int(cursor.lastrowid)
        conn.execute(
            """DELETE FROM bundle_components WHERE bundle_variant_id IN(
               SELECT bundle_variant_id FROM bundle_variants WHERE manual_override=0)"""
        )
        conn.execute("DELETE FROM bundle_variants WHERE manual_override=0")

        valid_count = 0
        for variant_id, variant_rows in grouped.items():
            first = variant_rows[0]
            component_rows = [
                row
                for row in variant_rows
                if row.get("bundle_item_product_id")
                and row.get("bundle_item_variant_id")
                and (row.get("bundle_item_quantity") or "").isdigit()
                and int(row["bundle_item_quantity"]) > 0
            ]
            messages: list[str] = []
            if not component_rows:
                messages.append("Geen geldige componentregels")
            if len(component_rows) > 30:
                messages.append("Meer dan 30 componenten")
            component_ids = [row["bundle_item_variant_id"] for row in component_rows]
            if len(component_ids) != len(set(component_ids)):
                messages.append("Dubbele componentvariant")
            valid = not messages
            valid_count += int(valid)
            selected = existing_selection.get(variant_id, 0) if valid else 0
            conn.execute(
                """
                INSERT INTO bundle_variants(
                    bundle_variant_id,bundle_product_id,product_title,
                    product_status,variant_title,variant_sku,variant_price,
                    sync_price,selected,valid,validation_message,import_id,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    variant_id,
                    first["bundle_product_id"],
                    first["bundle_product_title"],
                    first["bundle_product_status"],
                    first.get("bundle_variant_title") or "",
                    first.get("bundle_variant_sku") or "",
                    _number(first.get("bundle_variant_price")),
                    int((first.get("sync_price") or "").upper() == "TRUE"),
                    selected,
                    int(valid),
                    "; ".join(messages),
                    import_id,
                    now,
                ),
            )
            for row in component_rows:
                conn.execute(
                    """
                    INSERT INTO bundle_components(
                        bundle_variant_id,component_product_id,component_variant_id,
                        product_title,variant_title,variant_sku,quantity,variant_price
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        variant_id,
                        row["bundle_item_product_id"],
                        row["bundle_item_variant_id"],
                        row.get("bundle_item_product_title") or "",
                        row.get("bundle_item_variant_title") or "",
                        row.get("bundle_item_variant_sku") or "",
                        int(row["bundle_item_quantity"]),
                        _number(row.get("bundle_item_variant_price")),
                    ),
                )
    return {
        "rows": len(rows),
        "bundles": len(grouped),
        "valid": valid_count,
        "invalid": len(grouped) - valid_count,
        "source_hash": source_hash,
    }


def import_bundle_csv_path(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    return import_bundle_csv(path.read_bytes(), path.name)


def bundle_stats() -> dict[str, int]:
    init_bundle_store()
    with _connect(REGISTRY_PATH) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) total,
                   COALESCE(SUM(valid),0) valid,
                   COALESCE(SUM(CASE WHEN valid=0 THEN 1 ELSE 0 END),0) invalid,
                   COALESCE(SUM(CASE WHEN selected=1 AND valid=1 THEN 1 ELSE 0 END),0)
                       selected
            FROM bundle_variants
            """
        ).fetchone()
        components = conn.execute(
            "SELECT COUNT(*) FROM bundle_components"
        ).fetchone()[0]
    return {**dict(row), "components": int(components)}


def certilas_surcharge_bundle_stats() -> dict[str, int]:
    path = supplier_database_path("certilas")
    with _connect(path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_price_components'"
        ).fetchone()
        if not exists:
            return {"total": 0, "ready": 0, "attention": 0}
        row = conn.execute(
            """SELECT COUNT(*) total,
                      COALESCE(SUM(status='ready'),0) ready,
                      COALESCE(SUM(status<>'ready'),0) attention
               FROM product_price_components WHERE component_type='alloy_surcharge'"""
        ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def list_certilas_surcharge_bundles(
    *, query: str = "", status: str = "all", limit: int = 500,
) -> list[dict[str, Any]]:
    path = supplier_database_path("certilas")
    where = ["pc.component_type='alloy_surcharge'"]
    params: list[Any] = []
    if query.strip():
        like = f"%{query.strip()}%"
        where.append("(pc.main_sku LIKE ? OR pc.component_sku LIKE ? OR p.source_description LIKE ? OR z.source_description LIKE ?)")
        params.extend([like, like, like, like])
    if status == "ready":
        where.append("pc.status='ready'")
    elif status == "attention":
        where.append("pc.status<>'ready'")
    with _connect(path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_price_components'"
        ).fetchone()
        if not exists:
            return []
        return [dict(row) for row in conn.execute(
            f"""SELECT pc.*,p.source_description main_description,
                       z.source_description component_description
                FROM product_price_components pc
                JOIN products p ON p.sku=pc.main_sku
                LEFT JOIN products z ON z.sku=pc.component_sku
                WHERE {' AND '.join(where)}
                ORDER BY pc.status='ready' DESC,p.source_description,pc.main_sku
                LIMIT ?""",
            (*params, max(1, min(int(limit), 1000))),
        ).fetchall()]


def set_all_bundle_selection(selected: bool) -> int:
    init_bundle_store()
    with _connect(REGISTRY_PATH) as conn:
        cursor = conn.execute(
            "UPDATE bundle_variants SET selected=? WHERE valid=1",
            (int(selected),),
        )
    return int(cursor.rowcount)


def set_bundle_selection(bundle_variant_id: str, selected: bool) -> None:
    init_bundle_store()
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            """
            UPDATE bundle_variants
            SET selected=CASE WHEN valid=1 THEN ? ELSE 0 END,updated_at=?
            WHERE bundle_variant_id=?
            """,
            (int(selected), utc_now(), bundle_variant_id),
        )


def list_bundle_previews(
    *,
    query: str = "",
    selected_only: bool = False,
    limit: int = 250,
    offset: int = 0,
) -> list[dict[str, Any]]:
    init_bundle_store()
    where = []
    params: list[Any] = []
    if query.strip():
        like = f"%{query.strip()}%"
        where.append(
            "(product_title LIKE ? OR variant_title LIKE ? OR variant_sku LIKE ?)"
        )
        params.extend([like, like, like])
    if selected_only:
        where.append("selected=1")
    sql_where = " WHERE " + " AND ".join(where) if where else ""
    with _connect(REGISTRY_PATH) as conn:
        variants = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT * FROM bundle_variants
                {sql_where}
                ORDER BY valid DESC,product_title,variant_title,bundle_variant_id
                LIMIT ? OFFSET ?
                """,
                (*params, max(1, min(int(limit), 500)), max(0, int(offset))),
            ).fetchall()
        ]
        for variant in variants:
            variant["components"] = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM bundle_components
                    WHERE bundle_variant_id=?
                    ORDER BY product_title,variant_title,component_variant_id
                    """,
                    (variant["bundle_variant_id"],),
                ).fetchall()
            ]
    return variants


def bundle_editor_options() -> list[dict[str, str]]:
    init_bundle_store()
    with _connect(REGISTRY_PATH) as conn:
        return [dict(row) for row in conn.execute(
            """SELECT bundle_variant_id,product_title,variant_sku,variant_title
               FROM bundle_variants
               ORDER BY product_title,variant_title,bundle_variant_id"""
        ).fetchall()]


def get_bundle_for_edit(bundle_variant_id: str) -> dict[str, Any] | None:
    if not bundle_variant_id:
        return None
    init_bundle_store()
    with _connect(REGISTRY_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM bundle_variants WHERE bundle_variant_id=?",
            (bundle_variant_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["components"] = [dict(item) for item in conn.execute(
            """SELECT quantity,variant_sku,product_title,variant_title,variant_price
               FROM bundle_components WHERE bundle_variant_id=? ORDER BY id""",
            (bundle_variant_id,),
        ).fetchall()]
        return result


def delete_bundle(bundle_variant_id: str) -> bool:
    """Delete one bundle definition without deleting its underlying products."""
    if not bundle_variant_id:
        raise ValueError("Kies eerst een bundel om te verwijderen.")
    init_bundle_store()
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            "DELETE FROM bundle_components WHERE bundle_variant_id=?",
            (bundle_variant_id,),
        )
        cursor = conn.execute(
            "DELETE FROM bundle_variants WHERE bundle_variant_id=?",
            (bundle_variant_id,),
        )
    return cursor.rowcount > 0


def save_bundle_draft(
    *, bundle_variant_id: str = "", product_title: str, variant_sku: str,
    variant_title: str = "", variant_price: float | None = None,
    product_status: str = "DRAFT", components: list[dict[str, Any]],
) -> str:
    init_bundle_store()
    title = product_title.strip()
    sku = variant_sku.strip()
    if not title or not sku:
        raise ValueError("Vul de bundeltitel en hoofd-SKU in.")
    cleaned = []
    for item in components:
        component_sku = str(item.get("SKU") or item.get("variant_sku") or "").strip()
        if not component_sku:
            continue
        try:
            quantity = int(item.get("Aantal") or item.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if quantity <= 0:
            raise ValueError(f"Aantal voor component {component_sku} moet minimaal 1 zijn.")
        cleaned.append({
            "sku": component_sku, "quantity": quantity,
            "title": str(item.get("Product") or item.get("product_title") or component_sku).strip(),
            "variant": str(item.get("Variant") or item.get("variant_title") or "").strip(),
            "price": _number(item.get("Prijs") if "Prijs" in item else item.get("variant_price")),
        })
    if not cleaned:
        raise ValueError("Voeg minimaal één component met SKU en aantal toe.")
    if len(cleaned) > 30:
        raise ValueError("Een bundel kan maximaal 30 componentregels bevatten.")
    if len({item["sku"].casefold() for item in cleaned}) != len(cleaned):
        raise ValueError("Dezelfde component-SKU staat meer dan één keer in de bundel.")
    now = utc_now()
    identifier = bundle_variant_id or f"manual-{uuid.uuid4().hex}"
    with _connect(REGISTRY_PATH) as conn:
        existing = conn.execute(
            "SELECT * FROM bundle_variants WHERE bundle_variant_id=?", (identifier,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE bundle_variants SET product_title=?,product_status=?,
                   variant_title=?,variant_sku=?,variant_price=?,valid=1,
                   validation_message='',manual_override=1,updated_at=?
                   WHERE bundle_variant_id=?""",
                (title,product_status,variant_title.strip(),sku,variant_price,now,identifier),
            )
        else:
            cursor = conn.execute(
                """INSERT INTO bundle_imports(source_name,source_hash,imported_at,row_count,bundle_count)
                   VALUES('Handmatig in Bundelbouwer',?,?,?,1)""",
                (identifier,now,len(cleaned)),
            )
            conn.execute(
                """INSERT INTO bundle_variants(
                   bundle_variant_id,bundle_product_id,product_title,product_status,
                   variant_title,variant_sku,variant_price,sync_price,selected,valid,
                   validation_message,import_id,manual_override,updated_at)
                   VALUES(?,?,?,?,?,?,?,0,0,1,'',?,1,?)""",
                (identifier,f"manual-product-{uuid.uuid4().hex}",title,product_status,
                 variant_title.strip(),sku,variant_price,int(cursor.lastrowid),now),
            )
        conn.execute("DELETE FROM bundle_components WHERE bundle_variant_id=?", (identifier,))
        for index, item in enumerate(cleaned, start=1):
            conn.execute(
                """INSERT INTO bundle_components(
                   bundle_variant_id,component_product_id,component_variant_id,
                   product_title,variant_title,variant_sku,quantity,variant_price)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (identifier,f"manual-product-{item['sku']}",
                 f"manual-component-{index}-{item['sku']}",item["title"],
                 item["variant"],item["sku"],item["quantity"],item["price"]),
            )
    return identifier


def refresh_bundle_images() -> dict[str, int]:
    init_bundle_store()
    with _connect(REGISTRY_PATH) as conn:
        product_ids = {
            row[0]
            for row in conn.execute(
                """
                SELECT bundle_product_id FROM bundle_variants
                UNION
                SELECT component_product_id FROM bundle_components
                """
            ).fetchall()
            if row[0]
        }
    if not product_ids:
        return {"requested": 0, "found": 0}

    client = ShopifyClient.from_settings()
    numeric_ids = sorted(product_ids)
    images: dict[str, str] = {}
    query = """
    query PimBundleImages($ids:[ID!]!){
      nodes(ids:$ids){
        ... on Product{
          id
          featuredMedia{preview{image{url}}}
          media(first:1){nodes{preview{image{url}}}}
        }
      }
    }
    """
    for start in range(0, len(numeric_ids), 100):
        gids = [
            f"gid://shopify/Product/{value}"
            for value in numeric_ids[start : start + 100]
        ]
        data = client.graphql(query, {"ids": gids})
        for product in data.get("nodes") or []:
            if not product:
                continue
            preview = ((product.get("featuredMedia") or {}).get("preview") or {})
            url = (preview.get("image") or {}).get("url")
            if not url:
                media = ((product.get("media") or {}).get("nodes") or [])
                if media:
                    url = (
                        (((media[0] or {}).get("preview") or {}).get("image") or {})
                        .get("url")
                    )
            images[product["id"].rsplit("/", 1)[-1]] = url or ""

    with _connect(REGISTRY_PATH) as conn:
        for product_id, url in images.items():
            conn.execute(
                "UPDATE bundle_variants SET image_url=? WHERE bundle_product_id=?",
                (url, product_id),
            )
            conn.execute(
                "UPDATE bundle_components SET image_url=? WHERE component_product_id=?",
                (url, product_id),
            )
    return {"requested": len(product_ids), "found": sum(bool(url) for url in images.values())}
