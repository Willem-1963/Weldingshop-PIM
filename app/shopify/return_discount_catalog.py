from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from app.shopify.client import ShopifyClient

MACHINE_COLLECTIONS = {
    "mig": "gid://shopify/Collection/698730414409",
    "tig": "gid://shopify/Collection/90191527996",
    "plasma": "gid://shopify/Collection/699059241289",
}
ACCESSORY_COLLECTIONS = {
    "mig": [
        "gid://shopify/Collection/88264933436",
        "gid://shopify/Collection/88278368316",
        "gid://shopify/Collection/88793776188",
        "gid://shopify/Collection/658396578121",
        "gid://shopify/Collection/698730447177",
    ],
    "tig": [
        "gid://shopify/Collection/89067782204",
        "gid://shopify/Collection/88278630460",
        "gid://shopify/Collection/88926421052",
        "gid://shopify/Collection/699018543433",
        "gid://shopify/Collection/698730742089",
    ],
    "plasma": [
        "gid://shopify/Collection/88280498236",
        "gid://shopify/Collection/137444065340",
        "gid://shopify/Collection/137445998652",
    ],
}
AUDIT_DIR = Path("data/audit/return-discount-catalog")


def _collection_products(client: ShopifyClient, collection_id: str) -> tuple[str, str, list[dict[str, Any]]]:
    after = None
    rows: list[dict[str, Any]] = []
    title = handle = ""
    while True:
        data = client.graphql(
            """query($id:ID!,$after:String){collection(id:$id){title handle products(first:100,after:$after){nodes{id title handle status machineGroup:metafield(namespace:"custom",key:"return_discount_machine_group"){value} accessory:metafield(namespace:"custom",key:"return_discount_accessory"){value} variants(first:100){nodes{id sku price minimum:metafield(namespace:"custom",key:"return_discount_min_price"){value} inventoryItem{unitCost{amount currencyCode}}} pageInfo{hasNextPage endCursor}}} pageInfo{hasNextPage endCursor}}}}""",
            {"id": collection_id, "after": after},
        ).get("collection")
        if not data:
            raise RuntimeError(f"Collectie ontbreekt: {collection_id}")
        title, handle = data["title"], data["handle"]
        rows.extend(data["products"]["nodes"])
        page = data["products"]["pageInfo"]
        if not page["hasNextPage"]:
            break
        after = page["endCursor"]
    for product in rows:
        page = product["variants"]["pageInfo"]
        after = page["endCursor"]
        while page["hasNextPage"]:
            extra = client.graphql(
                """query($id:ID!,$after:String){product(id:$id){variants(first:100,after:$after){nodes{id sku price minimum:metafield(namespace:"custom",key:"return_discount_min_price"){value} inventoryItem{unitCost{amount currencyCode}}} pageInfo{hasNextPage endCursor}}}}""",
                {"id": product["id"], "after": after},
            )["product"]["variants"]
            product["variants"]["nodes"].extend(extra["nodes"])
            page = extra["pageInfo"]
            after = page["endCursor"]
    return title, handle, rows


def _minimum_price(variant: dict[str, Any]) -> str | None:
    cost = (variant.get("inventoryItem") or {}).get("unitCost") or {}
    if not cost.get("amount") or cost.get("currencyCode") != "EUR":
        return None
    value = (Decimal(cost["amount"]) * Decimal("1.41")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(value, "f")


def _set_metafields(client: ShopifyClient, values: list[dict[str, str]]) -> int:
    written = 0
    for offset in range(0, len(values), 25):
        batch = values[offset:offset + 25]
        payload = client.graphql(
            """mutation($metafields:[MetafieldsSetInput!]!){metafieldsSet(metafields:$metafields){metafields{id} userErrors{field message code}}}""",
            {"metafields": batch},
        )["metafieldsSet"]
        if payload.get("userErrors"):
            raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
        written += len(payload.get("metafields") or [])
    return written


def run(apply: bool = False) -> dict[str, Any]:
    client = ShopifyClient.from_settings()
    machines: dict[str, dict[str, Any]] = {}
    accessories: dict[str, dict[str, Any]] = {}
    collections: list[dict[str, Any]] = []
    for group, collection_id in MACHINE_COLLECTIONS.items():
        title, handle, products = _collection_products(client, collection_id)
        collections.append({"kind": "machine", "group": group, "title": title, "handle": handle, "products": len(products)})
        for product in products:
            previous = machines.get(product["id"])
            if previous and previous["group"] != group:
                raise RuntimeError(f"Machine staat in meerdere groepen: {product['id']}")
            machines[product["id"]] = {"group": group, "product": product}
    for group, collection_ids in ACCESSORY_COLLECTIONS.items():
        for collection_id in collection_ids:
            title, handle, products = _collection_products(client, collection_id)
            collections.append({"kind": "accessory", "group": group, "title": title, "handle": handle, "products": len(products)})
            for product in products:
                accessories.setdefault(product["id"], product)
    overlap = sorted(set(machines) & set(accessories))
    if overlap:
        raise RuntimeError(f"Machine/accessoire-overlap niet toegestaan: {overlap}")
    writes: list[dict[str, str]] = []
    for product_id, item in machines.items():
        current = str((item["product"].get("machineGroup") or {}).get("value") or "")
        if current != item["group"]:
            writes.append({"ownerId": product_id, "namespace": "custom", "key": "return_discount_machine_group", "type": "single_line_text_field", "value": item["group"]})
    missing_cost = blocked_floor = full_ten = partial_floor = variants = 0
    for product_id, product in accessories.items():
        current = str((product.get("accessory") or {}).get("value") or "").casefold()
        if current != "true":
            writes.append({"ownerId": product_id, "namespace": "custom", "key": "return_discount_accessory", "type": "boolean", "value": "true"})
        for variant in product["variants"]["nodes"]:
            variants += 1
            minimum = _minimum_price(variant)
            if minimum is None:
                missing_cost += 1
                continue
            current_minimum = str((variant.get("minimum") or {}).get("value") or "")
            if Decimal(current_minimum or "0") != Decimal(minimum):
                writes.append({"ownerId": variant["id"], "namespace": "custom", "key": "return_discount_min_price", "type": "number_decimal", "value": minimum})
            price = Decimal(variant["price"])
            floor = Decimal(minimum)
            target = (price * Decimal("0.90")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if floor >= price:
                blocked_floor += 1
            elif target >= floor:
                full_ten += 1
            else:
                partial_floor += 1
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "apply": apply,
        "machine_products": len(machines),
        "accessory_products": len(accessories),
        "accessory_variants": variants,
        "full_10pct": full_ten,
        "partial_floor": partial_floor,
        "blocked_floor": blocked_floor,
        "missing_cost": missing_cost,
        "changes": len(writes),
        "change_sample": writes[:50],
        "collections": collections,
    }
    report["written"] = _set_metafields(client, writes) if apply else 0
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIT_DIR / ("run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + chr(10))
    report["audit"] = str(path)
    return report
