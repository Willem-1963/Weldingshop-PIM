"""Synchroniseer vaste Certilas POS-prijzen naar variantmetafields voor Shopify Functions."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.shopify.client import ShopifyClient

PRICE_LIST_ID = "gid://shopify/PriceList/31914918217"
NAMESPACE = "custom"
KEY = "certilas_pos_prijs"
BATCH = 25


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def certilas_variants(client: ShopifyClient, include_metafield: bool = False) -> list[dict]:
    field = f'posPrice: metafield(namespace: "{NAMESPACE}", key: "{KEY}"){{value}}' if include_metafield else ""
    query = f'''query($after:String){{productVariants(first:250,after:$after,query:"vendor:Certilas"){{pageInfo{{hasNextPage endCursor}} nodes{{id sku {field} product{{vendor status title}}}}}}}}'''
    after = None
    rows: list[dict] = []
    while True:
        connection = client.graphql(query, {"after": after})["productVariants"]
        rows.extend(
            row for row in connection["nodes"]
            if str(row["product"].get("vendor") or "").casefold() in {"certilas", "ceweld"}
        )
        if not connection["pageInfo"]["hasNextPage"]:
            return rows
        after = connection["pageInfo"]["endCursor"]


def pos_prices(client: ShopifyClient, variant_ids: list[str]) -> dict[str, str]:
    query = '''query($id:ID!,$query:String!){priceList(id:$id){prices(first:250,query:$query){nodes{price{amount currencyCode} variant{id sku}}}}}'''
    result: dict[str, str] = {}
    for offset in range(0, len(variant_ids), 40):
        batch = variant_ids[offset:offset + 40]
        search = " OR ".join(f"variant_id:{item.rsplit('/', 1)[-1]}" for item in batch)
        nodes = client.graphql(query, {"id": PRICE_LIST_ID, "query": search})["priceList"]["prices"]["nodes"]
        for node in nodes:
            price = node["price"]
            amount = Decimal(str(price.get("amount") or "0"))
            if price.get("currencyCode") == "EUR" and amount > 0:
                result[node["variant"]["id"]] = f"{amount:.2f}"
    return result


def set_metafields(client: ShopifyClient, values: list[dict]) -> list[dict]:
    mutation = '''mutation($metafields:[MetafieldsSetInput!]!){metafieldsSet(metafields:$metafields){metafields{id namespace key value} userErrors{field message code}}}'''
    written: list[dict] = []
    for offset in range(0, len(values), BATCH):
        batch = values[offset:offset + BATCH]
        payload = client.graphql(mutation, {"metafields": batch})["metafieldsSet"]
        if payload.get("userErrors"):
            raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
        written.extend(payload.get("metafields") or [])
    return written


def run(apply: bool) -> dict:
    client = ShopifyClient.from_settings()
    variants = certilas_variants(client, include_metafield=True)
    current = {row["id"]: str((row.get("posPrice") or {}).get("value") or "") for row in variants}
    prices = pos_prices(client, [row["id"] for row in variants])
    values = [
        {"ownerId": row["id"], "namespace": NAMESPACE, "key": KEY,
         "type": "number_decimal", "value": prices[row["id"]]}
        for row in variants if row["id"] in prices
        and Decimal(current.get(row["id"]) or "0") != Decimal(prices[row["id"]])
    ]
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "apply": apply,
        "price_list_id": PRICE_LIST_ID,
        "variants": len(variants),
        "positive_pos_prices": len(prices),
        "blocked_zero_or_missing": len(variants) - len(prices),
        "changed": len(values),
        "unchanged": len(prices) - len(values),
        "written": 0,
        "verified": 0,
    }
    if apply:
        written = set_metafields(client, values)
        report["written"] = len(written)
        expected = {row["ownerId"]: row["value"] for row in values}
        readback = certilas_variants(client, include_metafield=True)
        mismatches = []
        for row in readback:
            if row["id"] not in expected:
                continue
            actual = str((row.get("posPrice") or {}).get("value") or "")
            if Decimal(actual or "0") != Decimal(expected[row["id"]]):
                mismatches.append({"id": row["id"], "sku": row.get("sku"), "expected": expected[row["id"]], "actual": actual})
        report["verified"] = len(expected) - len(mismatches)
        report["mismatches"] = mismatches
        if mismatches:
            raise RuntimeError(f"POS-prijsmetafield readback mismatch: {len(mismatches)}")
    audit_dir = Path("data/audit/certilas-pos-price-metafields")
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / f"run-{utc_stamp()}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + chr(10))
    report["audit"] = str(path)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.apply), ensure_ascii=False, indent=2))
