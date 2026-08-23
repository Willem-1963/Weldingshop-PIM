from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.shopify.client import ShopifyClient
from app.suppliers.hub import get_supplier, init_supplier_database
from app.suppliers.routes import supplier_route


NAMESPACE = "shopify--discovery--product_recommendation"
KEY = "complementary_products"
TYPE = "list.product_reference"
MAX_LINKS = 10
THROTTLE_RETRIES = 8


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _graphql_with_throttle_retry(
    client: ShopifyClient,
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retry GraphQL operations rejected by Shopify's temporary cost limit."""
    for attempt in range(THROTTLE_RETRIES):
        try:
            return client.graphql(query, variables)
        except RuntimeError as exc:
            if "THROTTLED" not in str(exc).upper() or attempt == THROTTLE_RETRIES - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def init_complementary_products(slug: str) -> None:
    path = init_supplier_database(slug)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS complementary_product_links(
                source_sku TEXT NOT NULL,
                target_sku TEXT NOT NULL,
                position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 10),
                reason TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'automatic',
                locked INTEGER NOT NULL DEFAULT 0,
                target_shopify_product_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(source_sku,target_sku),
                UNIQUE(source_sku,position),
                CHECK(source_sku<>target_sku),
                FOREIGN KEY(source_sku) REFERENCES products(sku) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_complementary_target
            ON complementary_product_links(target_sku);
            """
        )


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _filters(value: str) -> tuple[str, ...]:
    try:
        values = json.loads(value or "[]")
    except json.JSONDecodeError:
        values = []
    return tuple(sorted({_text(item).casefold() for item in values if _text(item)}))


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in {"voor", "met", "van", "the"}
    }


def _product_rows(slug: str) -> list[dict[str, Any]]:
    path = init_supplier_database(slug)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            """SELECT sku,source_title,brand,category,category_full,
                      product_type,product_group_name,filter_values_json
                 FROM products WHERE source_present=1 ORDER BY sku"""
        )]


def _signature(product: dict[str, Any]) -> tuple:
    return (
        _filters(product.get("filter_values_json") or "[]"),
        _text(product.get("category_full") or product.get("category")).casefold(),
        _text(product.get("brand")).casefold(),
        _text(product.get("source_title")).casefold(),
        _text(product.get("sku")),
    )


def _reason(source: dict[str, Any], target: dict[str, Any]) -> tuple[str, float]:
    reasons = [f"Productgroep: {_text(source['product_group_name'])}"]
    score = 0.72
    shared = set(_filters(source.get("filter_values_json") or "[]")) & set(
        _filters(target.get("filter_values_json") or "[]")
    )
    if shared:
        reasons.append("Gedeelde filters: " + ", ".join(sorted(shared)[:4]))
        score += min(0.18, len(shared) * 0.06)
    source_category = _text(source.get("category_full") or source.get("category"))
    target_category = _text(target.get("category_full") or target.get("category"))
    if source_category and source_category.casefold() == target_category.casefold():
        reasons.append(f"Categorie: {source_category}")
        score += 0.05
    title_overlap = _tokens(source.get("source_title") or "") & _tokens(
        target.get("source_title") or ""
    )
    if title_overlap:
        score += min(0.05, len(title_overlap) * 0.01)
    return "; ".join(reasons), min(0.99, score)


def build_complementary_preview(slug: str) -> dict[str, Any]:
    init_complementary_products(slug)
    products = _product_rows(slug)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for product in products:
        group = _text(product.get("product_group_name"))
        if group:
            groups[group.casefold()].append(product)

    path = init_supplier_database(slug)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        locked_rows = [dict(row) for row in connection.execute(
            """SELECT * FROM complementary_product_links
                 WHERE locked=1 ORDER BY source_sku,position"""
        )]
    locked: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in locked_rows:
        locked[row["source_sku"]].append(row)

    proposals: list[dict[str, Any]] = []
    eligible = 0
    oversized = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        eligible += len(members)
        oversized += int(len(members) - 1 > MAX_LINKS)
        ordered = sorted(members, key=_signature)
        size = len(ordered)
        by_sku = {item["sku"]: item for item in ordered}
        for index, source_product in enumerate(ordered):
            chosen: list[str] = []
            for row in locked.get(source_product["sku"], []):
                target_sku = row["target_sku"]
                if target_sku != source_product["sku"] and target_sku not in chosen:
                    chosen.append(target_sku)
                    proposals.append({**row, "preserved": True})
                    if len(chosen) == MAX_LINKS:
                        break
            offsets = []
            for distance in range(1, size):
                offsets.extend((distance, -distance))
            for offset in offsets:
                if len(chosen) >= MAX_LINKS:
                    break
                target = ordered[(index + offset) % size]
                if target["sku"] in chosen or target["sku"] == source_product["sku"]:
                    continue
                reason, confidence = _reason(source_product, target)
                chosen.append(target["sku"])
                proposals.append({
                    "source_sku": source_product["sku"],
                    "target_sku": target["sku"],
                    "position": len(chosen),
                    "reason": reason,
                    "confidence": confidence,
                    "source": "automatic",
                    "locked": 0,
                    "target_shopify_product_id": None,
                    "preserved": False,
                })
    product_by_sku = {product["sku"]: product for product in products}
    for proposal in proposals:
        source_product = product_by_sku.get(proposal["source_sku"], {})
        target_product = product_by_sku.get(proposal["target_sku"], {})
        proposal["product_group"] = _text(
            source_product.get("product_group_name")
        )
        proposal["source_title"] = _text(source_product.get("source_title"))
        proposal["target_title"] = _text(target_product.get("source_title"))
    group_details = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_skus = {item["sku"] for item in members}
        group_details.append({
            "product_group": _text(members[0].get("product_group_name")),
            "products": len(members),
            "proposed_links": sum(
                proposal["source_sku"] in member_skus
                for proposal in proposals
            ),
            "over_shopify_limit": len(members) - 1 > MAX_LINKS,
            "example_products": ", ".join(
                _text(item.get("source_title"))
                for item in sorted(members, key=_signature)[:3]
            ),
        })
    group_details.sort(
        key=lambda item: (-item["products"], item["product_group"].casefold())
    )
    return {
        "supplier_slug": slug,
        "products": len(products),
        "eligible_products": eligible,
        "groups": sum(len(items) > 1 for items in groups.values()),
        "groups_over_limit": oversized,
        "links": len(proposals),
        "preserved_links": sum(bool(item.get("preserved")) for item in proposals),
        "group_details": group_details,
        "proposals": proposals,
    }


def generate_complementary_products(slug: str) -> dict[str, Any]:
    preview = build_complementary_preview(slug)
    now = _now()
    path = init_supplier_database(slug)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM complementary_product_links WHERE locked=0")
        for item in preview["proposals"]:
            if item.get("preserved"):
                continue
            connection.execute(
                """INSERT INTO complementary_product_links(
                       source_sku,target_sku,position,reason,confidence,source,
                       locked,target_shopify_product_id,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_sku,target_sku) DO UPDATE SET
                       position=excluded.position,reason=excluded.reason,
                       confidence=excluded.confidence,source=excluded.source,
                       updated_at=excluded.updated_at""",
                (item["source_sku"], item["target_sku"], item["position"],
                 item["reason"], item["confidence"], item["source"], 0,
                 None, now, now),
            )
    return {key: value for key, value in preview.items() if key != "proposals"}


def complementary_stats(slug: str) -> dict[str, int]:
    init_complementary_products(slug)
    path = init_supplier_database(slug)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """SELECT COUNT(*) links,COUNT(DISTINCT source_sku) products,
                      SUM(locked) locked
                 FROM complementary_product_links"""
        ).fetchone()
    return {"links": int(row[0]), "products": int(row[1]), "locked": int(row[2] or 0)}


def _shopify_snapshot(slug: str, client: ShopifyClient) -> list[dict[str, Any]]:
    supplier = get_supplier(slug) or {}
    route = supplier_route(slug)
    names = route.shopify_vendor_names or (supplier.get("name") or slug,)
    escaped = [str(name).replace('"', '\\"') for name in names]
    vendor_query = " OR ".join(f'vendor:"{name}"' for name in escaped)
    if len(escaped) > 1:
        vendor_query = f"({vendor_query})"
    query = """
    query($after:String,$query:String!){products(first:100,after:$after,query:$query){
      pageInfo{hasNextPage endCursor}nodes{id title status variants(first:100){nodes{sku}}
      complementary:metafield(namespace:"shopify--discovery--product_recommendation",key:"complementary_products"){
        value references(first:10){nodes{... on Product{id variants(first:100){nodes{sku}}}}}
      }} }}
    """
    after = None
    result = []
    while True:
        page = _graphql_with_throttle_retry(
            client, query, {"after": after, "query": vendor_query}
        )["products"]
        result.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return result
        after = page["pageInfo"]["endCursor"]


def import_shopify_complementary_products(
    slug: str, client: ShopifyClient | None = None,
) -> dict[str, int]:
    init_complementary_products(slug)
    client = client or ShopifyClient.from_settings()
    products = _shopify_snapshot(slug, client)
    product_skus = {
        product["id"]: [
            _text(item.get("sku")) for item in product["variants"]["nodes"]
            if _text(item.get("sku"))
        ] for product in products
    }
    imported = 0
    now = _now()
    path = init_supplier_database(slug)
    with sqlite3.connect(path) as connection:
        known = {row[0] for row in connection.execute("SELECT sku FROM products")}
        for product in products:
            source_skus = [sku for sku in product_skus[product["id"]] if sku in known]
            references = ((product.get("complementary") or {}).get("references") or {}).get("nodes") or []
            target_skus = []
            for target in references:
                candidates = product_skus.get(target["id"]) or [
                    _text(item.get("sku")) for item in target["variants"]["nodes"]
                ]
                target_sku = next((sku for sku in candidates if sku in known), "")
                if target_sku:
                    target_skus.append((target_sku, target["id"]))
            for source_sku in source_skus:
                # Een nieuwe Shopify-import wordt altijd vóór een nieuwe
                # generatie uitgevoerd. Ververs ook eerder uit Shopify
                # geïmporteerde relaties: hun product-ID kan na verwijderen
                # en opnieuw aanmaken niet meer bestaan. Handmatige locks
                # blijven wel behouden.
                connection.execute(
                    "DELETE FROM complementary_product_links "
                    "WHERE source_sku=? AND (locked=0 OR source='shopify_existing')",
                    (source_sku,),
                )
                position = 0
                for target_sku, target_id in target_skus:
                    if target_sku == source_sku or position >= MAX_LINKS:
                        continue
                    position += 1
                    connection.execute(
                        """INSERT INTO complementary_product_links(
                               source_sku,target_sku,position,reason,confidence,
                               source,locked,target_shopify_product_id,created_at,updated_at
                           ) VALUES(?,?,?,'Bestaande Shopify-koppeling',1,'shopify_existing',1,?,?,?)
                           ON CONFLICT(source_sku,target_sku) DO UPDATE SET
                               locked=1,source='shopify_existing',
                               target_shopify_product_id=excluded.target_shopify_product_id,
                               updated_at=excluded.updated_at""",
                        (source_sku, target_sku, position, target_id, now, now),
                    )
                    imported += 1
    return {"products": len(products), "links_imported": imported}


def sync_complementary_products_to_shopify(
    slug: str, client: ShopifyClient | None = None, *, dry_run: bool = False,
) -> dict[str, int]:
    init_complementary_products(slug)
    client = client or ShopifyClient.from_settings()
    products = _shopify_snapshot(slug, client)
    sku_to_product: dict[str, str] = {}
    product_to_skus: dict[str, list[str]] = defaultdict(list)
    for product in products:
        for variant in product["variants"]["nodes"]:
            sku = _text(variant.get("sku"))
            if sku:
                sku_to_product[sku.upper()] = product["id"]
                product_to_skus[product["id"]].append(sku)
    path = init_supplier_database(slug)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(
            """SELECT * FROM complementary_product_links
                 ORDER BY source_sku,position"""
        )]
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_source[row["source_sku"].upper()].append(row)
    payloads = []
    for product_id, source_skus in product_to_skus.items():
        targets = []
        for source_sku in source_skus:
            for row in by_source.get(source_sku.upper(), []):
                # Een actuele SKU-match heeft voorrang op een opgeslagen ID.
                # Shopify kan een verwijderd en opnieuw aangemaakt product
                # immers een andere ID hebben gegeven.
                target_id = sku_to_product.get(
                    row["target_sku"].upper()
                ) or row.get("target_shopify_product_id")
                if target_id and target_id != product_id and target_id not in targets:
                    targets.append(target_id)
                    if len(targets) == MAX_LINKS:
                        break
            if len(targets) == MAX_LINKS:
                break
        if not targets:
            continue
        payloads.append({"ownerId": product_id, "namespace": NAMESPACE,
                         "key": KEY, "type": TYPE,
                         "value": json.dumps(targets)})
    if dry_run:
        return {"shopify_products_found": len(products),
                "products_with_links": len(payloads), "updated": 0}
    updated = 0
    for index in range(0, len(payloads), 25):
        result = _graphql_with_throttle_retry(
            client,
            """mutation($metafields:[MetafieldsSetInput!]!){
                 metafieldsSet(metafields:$metafields){
                   metafields{id} userErrors{field message code}
                 }}""",
            {"metafields": payloads[index:index + 25]},
        )["metafieldsSet"]
        if result.get("userErrors"):
            raise RuntimeError(str(result["userErrors"]))
        updated += len(result.get("metafields") or [])
    return {"shopify_products_found": len(products),
            "products_with_links": len(payloads), "updated": updated}


def synchronize_complementary_products(
    slug: str, client: ShopifyClient | None = None,
) -> dict[str, Any]:
    """Run the only safe mutation order for complementary products.

    Existing Shopify choices are imported and locked first. Automatic links
    are then rebuilt around those locks and immediately written to Shopify.
    Keeping these operations behind one entry point prevents a later import
    from leaving the freshly generated automatic links deleted.
    """
    client = client or ShopifyClient.from_settings()
    imported = import_shopify_complementary_products(slug, client=client)
    generated = generate_complementary_products(slug)
    uploaded = sync_complementary_products_to_shopify(slug, client=client)
    return {
        "imported": imported,
        "generated": generated,
        "uploaded": uploaded,
    }
