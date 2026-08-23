from __future__ import annotations

import json
import math
import re
import uuid
from typing import Any, Callable

from app.shopify.client import ShopifyClient, get_shopify_settings
from app.suppliers.hub import (
    REGISTRY_PATH, SUPPLIER_DIR, _connect, init_registry, utc_now,
)


RESTOCK_TYPES = {"return", "cancel", "legacy_restock"}


def _active_supplier_database_paths() -> list:
    """Return only registered live supplier databases, never local backups."""
    init_registry()
    paths = []
    try:
        with _connect(REGISTRY_PATH) as connection:
            slugs = [
                str(row["slug"])
                for row in connection.execute(
                    "SELECT slug FROM suppliers WHERE enabled=1 ORDER BY slug"
                ).fetchall()
            ]
        paths = [SUPPLIER_DIR / f"{slug}.sqlite" for slug in slugs]
    except Exception:
        paths = []
    existing = [path for path in paths if path.is_file()]
    if existing:
        return existing
    return [
        path for path in sorted(SUPPLIER_DIR.glob("*.sqlite"))
        if "-before-" not in path.stem and "backup" not in path.stem.casefold()
    ]


def product_name_for_sku(sku: str) -> str:
    """Find the best locally stored PIM product name for a supplier SKU."""
    value = str(sku or "").strip()
    if not value:
        return ""
    for path in _active_supplier_database_paths():
        try:
            with _connect(path) as conn:
                columns = {
                    row["name"] for row in conn.execute(
                        "PRAGMA table_info(products)"
                    ).fetchall()
                }
                if "sku" not in columns:
                    continue
                name_columns = [
                    name for name in ("ai_title", "source_title", "source_description")
                    if name in columns
                ]
                if not name_columns:
                    continue
                row = conn.execute(
                    "SELECT " + ",".join(name_columns)
                    + " FROM products WHERE sku=? COLLATE NOCASE LIMIT 1",
                    (value,),
                ).fetchone()
                if row:
                    for name in name_columns:
                        product_name = str(row[name] or "").strip()
                        if product_name:
                            return product_name
        except Exception:
            continue
    return ""


def suggested_mapping_values(base_sku: str) -> dict[str, Any]:
    """Find known packaging content for a SKU in the local supplier databases."""
    sku = str(base_sku or "").strip()
    if not sku:
        return {"base_package_quantity": 1, "source": ""}
    for path in _active_supplier_database_paths():
        try:
            with _connect(path) as conn:
                row = conn.execute(
                    """
                    SELECT sku,weight_grams,kg_per_sales_unit
                    FROM products WHERE sku=? COLLATE NOCASE LIMIT 1
                    """,
                    (sku,),
                ).fetchone()
        except Exception:
            continue
        if not row:
            continue
        package_quantity = (
            float(row["kg_per_sales_unit"] or 0) * 1000
            or float(row["weight_grams"] or 0)
            or 1
        )
        return {
            "base_package_quantity": max(1, int(round(package_quantity))),
            "source": path.stem,
        }
    return {"base_package_quantity": 1, "source": ""}


def find_product_family(base_sku: str) -> dict[str, Any] | None:
    """Return the stored PIM family containing the supplied base SKU."""
    sku = str(base_sku or "").strip()
    if not sku:
        return None
    for path in _active_supplier_database_paths():
        try:
            with _connect(path) as conn:
                tables = {
                    row["name"] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if not {"product_families", "product_family_variants"} <= tables:
                    continue
                family = conn.execute(
                    """
                    SELECT f.family_key,f.title
                    FROM product_family_variants AS v
                    JOIN product_families AS f ON f.family_key=v.family_key
                    WHERE v.sku=? COLLATE NOCASE LIMIT 1
                    """,
                    (sku,),
                ).fetchone()
                if not family:
                    continue
                variants = conn.execute(
                    """
                    SELECT sku,variant_title FROM product_family_variants
                    WHERE family_key=? ORDER BY position,sku COLLATE NOCASE
                    """,
                    (family["family_key"],),
                ).fetchall()
                return {
                    "family_key": family["family_key"],
                    "title": family["title"],
                    "skus": [row["sku"] for row in variants],
                    "variants": [
                        {
                            "sku": row["sku"],
                            "value": row["variant_title"],
                        }
                        for row in variants
                    ],
                    "source": path.stem,
                }
        except Exception:
            continue
    return None


def list_family_product_options() -> list[dict[str, str]]:
    """Return every persisted PIM-family variant for searchable UI selection."""
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in _active_supplier_database_paths():
        try:
            with _connect(path) as conn:
                tables = {
                    row["name"] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if not {"product_families", "product_family_variants"} <= tables:
                    continue
                rows = conn.execute(
                    """
                    SELECT f.title,v.sku,v.variant_title
                    FROM product_family_variants AS v
                    JOIN product_families AS f ON f.family_key=v.family_key
                    ORDER BY f.title COLLATE NOCASE,v.position,v.sku COLLATE NOCASE
                    """
                ).fetchall()
        except Exception:
            continue
        for row in rows:
            sku = str(row["sku"] or "").strip()
            normalized = sku.casefold()
            if not sku or normalized in seen:
                continue
            seen.add(normalized)
            result.append({
                "sku": sku,
                "family_title": str(row["title"] or "").strip(),
                "variant_title": str(row["variant_title"] or "").strip(),
                "source": path.stem,
            })
    return result


def build_derived_sku(base_sku: str, quantity: float, unit_label: str) -> str:
    sku = str(base_sku or "").strip().upper()
    if not sku:
        return ""
    numeric_amount = float(quantity or 0)
    amount = f"{numeric_amount:.3f}".rstrip("0").rstrip(".")
    unit = str(unit_label or "").casefold()
    if unit == "kilogram":
        suffix = f"{amount}KG"
    elif unit == "gram":
        suffix = f"{amount}G"
    elif unit == "lengte":
        suffix = f"{amount}MM"
    else:
        suffix = f"{amount}ST"
    return f"{sku}-{suffix}"


def init_derived_inventory() -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS derived_inventory_mappings (
                derived_sku TEXT PRIMARY KEY COLLATE NOCASE,
                base_sku TEXT NOT NULL COLLATE NOCASE,
                base_quantity REAL NOT NULL CHECK(base_quantity > 0),
                base_package_quantity REAL NOT NULL DEFAULT 1,
                unit_label TEXT NOT NULL DEFAULT 'stuk',
                customer_notice TEXT NOT NULL DEFAULT '',
                price_adjustment_type TEXT NOT NULL DEFAULT 'none',
                price_adjustment_value REAL NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS derived_inventory_order_lines (
                order_id TEXT NOT NULL,
                line_item_id TEXT NOT NULL,
                derived_sku TEXT NOT NULL COLLATE NOCASE,
                base_sku TEXT NOT NULL COLLATE NOCASE,
                base_quantity_per_item REAL NOT NULL,
                ordered_quantity INTEGER NOT NULL,
                restored_quantity INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(order_id, line_item_id)
            );
            CREATE TABLE IF NOT EXISTS derived_inventory_events (
                webhook_id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                order_id TEXT,
                status TEXT NOT NULL,
                adjustments_json TEXT NOT NULL DEFAULT '[]',
                message TEXT,
                received_at TEXT NOT NULL,
                processed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS derived_inventory_adjustments (
                webhook_id TEXT NOT NULL,
                base_sku TEXT NOT NULL COLLATE NOCASE,
                delta REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                message TEXT,
                processed_at TEXT,
                PRIMARY KEY(webhook_id, base_sku),
                FOREIGN KEY(webhook_id) REFERENCES derived_inventory_events(webhook_id)
            );
            CREATE INDEX IF NOT EXISTS idx_derived_order_lines_order
            ON derived_inventory_order_lines(order_id);
            CREATE TABLE IF NOT EXISTS derived_inventory_pools (
                base_sku TEXT PRIMARY KEY COLLATE NOCASE,
                available_units REAL NOT NULL,
                initialized_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS derived_inventory_piece_packages (
                base_sku TEXT PRIMARY KEY COLLATE NOCASE,
                pieces_per_package INTEGER NOT NULL CHECK(pieces_per_package > 0),
                confirmed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        columns = {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(derived_inventory_mappings)"
            ).fetchall()
        }
        if "base_package_quantity" not in columns:
            conn.execute(
                "ALTER TABLE derived_inventory_mappings "
                "ADD COLUMN base_package_quantity REAL NOT NULL DEFAULT 1"
            )
        if "price_adjustment_type" not in columns:
            conn.execute(
                "ALTER TABLE derived_inventory_mappings "
                "ADD COLUMN price_adjustment_type TEXT NOT NULL DEFAULT 'none'"
            )
        if "price_adjustment_value" not in columns:
            conn.execute(
                "ALTER TABLE derived_inventory_mappings "
                "ADD COLUMN price_adjustment_value REAL NOT NULL DEFAULT 0"
            )


def list_piece_package_counts() -> dict[str, int]:
    init_derived_inventory()
    with _connect(REGISTRY_PATH) as conn:
        return {
            row["base_sku"]: int(row["pieces_per_package"])
            for row in conn.execute(
                "SELECT base_sku,pieces_per_package FROM derived_inventory_piece_packages"
            ).fetchall()
        }


def save_piece_package_count(base_sku: str, pieces_per_package: int) -> None:
    sku = str(base_sku or "").strip()
    try:
        count = int(pieces_per_package)
    except (TypeError, ValueError) as exc:
        raise ValueError("Het aantal stuks per verpakking moet een geheel getal zijn.") from exc
    if not sku or count <= 0:
        raise ValueError("Basis-SKU en een positief aantal stuks zijn verplicht.")
    init_derived_inventory()
    now = utc_now()
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            """
            INSERT INTO derived_inventory_piece_packages(
                base_sku,pieces_per_package,confirmed_at,updated_at
            ) VALUES(?,?,?,?)
            ON CONFLICT(base_sku) DO UPDATE SET
                pieces_per_package=excluded.pieces_per_package,
                updated_at=excluded.updated_at
            """,
            (sku, count, now, now),
        )


def save_mapping(
    derived_sku: str,
    base_sku: str,
    base_quantity: float,
    *,
    base_package_quantity: float = 1,
    unit_label: str = "gram",
    customer_notice: str = "",
    price_adjustment_type: str = "none",
    price_adjustment_value: float = 0,
    enabled: bool = True,
    replace_existing: bool = True,
) -> None:
    derived_sku = str(derived_sku or "").strip()
    base_sku = str(base_sku or "").strip()
    try:
        base_quantity = float(base_quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError("De hoeveelheid basisvoorraad moet een getal zijn.") from exc
    if not derived_sku or not base_sku:
        raise ValueError("Zowel de verkoop-SKU als de basis-SKU is verplicht.")
    if derived_sku.casefold() == base_sku.casefold():
        raise ValueError("De verkoop-SKU moet afwijken van de basis-SKU.")
    if not math.isfinite(base_quantity) or base_quantity <= 0:
        raise ValueError("De hoeveelheid basisvoorraad moet groter zijn dan nul.")
    unit_label = str(unit_label or "gram").strip().casefold()
    if unit_label not in {"gram", "kilogram", "stuk", "lengte"}:
        raise ValueError("Ongeldige verkoopeenheid.")
    if unit_label != "kilogram" and not base_quantity.is_integer():
        raise ValueError(
            "Gebruik voor gram, millimeters en aantallen een geheel getal. "
            "Kilogram mag een decimaal zijn, bijvoorbeeld 2,6."
        )
    if unit_label != "kilogram":
        base_quantity = int(base_quantity)
    try:
        base_package_quantity = float(base_package_quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError("De inhoud van de basisverpakking moet een getal zijn.") from exc
    if not math.isfinite(base_package_quantity) or base_package_quantity <= 0:
        raise ValueError("De inhoud van de basisverpakking moet groter zijn dan nul.")
    if unit_label != "kilogram" and not base_package_quantity.is_integer():
        raise ValueError(
            "De inhoud moet bij gram, millimeters en stuks een geheel getal zijn. "
            "Kilogram mag een decimaal zijn."
        )
    if unit_label != "kilogram":
        base_package_quantity = int(base_package_quantity)
    if unit_label == "stuk":
        confirmed_counts = list_piece_package_counts()
        confirmed = next(
            (count for sku, count in confirmed_counts.items()
             if sku.casefold() == base_sku.casefold()),
            None,
        )
        if confirmed is None:
            raise ValueError(
                f"Vul eerst het werkelijke aantal stuks per verpakking in voor {base_sku}."
            )
        if base_package_quantity != confirmed:
            raise ValueError(
                f"De bevestigde verpakking van {base_sku} bevat {confirmed} stuks."
            )
    price_adjustment_type = str(price_adjustment_type or "none").strip().casefold()
    if price_adjustment_type not in {"none", "fixed", "percent"}:
        raise ValueError("Ongeldige verkoopprijsaanpassing.")
    try:
        price_adjustment_value = float(price_adjustment_value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("De verkoopprijsaanpassing moet een getal zijn.") from exc
    if not math.isfinite(price_adjustment_value) or price_adjustment_value < 0:
        raise ValueError("De verkoopprijsaanpassing mag niet negatief zijn.")
    init_derived_inventory()
    now = utc_now()
    with _connect(REGISTRY_PATH) as conn:
        existing = conn.execute(
            "SELECT 1 FROM derived_inventory_mappings WHERE derived_sku=?",
            (derived_sku,),
        ).fetchone()
        if existing and not replace_existing:
            raise ValueError(
                f"Verkoop-SKU {derived_sku} bestaat al. Selecteer die regel "
                "expliciet om hem te wijzigen."
            )
        conn.execute(
            """
            INSERT INTO derived_inventory_mappings(
                derived_sku,base_sku,base_quantity,base_package_quantity,
                unit_label,customer_notice,price_adjustment_type,
                price_adjustment_value,
                enabled,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(derived_sku) DO UPDATE SET
                base_sku=excluded.base_sku,
                base_quantity=excluded.base_quantity,
                base_package_quantity=excluded.base_package_quantity,
                unit_label=excluded.unit_label,
                customer_notice=excluded.customer_notice,
                price_adjustment_type=excluded.price_adjustment_type,
                price_adjustment_value=excluded.price_adjustment_value,
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
            """,
            (
                derived_sku, base_sku, base_quantity, base_package_quantity,
                unit_label, str(customer_notice or "").strip(),
                price_adjustment_type, price_adjustment_value,
                int(enabled), now, now,
            ),
        )


def list_mappings() -> list[dict[str, Any]]:
    init_derived_inventory()
    with _connect(REGISTRY_PATH) as conn:
        return [
            dict(row) for row in conn.execute(
                "SELECT * FROM derived_inventory_mappings ORDER BY base_sku,derived_sku"
            ).fetchall()
        ]


def delete_mapping(derived_sku: str) -> None:
    init_derived_inventory()
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            "DELETE FROM derived_inventory_mappings WHERE derived_sku=?",
            (str(derived_sku).strip(),),
        )


def _mapping(conn: Any, sku: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM derived_inventory_mappings WHERE derived_sku=? AND enabled=1",
        (str(sku or "").strip(),),
    ).fetchone()
    return dict(row) if row else None


def _order_id(payload: dict[str, Any]) -> str:
    return str(payload.get("admin_graphql_api_id") or payload.get("order_id") or payload.get("id") or "")


def _line_id(line: dict[str, Any]) -> str:
    return str(line.get("admin_graphql_api_id") or line.get("line_item_id") or line.get("id") or "")


def _inventory_delta(base_sku: str, amount: float) -> dict[str, Any]:
    return {"base_sku": base_sku, "delta": round(float(amount), 6)}


def _sale_adjustments(conn: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    order_id = _order_id(payload)
    adjustments: list[dict[str, Any]] = []
    for line in payload.get("line_items") or []:
        mapping = _mapping(conn, line.get("sku") or "")
        line_id = _line_id(line)
        quantity = max(0, int(line.get("quantity") or 0))
        if not mapping or not line_id or not quantity:
            continue
        exists = conn.execute(
            "SELECT 1 FROM derived_inventory_order_lines WHERE order_id=? AND line_item_id=?",
            (order_id, line_id),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO derived_inventory_order_lines(
                order_id,line_item_id,derived_sku,base_sku,
                base_quantity_per_item,ordered_quantity,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                order_id, line_id, mapping["derived_sku"], mapping["base_sku"],
                mapping["base_quantity"], quantity, utc_now(), utc_now(),
            ),
        )
        adjustments.append(_inventory_delta(mapping["base_sku"], -quantity * mapping["base_quantity"]))
    return adjustments


def _restore_line(
    conn: Any, order_id: str, line_item_id: str, requested_quantity: int
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM derived_inventory_order_lines
        WHERE order_id=? AND line_item_id=?
        """,
        (order_id, line_item_id),
    ).fetchone()
    if not row:
        return None
    remaining = max(0, int(row["ordered_quantity"]) - int(row["restored_quantity"]))
    restored = min(remaining, max(0, int(requested_quantity)))
    if not restored:
        return None
    conn.execute(
        """
        UPDATE derived_inventory_order_lines
        SET restored_quantity=restored_quantity+?,updated_at=?
        WHERE order_id=? AND line_item_id=?
        """,
        (restored, utc_now(), order_id, line_item_id),
    )
    return _inventory_delta(row["base_sku"], restored * row["base_quantity_per_item"])


def _cancel_adjustments(conn: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    order_id = _order_id(payload)
    rows = conn.execute(
        "SELECT * FROM derived_inventory_order_lines WHERE order_id=?",
        (order_id,),
    ).fetchall()
    result = []
    for row in rows:
        adjustment = _restore_line(
            conn, order_id, row["line_item_id"],
            int(row["ordered_quantity"]) - int(row["restored_quantity"]),
        )
        if adjustment:
            result.append(adjustment)
    return result


def _refund_adjustments(conn: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    order_id = _order_id(payload)
    result = []
    for refund_line in payload.get("refund_line_items") or []:
        restock_type = str(refund_line.get("restock_type") or "").casefold()
        if restock_type not in RESTOCK_TYPES:
            continue
        line = refund_line.get("line_item") or {}
        line_id = _line_id(line) or str(refund_line.get("line_item_id") or "")
        adjustment = _restore_line(
            conn, order_id, line_id, int(refund_line.get("quantity") or 0)
        )
        if adjustment:
            result.append(adjustment)
    return result


def _combine(adjustments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, float] = {}
    for item in adjustments:
        totals[item["base_sku"]] = totals.get(item["base_sku"], 0.0) + item["delta"]
    return [_inventory_delta(sku, delta) for sku, delta in totals.items() if delta]


def _location_quantity(client: ShopifyClient, inventory_item_id: str, location_id: str) -> int:
    data = client.graphql(
        """
        query PimLocationQuantity($id:ID!,$location:ID!){
          inventoryItem(id:$id){inventoryLevel(locationId:$location){
            quantities(names:["available"]){name quantity}
          }}
        }
        """,
        {"id": inventory_item_id, "location": location_id},
    )
    quantities = (
        (((data.get("inventoryItem") or {}).get("inventoryLevel") or {}).get("quantities"))
        or []
    )
    return int(next((q["quantity"] for q in quantities if q.get("name") == "available"), 0))


def _set_location_quantities(
    client: ShopifyClient, location_id: str, quantities: list[dict[str, Any]]
) -> None:
    if not quantities:
        return
    result = client.graphql(
        """
        mutation SetDerivedInventory(
          $input:InventorySetQuantitiesInput!,$idempotencyKey:String!
        ){
          inventorySetQuantities(input:$input) @idempotent(key:$idempotencyKey){
            userErrors{field message code}
          }
        }
        """,
        {"idempotencyKey": str(uuid.uuid4()), "input": {
            "name": "available", "reason": "correction",
            "referenceDocumentUri": f"pim://weldingshop/deelverkoop/{uuid.uuid4()}",
            "quantities": quantities,
        }},
    )["inventorySetQuantities"]
    if result.get("userErrors"):
        raise RuntimeError(json.dumps(result["userErrors"], ensure_ascii=False))


def backfill_piece_sales(
    start_date: str, *, apply: bool = False, client: ShopifyClient | None = None,
) -> dict[str, Any]:
    """Preview or idempotently import net historical piece-sale order lines."""
    start = str(start_date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
        raise ValueError("De begindatum moet de vorm JJJJ-MM-DD hebben.")
    init_derived_inventory()
    with _connect(REGISTRY_PATH) as conn:
        mappings = {
            row["derived_sku"].casefold(): dict(row)
            for row in conn.execute(
                "SELECT * FROM derived_inventory_mappings "
                "WHERE enabled=1 AND lower(unit_label)='stuk'"
            ).fetchall()
        }
        existing = {
            (row["order_id"], row["line_item_id"])
            for row in conn.execute(
                "SELECT order_id,line_item_id FROM derived_inventory_order_lines"
            ).fetchall()
        }
    if not mappings:
        raise ValueError("Er zijn nog geen actieve deelverkoopregels in stuks.")
    client = client or ShopifyClient.from_settings()
    cursor = None
    lines: list[dict[str, Any]] = []
    while True:
        data = client.graphql(
            """
            query HistoricalPieceSales($cursor:String,$query:String!){
              orders(first:100,after:$cursor,sortKey:CREATED_AT,query:$query){
                pageInfo{hasNextPage endCursor}
                nodes{
                  id name createdAt cancelledAt
                  lineItems(first:250){nodes{id sku quantity currentQuantity}}
                }
              }
            }
            """,
            {"cursor": cursor, "query": f"created_at:>={start}"},
        )
        orders = data.get("orders") or {}
        for order in orders.get("nodes") or []:
            if order.get("cancelledAt"):
                continue
            for line in (order.get("lineItems") or {}).get("nodes") or []:
                mapping = mappings.get(str(line.get("sku") or "").strip().casefold())
                current_quantity = max(0, int(line.get("currentQuantity") or 0))
                identity = (str(order.get("id") or ""), str(line.get("id") or ""))
                if not mapping or not current_quantity or identity in existing:
                    continue
                lines.append({
                    "order_id": identity[0], "line_item_id": identity[1],
                    "order_name": order.get("name") or identity[0],
                    "created_at": order.get("createdAt") or "",
                    "derived_sku": mapping["derived_sku"],
                    "base_sku": mapping["base_sku"],
                    "sale_quantity": current_quantity,
                    "pieces_per_sale": int(mapping["base_quantity"]),
                    "pieces": current_quantity * int(mapping["base_quantity"]),
                })
        page = orders.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    totals: dict[str, int] = {}
    for line in lines:
        totals[line["base_sku"]] = totals.get(line["base_sku"], 0) + line["pieces"]
    if apply and lines:
        for base_sku in totals:
            initialize_pool_from_shopify(base_sku)
        now = utc_now()
        with _connect(REGISTRY_PATH) as conn:
            for line in lines:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO derived_inventory_order_lines(
                        order_id,line_item_id,derived_sku,base_sku,
                        base_quantity_per_item,ordered_quantity,restored_quantity,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,0,?,?)
                    """,
                    (
                        line["order_id"], line["line_item_id"], line["derived_sku"],
                        line["base_sku"], line["pieces_per_sale"],
                        line["sale_quantity"], line["created_at"] or now, now,
                    ),
                )
            for base_sku, pieces in totals.items():
                conn.execute(
                    """
                    UPDATE derived_inventory_pools
                    SET available_units=MAX(0,available_units-?),updated_at=?
                    WHERE base_sku=?
                    """,
                    (pieces, now, base_sku),
                )
        for base_sku in totals:
            synchronize_pool_to_shopify(base_sku)
    return {
        "start_date": start, "lines": lines, "orders": len({x["order_id"] for x in lines}),
        "pieces": sum(totals.values()), "totals": totals, "applied": bool(apply),
    }


def initialize_pool_from_shopify(base_sku: str) -> dict[str, Any]:
    """Initialize once from the visible number of complete base packages."""
    init_derived_inventory()
    with _connect(REGISTRY_PATH) as conn:
        existing = conn.execute(
            "SELECT * FROM derived_inventory_pools WHERE base_sku=?", (base_sku,)
        ).fetchone()
        mapping = conn.execute(
            """
            SELECT base_package_quantity FROM derived_inventory_mappings
            WHERE base_sku=? AND enabled=1 LIMIT 1
            """,
            (base_sku,),
        ).fetchone()
    if existing:
        return dict(existing)
    if not mapping:
        raise ValueError(f"Geen actieve deelverkoopregel voor basis-SKU {base_sku}.")
    settings = get_shopify_settings()
    location_id = settings.get("location_id")
    if not location_id:
        raise RuntimeError("Algemene Shopify-magazijnlocatie ontbreekt.")
    client = ShopifyClient.from_settings()
    variant = client.find_variant_by_sku(base_sku)
    if not variant or not (variant.get("inventoryItem") or {}).get("id"):
        raise RuntimeError(f"Basis-SKU niet gevonden in Shopify: {base_sku}")
    packages = _location_quantity(client, variant["inventoryItem"]["id"], location_id)
    now = utc_now()
    available = packages * float(mapping["base_package_quantity"])
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO derived_inventory_pools(
                base_sku,available_units,initialized_at,updated_at
            ) VALUES(?,?,?,?)
            """,
            (base_sku, available, now, now),
        )
        row = conn.execute(
            "SELECT * FROM derived_inventory_pools WHERE base_sku=?", (base_sku,)
        ).fetchone()
    return dict(row)


def synchronize_pool_to_shopify(base_sku: str) -> dict[str, int]:
    init_derived_inventory()
    with _connect(REGISTRY_PATH) as conn:
        pool = conn.execute(
            "SELECT * FROM derived_inventory_pools WHERE base_sku=?", (base_sku,)
        ).fetchone()
        mappings = conn.execute(
            """
            SELECT * FROM derived_inventory_mappings
            WHERE base_sku=? AND enabled=1 ORDER BY derived_sku
            """,
            (base_sku,),
        ).fetchall()
    if not pool or not mappings:
        return {}
    settings = get_shopify_settings()
    location_id = settings.get("location_id")
    if not location_id:
        raise RuntimeError("Algemene Shopify-magazijnlocatie ontbreekt.")
    available = max(0, float(pool["available_units"]))
    desired = {base_sku: math.floor(available / float(mappings[0]["base_package_quantity"]))}
    for mapping in mappings:
        desired[mapping["derived_sku"]] = math.floor(
            available / float(mapping["base_quantity"])
        )
    client = ShopifyClient.from_settings()
    quantities = []
    for sku, quantity in desired.items():
        variant = client.find_variant_by_sku(sku)
        item_id = (variant or {}).get("inventoryItem", {}).get("id")
        if item_id:
            quantities.append({
                "inventoryItemId": item_id,
                "locationId": location_id,
                "quantity": int(quantity),
                "changeFromQuantity": None,
            })
    _set_location_quantities(client, location_id, quantities)
    return desired


def adjust_shopify_inventory(base_sku: str, delta: float) -> None:
    initialize_pool_from_shopify(base_sku)
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            """
            UPDATE derived_inventory_pools
            SET available_units=MAX(0,available_units+?),updated_at=?
            WHERE base_sku=?
            """,
            (float(delta), utc_now(), base_sku),
        )
    synchronize_pool_to_shopify(base_sku)


def process_webhook(
    topic: str,
    webhook_id: str,
    payload: dict[str, Any],
    *,
    adjuster: Callable[[str, float], None] = adjust_shopify_inventory,
) -> dict[str, Any]:
    init_derived_inventory()
    topic = str(topic or "").casefold()
    webhook_id = str(webhook_id or "").strip()
    if not webhook_id:
        raise ValueError("Shopify-webhook-ID ontbreekt.")
    duplicate = False
    with _connect(REGISTRY_PATH) as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT * FROM derived_inventory_events WHERE webhook_id=?", (webhook_id,)
        ).fetchone()
        if previous:
            duplicate = True
        else:
            conn.execute(
                """
                INSERT INTO derived_inventory_events(
                    webhook_id,topic,order_id,status,received_at
                ) VALUES(?,?,?,'processing',?)
                """,
                (webhook_id, topic, _order_id(payload), utc_now()),
            )
            if topic == "orders/create":
                adjustments = _sale_adjustments(conn, payload)
            elif topic == "orders/cancelled":
                adjustments = _cancel_adjustments(conn, payload)
            elif topic == "refunds/create":
                adjustments = _refund_adjustments(conn, payload)
            else:
                adjustments = []
            adjustments = _combine(adjustments)
            for item in adjustments:
                conn.execute(
                    """
                    INSERT INTO derived_inventory_adjustments(
                        webhook_id,base_sku,delta,status
                    ) VALUES(?,?,?,'pending')
                    """,
                    (webhook_id, item["base_sku"], item["delta"]),
                )
            conn.execute(
                "UPDATE derived_inventory_events SET adjustments_json=? WHERE webhook_id=?",
                (json.dumps(adjustments, ensure_ascii=False), webhook_id),
            )

    with _connect(REGISTRY_PATH) as conn:
        pending = conn.execute(
            """
            SELECT * FROM derived_inventory_adjustments
            WHERE webhook_id=? AND status!='processed' ORDER BY base_sku
            """,
            (webhook_id,),
        ).fetchall()
    for item in pending:
        try:
            adjuster(item["base_sku"], item["delta"])
        except Exception as exc:
            with _connect(REGISTRY_PATH) as conn:
                conn.execute(
                    """
                    UPDATE derived_inventory_adjustments SET status='failed',message=?
                    WHERE webhook_id=? AND base_sku=?
                    """,
                    (str(exc), webhook_id, item["base_sku"]),
                )
                conn.execute(
                    "UPDATE derived_inventory_events SET status='failed',message=? WHERE webhook_id=?",
                    (str(exc), webhook_id),
                )
            raise
        with _connect(REGISTRY_PATH) as conn:
            conn.execute(
                """
                UPDATE derived_inventory_adjustments
                SET status='processed',message=NULL,processed_at=?
                WHERE webhook_id=? AND base_sku=?
                """,
                (utc_now(), webhook_id, item["base_sku"]),
            )

    with _connect(REGISTRY_PATH) as conn:
        failed = conn.execute(
            """
            SELECT COUNT(*) FROM derived_inventory_adjustments
            WHERE webhook_id=? AND status!='processed'
            """,
            (webhook_id,),
        ).fetchone()[0]
        adjustments_json = conn.execute(
            "SELECT adjustments_json FROM derived_inventory_events WHERE webhook_id=?",
            (webhook_id,),
        ).fetchone()[0]
        adjustments = json.loads(adjustments_json or "[]")
        if failed:
            return {"duplicate": duplicate, "status": "failed", "adjustments": adjustments}
        conn.execute(
            """
            UPDATE derived_inventory_events
            SET status='processed',adjustments_json=?,processed_at=?
            WHERE webhook_id=?
            """,
            (json.dumps(adjustments, ensure_ascii=False), utc_now(), webhook_id),
        )
    return {"duplicate": duplicate, "status": "processed", "adjustments": adjustments}
