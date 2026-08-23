from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/srv/ai-product-factory")
sys.path.insert(0, "/srv/ai-product-factory/scripts")

from app.shopify.client import ShopifyClient
from app.shopify.sync import _shopify_products
from kentie_match_batch_sync import graphql_retry


DB = "/srv/ai-product-factory/data/database/suppliers/kentie.sqlite"
AUDIT = Path("/srv/ai-product-factory/data/audit/kentie-shopify-match-cleanup-20260818.json")
client = ShopifyClient.from_settings()
live = _shopify_products(client, "Kentie", "kentie")
targets: dict[str, dict] = {}
for match in live.values():
    product = match["product"]
    if "match" not in str(product.get("descriptionHtml") or "").casefold():
        continue
    row = targets.setdefault(product["id"], {
        "id": product["id"], "title": product["title"],
        "status": product["status"], "old_description": product.get("descriptionHtml") or "",
        "skus": [],
    })
    row["skus"].append(str(match["variant"].get("sku") or "").strip())
if len(targets) != 129:
    raise RuntimeError(f"Veiligheidsstop: verwacht 129 Shopify-producten, gevonden {len(targets)}")

with sqlite3.connect(DB) as connection:
    connection.row_factory = sqlite3.Row
    pim = {row["sku"].upper(): dict(row) for row in connection.execute(
        "SELECT sku,html_description FROM products WHERE source_present=1"
    )}
for row in targets.values():
    candidates = [pim.get(sku.upper()) for sku in row["skus"] if sku]
    source = next((item for item in candidates if item), None)
    if not source:
        raise RuntimeError(f"Geen actuele PIM-bron voor {row['skus']}")
    description = str(source["html_description"] or "")
    if len(description) < 80 or "match" in description.casefold():
        raise RuntimeError(f"PIM-tekst nog ongeschikt voor {row['skus']}")
    row["new_description"] = description

AUDIT.parent.mkdir(parents=True, exist_ok=True)
AUDIT.write_text(json.dumps({
    "created_at": datetime.now(timezone.utc).isoformat(),
    "products": list(targets.values()),
}, ensure_ascii=False, indent=2), encoding="utf-8")

rows = list(targets.values())
for offset in range(0, len(rows), 10):
    batch = rows[offset:offset + 10]
    for row in batch:
        payload = graphql_retry(client, """mutation($input:ProductInput!){
          productUpdate(input:$input){product{id status} userErrors{field message}}}""",
          {"input": {"id": row["id"], "descriptionHtml": row["new_description"]}}
        )["productUpdate"]
        if payload.get("userErrors"):
            raise RuntimeError(f"{row['skus']}: {payload['userErrors']}")
        if (payload.get("product") or {}).get("status") != row["status"]:
            raise RuntimeError(f"Status gewijzigd voor {row['skus']}")
    print(json.dumps({"batch": offset // 10 + 1,
                      "skus": [sku for row in batch for sku in row["skus"]]},
                     ensure_ascii=False), flush=True)

after = _shopify_products(client, "Kentie", "kentie")
remaining = {
    match["product"]["id"] for match in after.values()
    if "match" in str(match["product"].get("descriptionHtml") or "").casefold()
}
if remaining:
    raise RuntimeError(f"Shopify bevat nog {len(remaining)} Kentie-producten met match")
print(json.dumps({"updated": len(targets), "remaining": 0,
                  "audit": str(AUDIT)}, ensure_ascii=False), flush=True)
