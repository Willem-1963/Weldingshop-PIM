from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.shopify.client import ShopifyClient
from app.shopify.sync import _run_rows


LOCATION_ID = "gid://shopify/Location/116493091145"
VENDOR = "Valkenpower"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    client = ShopifyClient.from_settings()
    products = []
    after = None
    while True:
        connection = client.graphql(
            """
            query($after:String,$query:String!,$location:ID!){
              products(first:100,after:$after,query:$query){
                pageInfo{hasNextPage endCursor}
                nodes{
                  variants(first:100){nodes{
                    sku inventoryItem{
                      id inventoryLevel(locationId:$location){
                        quantities(names:["available"]){name quantity}
                      }
                    }
                  }}
                }
              }
            }
            """,
            {
                "after": after,
                "query": f'vendor:"{VENDOR}"',
                "location": LOCATION_ID,
            },
        )["products"]
        products.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            break
        after = connection["pageInfo"]["endCursor"]
    rows = []
    variant_count = 0
    for product in products:
        for variant in product["variants"]["nodes"]:
            variant_count += 1
            item = variant.get("inventoryItem") or {}
            level = item.get("inventoryLevel") or {}
            quantity = next((
                int(value.get("quantity") or 0)
                for value in level.get("quantities") or []
                if value.get("name") == "available"
            ), 0)
            if level:
                rows.append({
                    "sku": (variant.get("sku") or "").strip(),
                    "inventoryItemId": item["id"],
                    "locationId": LOCATION_ID,
                    "previousQuantity": quantity,
                })
    output = Path("data/output/shopify")
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = output / f"valkenpower-sp-tools-inventory-backup-{stamp}.json"
    report.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    if args.apply:
        nonzero_rows = [row for row in rows if row["previousQuantity"]]
        for index in range(0, len(nonzero_rows), 100):
            chunk = nonzero_rows[index:index + 100]
            payload = client.graphql(
                """
                mutation($input:InventorySetQuantitiesInput!,$key:String!){
                  inventorySetQuantities(input:$input) @idempotent(key:$key){
                    userErrors{field message code}
                  }
                }
                """,
                {
                    "key": str(uuid.uuid4()),
                    "input": {
                        "name": "available",
                        "reason": "correction",
                        "referenceDocumentUri": "pim://weldingshop/valkenpower/clear-sp-tools",
                        "quantities": [{
                            "inventoryItemId": row["inventoryItemId"],
                            "locationId": LOCATION_ID,
                            "quantity": 0,
                            "changeFromQuantity": row["previousQuantity"],
                        } for row in chunk],
                    },
                },
            )["inventorySetQuantities"]
            if payload.get("userErrors"):
                raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
        _run_rows(
            client,
            [{
                "inventoryItemId": row["inventoryItemId"],
                "inventoryItemUpdates": [{
                    "locationId": LOCATION_ID,
                    "activate": False,
                }],
            } for row in rows],
            """
            mutation call(
              $inventoryItemId:ID!,
              $inventoryItemUpdates:[InventoryBulkToggleActivationInput!]!
            ){
              inventoryBulkToggleActivation(
                inventoryItemId:$inventoryItemId,
                inventoryItemUpdates:$inventoryItemUpdates
              ){userErrors{field message code}}
            }
            """,
            f"valkenpower-sp-tools-deactivate-{stamp}.jsonl",
            f"pim-valkenpower-sp-tools-deactivate-{uuid.uuid4().hex[:8]}",
            None,
            0,
            100,
            "Magazijn-SP-Tools bij Valkenpower verwijderen",
        )
    print(json.dumps({
        "vendor": VENDOR,
        "location": LOCATION_ID,
        "products_found": len(products),
        "variants_found": variant_count,
        "activated_levels": len(rows),
        "nonzero_levels": sum(bool(row["previousQuantity"]) for row in rows),
        "applied": args.apply,
        "backup": str(report),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
