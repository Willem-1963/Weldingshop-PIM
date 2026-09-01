from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import requests

from app.shopify.client import ShopifyClient
from app.suppliers.hub import _connect, init_supplier_database, utc_now


def _schema(slug: str) -> None:
    with _connect(init_supplier_database(slug)) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS historical_sales_lines(
            order_id TEXT NOT NULL, line_id TEXT NOT NULL,
            sale_date TEXT NOT NULL, shopify_updated_at TEXT NOT NULL,
            sku TEXT NOT NULL, title TEXT NOT NULL, quantity INTEGER NOT NULL,
            PRIMARY KEY(order_id,line_id)
        );
        CREATE INDEX IF NOT EXISTS historical_sales_date_sku
            ON historical_sales_lines(sale_date,sku);
        CREATE TABLE IF NOT EXISTS historical_sales_inventory(
            sku TEXT PRIMARY KEY, title TEXT NOT NULL,
            inventory_quantity INTEGER NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS historical_sales_meta(
            id INTEGER PRIMARY KEY CHECK(id=1), synced_at TEXT NOT NULL,
            oldest_order TEXT NOT NULL, newest_order TEXT NOT NULL,
            order_count INTEGER NOT NULL, line_count INTEGER NOT NULL,
            vendor_names TEXT NOT NULL
        );
        """)


def _matches_vendor(value: Any, vendor_names: Iterable[str]) -> bool:
    wanted = {str(name).strip().casefold() for name in vendor_names if str(name).strip()}
    return str(value or "").strip().casefold() in wanted


def _wait_for_bulk(client: ShopifyClient, operation_id: str) -> str:
    query = """query SupplierSalesBulkStatus {
      currentBulkOperation(type: QUERY) { id status errorCode url }
    }"""
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        operation = client.graphql(query).get("currentBulkOperation") or {}
        if operation.get("id") == operation_id:
            if operation.get("status") == "COMPLETED" and operation.get("url"):
                return str(operation["url"])
            if operation.get("status") in {"FAILED", "CANCELED", "EXPIRED"}:
                raise RuntimeError(
                    f"Shopify-bulkexport {operation['status']}: "
                    f"{operation.get('errorCode') or 'onbekende fout'}"
                )
        time.sleep(2)
    raise TimeoutError("Shopify-bulkexport duurde langer dan 15 minuten")


def _replace_orders(slug: str, orders: dict[str, dict[str, Any]]) -> int:
    rows = []
    with _connect(init_supplier_database(slug)) as conn:
        for order_id, order in orders.items():
            conn.execute("DELETE FROM historical_sales_lines WHERE order_id=?", (order_id,))
            if order["cancelled"]:
                continue
            for line in order["lines"]:
                if not line["sku"] or line["quantity"] <= 0:
                    continue
                rows.append((
                    order_id, line["id"], order["date"], order["updated_at"],
                    line["sku"], line["title"], line["quantity"],
                ))
        conn.executemany(
            """INSERT INTO historical_sales_lines
               (order_id,line_id,sale_date,shopify_updated_at,sku,title,quantity)
               VALUES(?,?,?,?,?,?,?)""", rows
        )
    return len(rows)


def _orders_with_supplier_lines(
    orders: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Keep only orders that contribute a supplier line to the history."""
    return {
        order_id: order for order_id, order in orders.items()
        if order.get("lines")
    }


def _history_bounds(orders: dict[str, dict[str, Any]]) -> tuple[str, str]:
    dates = sorted(
        str(order.get("date") or "") for order in orders.values()
        if not order.get("cancelled") and order.get("lines")
        and str(order.get("date") or "")
    )
    return (dates[0], dates[-1]) if dates else ("", "")


def _sync_inventory(
    slug: str, client: ShopifyClient, vendor_names: tuple[str, ...]
) -> int:
    query = """query SupplierInventory($after: String) {
      productVariants(first: 250, after: $after) {
        nodes { sku title inventoryQuantity product { title vendor } }
        pageInfo { hasNextPage endCursor }
      }
    }"""
    after = None
    rows: dict[str, tuple[str, int, str]] = {}
    while True:
        connection = client.graphql(query, {"after": after})["productVariants"]
        for node in connection.get("nodes") or []:
            product = node.get("product") or {}
            if not _matches_vendor(product.get("vendor"), vendor_names):
                continue
            sku = str(node.get("sku") or "").strip().upper()
            if sku:
                title = " · ".join(filter(None, (
                    str(product.get("title") or "").strip(),
                    str(node.get("title") or "").strip(),
                )))
                rows[sku] = (title, int(node.get("inventoryQuantity") or 0), utc_now())
        page = connection.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
    with _connect(init_supplier_database(slug)) as conn:
        conn.execute("DELETE FROM historical_sales_inventory")
        conn.executemany(
            """INSERT INTO historical_sales_inventory
               (sku,title,inventory_quantity,updated_at) VALUES(?,?,?,?)""",
            [(sku, *values) for sku, values in rows.items()],
        )
    return len(rows)


def full_sync(slug: str, vendor_names: tuple[str, ...]) -> dict[str, Any]:
    _schema(slug)
    client = ShopifyClient.from_settings()
    started_at = utc_now()
    bulk_query = """{
      orders(sortKey: CREATED_AT) { edges { node {
        id createdAt updatedAt cancelledAt
        lineItems { edges { node { id sku name vendor currentQuantity } } }
      } } }
    }"""
    mutation = """mutation SupplierSalesBulk($query: String!) {
      bulkOperationRunQuery(query: $query) {
        bulkOperation { id status } userErrors { field message }
      }
    }"""
    result = client.graphql(mutation, {"query": bulk_query})["bulkOperationRunQuery"]
    if result.get("userErrors"):
        raise RuntimeError(str(result["userErrors"]))
    url = _wait_for_bulk(client, str(result["bulkOperation"]["id"]))
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    orders: dict[str, dict[str, Any]] = {}
    for raw in response.iter_lines(decode_unicode=True):
        if not raw:
            continue
        item = json.loads(raw)
        parent = str(item.get("__parentId") or "")
        if not parent:
            day = str(item.get("createdAt") or "")[:10]
            orders[str(item["id"])] = {
                "date": day, "updated_at": str(item.get("updatedAt") or ""),
                "cancelled": bool(item.get("cancelledAt")), "lines": [],
            }
        elif parent in orders and _matches_vendor(item.get("vendor"), vendor_names):
            orders[parent]["lines"].append({
                "id": str(item["id"]),
                "sku": str(item.get("sku") or "").strip().upper(),
                "title": str(item.get("name") or "").strip(),
                "quantity": max(0, int(item.get("currentQuantity") or 0)),
            })
    orders = _orders_with_supplier_lines(orders)
    oldest, newest = _history_bounds(orders)
    path = init_supplier_database(slug)
    with _connect(path) as conn:
        conn.execute("DELETE FROM historical_sales_lines")
    line_count = _replace_orders(slug, orders)
    inventory_count = _sync_inventory(slug, client, vendor_names)
    with _connect(path) as conn:
        conn.execute(
            """INSERT INTO historical_sales_meta
               (id,synced_at,oldest_order,newest_order,order_count,line_count,vendor_names)
               VALUES(1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               synced_at=excluded.synced_at,oldest_order=excluded.oldest_order,
               newest_order=excluded.newest_order,order_count=excluded.order_count,
               line_count=excluded.line_count,vendor_names=excluded.vendor_names""",
            (started_at, oldest, newest, len(orders), line_count, json.dumps(vendor_names)),
        )
    return {"mode": "volledig", "orders": len(orders), "lines": line_count,
            "inventory": inventory_count, "oldest": oldest, "newest": newest}


def full_sync_many(
    supplier_vendors: dict[str, tuple[str, ...]],
) -> dict[str, dict[str, Any]]:
    """Verdeel één Shopify-bulkexport over meerdere leveranciersdossiers."""
    for slug in supplier_vendors:
        _schema(slug)
    vendor_slugs: dict[str, set[str]] = defaultdict(set)
    for slug, names in supplier_vendors.items():
        for name in names:
            if str(name).strip():
                vendor_slugs[str(name).strip().casefold()].add(slug)

    client = ShopifyClient.from_settings()
    started_at = utc_now()
    bulk_query = """{
      orders(sortKey: CREATED_AT) { edges { node {
        id createdAt updatedAt cancelledAt
        lineItems { edges { node { id sku name vendor currentQuantity } } }
      } } }
    }"""
    mutation = """mutation SupplierSalesBulk($query: String!) {
      bulkOperationRunQuery(query: $query) {
        bulkOperation { id status } userErrors { field message }
      }
    }"""
    result = client.graphql(mutation, {"query": bulk_query})["bulkOperationRunQuery"]
    if result.get("userErrors"):
        raise RuntimeError(str(result["userErrors"]))
    url = _wait_for_bulk(client, str(result["bulkOperation"]["id"]))
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    order_headers: dict[str, dict[str, Any]] = {}
    supplier_orders: dict[str, dict[str, dict[str, Any]]] = {
        slug: {} for slug in supplier_vendors
    }
    for raw in response.iter_lines(decode_unicode=True):
        if not raw:
            continue
        item = json.loads(raw)
        parent = str(item.get("__parentId") or "")
        if not parent:
            day = str(item.get("createdAt") or "")[:10]
            order_headers[str(item["id"])] = {
                "date": day, "updated_at": str(item.get("updatedAt") or ""),
                "cancelled": bool(item.get("cancelledAt")),
            }
            continue
        header = order_headers.get(parent)
        if not header:
            continue
        for slug in vendor_slugs.get(
            str(item.get("vendor") or "").strip().casefold(), set()
        ):
            order = supplier_orders[slug].setdefault(parent, {**header, "lines": []})
            order["lines"].append({
                "id": str(item["id"]),
                "sku": str(item.get("sku") or "").strip().upper(),
                "title": str(item.get("name") or "").strip(),
                "quantity": max(0, int(item.get("currentQuantity") or 0)),
            })

    inventory: dict[str, dict[str, tuple[str, int, str]]] = {
        slug: {} for slug in supplier_vendors
    }
    inventory_query = """query SupplierInventory($after: String) {
      productVariants(first: 250, after: $after) {
        nodes { sku title inventoryQuantity product { title vendor } }
        pageInfo { hasNextPage endCursor }
      }
    }"""
    after = None
    while True:
        connection = client.graphql(inventory_query, {"after": after})["productVariants"]
        for node in connection.get("nodes") or []:
            product = node.get("product") or {}
            slugs = vendor_slugs.get(
                str(product.get("vendor") or "").strip().casefold(), set()
            )
            sku = str(node.get("sku") or "").strip().upper()
            if not sku:
                continue
            title = " · ".join(filter(None, (
                str(product.get("title") or "").strip(),
                str(node.get("title") or "").strip(),
            )))
            for slug in slugs:
                inventory[slug][sku] = (
                    title, int(node.get("inventoryQuantity") or 0), started_at,
                )
        page = connection.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")

    summaries = {}
    for slug, vendor_names in supplier_vendors.items():
        supplier_orders[slug] = _orders_with_supplier_lines(supplier_orders[slug])
        oldest, newest = _history_bounds(supplier_orders[slug])
        path = init_supplier_database(slug)
        with _connect(path) as conn:
            conn.execute("DELETE FROM historical_sales_lines")
            conn.execute("DELETE FROM historical_sales_inventory")
        line_count = _replace_orders(slug, supplier_orders[slug])
        with _connect(path) as conn:
            conn.executemany(
                """INSERT INTO historical_sales_inventory
                   (sku,title,inventory_quantity,updated_at) VALUES(?,?,?,?)""",
                [(sku, *values) for sku, values in inventory[slug].items()],
            )
            conn.execute(
                """INSERT INTO historical_sales_meta
                   (id,synced_at,oldest_order,newest_order,order_count,line_count,vendor_names)
                   VALUES(1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   synced_at=excluded.synced_at,oldest_order=excluded.oldest_order,
                   newest_order=excluded.newest_order,order_count=excluded.order_count,
                   line_count=excluded.line_count,vendor_names=excluded.vendor_names""",
                (started_at, oldest, newest, len(supplier_orders[slug]), line_count,
                 json.dumps(vendor_names)),
            )
        summaries[slug] = {
            "orders": len(supplier_orders[slug]), "lines": line_count,
            "inventory": len(inventory[slug]), "vendors": vendor_names,
        }
    return summaries


def incremental_sync(slug: str, vendor_names: tuple[str, ...]) -> dict[str, Any]:
    _schema(slug)
    with _connect(init_supplier_database(slug)) as conn:
        meta = conn.execute("SELECT * FROM historical_sales_meta WHERE id=1").fetchone()
    if not meta:
        return full_sync(slug, vendor_names)
    client = ShopifyClient.from_settings()
    watermark = str(meta["synced_at"])
    sync_started = utc_now()
    query = """query SupplierSalesChanged($after: String, $query: String!) {
      orders(first: 100, after: $after, sortKey: UPDATED_AT, query: $query) {
        nodes { id createdAt updatedAt cancelledAt
          lineItems(first: 250) { nodes { id sku name vendor currentQuantity }
            pageInfo { hasNextPage } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }"""
    after = None
    orders: dict[str, dict[str, Any]] = {}
    while True:
        connection = client.graphql(query, {
            "after": after, "query": f"updated_at:>={watermark}",
        })["orders"]
        for item in connection.get("nodes") or []:
            lines = item.get("lineItems") or {}
            if (lines.get("pageInfo") or {}).get("hasNextPage"):
                raise RuntimeError(f"Order {item['id']} heeft meer dan 250 regels")
            orders[str(item["id"])] = {
                "date": str(item.get("createdAt") or "")[:10],
                "updated_at": str(item.get("updatedAt") or ""),
                "cancelled": bool(item.get("cancelledAt")),
                "lines": [{
                    "id": str(line["id"]),
                    "sku": str(line.get("sku") or "").strip().upper(),
                    "title": str(line.get("name") or "").strip(),
                    "quantity": max(0, int(line.get("currentQuantity") or 0)),
                } for line in lines.get("nodes") or []
                  if _matches_vendor(line.get("vendor"), vendor_names)],
            }
        page = connection.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
    changed_lines = _replace_orders(slug, orders)
    inventory_count = _sync_inventory(slug, client, vendor_names)
    with _connect(init_supplier_database(slug)) as conn:
        totals = conn.execute(
            "SELECT COUNT(DISTINCT order_id),COUNT(*),MIN(sale_date),MAX(sale_date) FROM historical_sales_lines"
        ).fetchone()
        conn.execute(
            """UPDATE historical_sales_meta SET synced_at=?,oldest_order=?,newest_order=?,
               order_count=?,line_count=?,vendor_names=? WHERE id=1""",
            (sync_started, totals[2] or "", totals[3] or "", totals[0], totals[1],
             json.dumps(vendor_names)),
        )
    return {"mode": "vanaf laatste update", "orders": len(orders),
            "lines": changed_lines, "inventory": inventory_count}


def sales_report(slug: str) -> dict[str, Any]:
    _schema(slug)
    cutoff = (date.today() - timedelta(days=365)).isoformat()
    with _connect(init_supplier_database(slug)) as conn:
        meta = conn.execute("SELECT * FROM historical_sales_meta WHERE id=1").fetchone()
        rows = conn.execute("""WITH skus AS (
          SELECT sku FROM historical_sales_lines UNION SELECT sku FROM historical_sales_inventory
        ) SELECT k.sku,COALESCE(MAX(s.title),MAX(i.title),'') title,
          COALESCE(SUM(s.quantity),0) total,MIN(s.sale_date) first_sale,
          MAX(s.sale_date) last_sale,COALESCE(MAX(i.inventory_quantity),0) inventory,
          COALESCE(SUM(CASE WHEN s.sale_date>=? THEN s.quantity ELSE 0 END),0) sold_12m
          FROM skus k LEFT JOIN historical_sales_lines s ON s.sku=k.sku
          LEFT JOIN historical_sales_inventory i ON i.sku=k.sku
          GROUP BY k.sku ORDER BY total DESC,k.sku""", (cutoff,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["years"] = {int(y): int(q) for y, q in conn.execute(
                """SELECT CAST(substr(sale_date,1,4) AS INTEGER),SUM(quantity)
                   FROM historical_sales_lines WHERE sku=? GROUP BY 1""", (row["sku"],))}
            item["months"] = {(int(y), int(m)): int(q) for y, m, q in conn.execute(
                """SELECT CAST(substr(sale_date,1,4) AS INTEGER),
                   CAST(substr(sale_date,6,2) AS INTEGER),SUM(quantity)
                   FROM historical_sales_lines WHERE sku=? GROUP BY 1,2""", (row["sku"],))}
            monthly = int(item["sold_12m"]) / 12
            item["months_cover"] = (
                max(0, int(item["inventory"])) / monthly if monthly else None
            )
            result.append(item)
        return {"meta": dict(meta) if meta else None, "rows": result}
