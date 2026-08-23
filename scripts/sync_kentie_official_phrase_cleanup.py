from __future__ import annotations

import json
import sqlite3
import sys

sys.path.insert(0, "/srv/ai-product-factory")
sys.path.insert(0, "/srv/ai-product-factory/scripts")

from app.shopify.client import ShopifyClient
from kentie_match_batch_sync import graphql_retry, targeted_live


DB = "/srv/ai-product-factory/data/database/suppliers/kentie.sqlite"
OLD = "/root/kentie-before-official-phrase-cleanup-20260818.sqlite"


with sqlite3.connect(OLD) as connection:
    skus = [row[0] for row in connection.execute(
        """SELECT sku FROM products WHERE source_present=1 AND
        lower(coalesce(source_description,'')) LIKE '%dit is een officieel kentie%'
        ORDER BY sku"""
    )]
if len(skus) != 72:
    raise RuntimeError(f"Veiligheidsstop: verwacht 72 SKU's, gevonden {len(skus)}")
with sqlite3.connect(DB) as connection:
    descriptions = dict(connection.execute(
        "SELECT sku,html_description FROM products WHERE sku IN (%s)"
        % ",".join("?" for _ in skus), skus,
    ))

client = ShopifyClient.from_settings()
for offset in range(0, len(skus), 10):
    batch = skus[offset:offset + 10]
    live = targeted_live(client, batch)
    missing = [sku for sku in batch if sku not in live]
    if missing:
        raise RuntimeError(f"Shopify mist SKU's: {missing}")
    for sku in batch:
        payload = graphql_retry(client, """mutation($input:ProductInput!){
        productUpdate(input:$input){product{id status} userErrors{field message}}}""", {
            "input": {"id": live[sku]["product"]["id"],
                      "descriptionHtml": descriptions[sku]},
        })["productUpdate"]
        if payload.get("userErrors"):
            raise RuntimeError(f"{sku}: {payload['userErrors']}")
    verified = targeted_live(client, batch)
    failures = [sku for sku in batch if
                "dit is een officieel kentie" in
                str(verified[sku]["product"].get("descriptionHtml") or "").casefold()
                or verified[sku]["product"].get("status") != "ACTIVE"]
    if failures:
        raise RuntimeError(f"Shopify-eindcontrole mislukt: {failures}")
    print(json.dumps({"batch": offset // 10 + 1, "skus": batch},
                     ensure_ascii=False), flush=True)
