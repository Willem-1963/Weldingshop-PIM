#!/usr/bin/env python3
"""Publish Valkenpower technical specs from PIM source to Shopify pim.* only.

Run on the PIM server from /root/weldingshop-pim. This intentionally does not
touch inventory, prices, descriptions, product status, media, tags, or ERP.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.shopify.client import ShopifyClient
from app.shopify.sync import (
    _shopify_products,
    _source_products,
    _valkenpower_pim_metafields,
)
from app.suppliers.hub import get_supplier


ROOT = Path("/root/weldingshop-pim")
OUT_DIR = ROOT / "output" / "vp-specs-source-publish"


SET_MUTATION = """
mutation($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id namespace key ownerType }
    userErrors { field message code }
  }
}
"""


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _source_metafields(product: dict[str, Any]) -> dict[str, dict[str, str]]:
    try:
        product["_raw_data"] = json.loads(product.get("raw_data_json") or "{}")
    except json.JSONDecodeError:
        product["_raw_data"] = {}
    product["_supplier_slug"] = "valkenpower"
    result = {}
    for metafield in _valkenpower_pim_metafields(product):
        if (
            metafield.get("namespace") == "pim"
            and metafield.get("key") in {"specificaties", "specs_json"}
            and str(metafield.get("value") or "").strip()
        ):
            result[str(metafield["key"])] = metafield
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = OUT_DIR / f"vp-specs-source-publish-{stamp}.json"

    supplier = get_supplier("valkenpower") or {}
    vendor = supplier.get("name") or "Valkenpower"
    client = ShopifyClient.from_settings()

    source = _source_products("valkenpower")
    if args.limit:
        source = source[:args.limit]
    existing = _shopify_products(client, vendor, "valkenpower")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped = Counter()
    for product in source:
        sku = str(product.get("sku") or "").strip().upper()
        match = existing.get(sku)
        if not match:
            skipped["no_shopify_match"] += 1
            continue
        fields = _source_metafields(product)
        if not {"specificaties", "specs_json"}.issubset(fields):
            skipped["no_source_specs"] += 1
            continue
        owner_id = str(match["product"]["id"])
        grouped[owner_id].append({
            "sku": sku,
            "handle": match["product"].get("handle"),
            "title": match["product"].get("title"),
            "fields": fields,
        })

    planned = []
    conflicts = []
    for owner_id, rows in grouped.items():
        signatures = {
            (
                rows_item["fields"]["specificaties"]["value"],
                rows_item["fields"]["specs_json"]["value"],
            )
            for rows_item in rows
        }
        if len(signatures) > 1:
            conflicts.append({
                "owner_id": owner_id,
                "skus": [row["sku"] for row in rows],
                "handle": rows[0].get("handle"),
            })
            continue
        row = rows[0]
        planned.append({
            "owner_id": owner_id,
            "handle": row.get("handle"),
            "title": row.get("title"),
            "skus": [item["sku"] for item in rows],
            "specificaties": row["fields"]["specificaties"]["value"],
            "specs_json": row["fields"]["specs_json"]["value"],
        })

    errors = []
    written_metafields = 0
    if args.apply:
        # Shopify accepts at most 25 metafields per metafieldsSet call.
        # Each product writes two metafields, so keep product batches <= 12.
        for batch in _chunks(planned, 12):
            metafields = []
            for item in batch:
                metafields.extend([
                    {
                        "ownerId": item["owner_id"],
                        "namespace": "pim",
                        "key": "specificaties",
                        "type": "multi_line_text_field",
                        "value": item["specificaties"],
                    },
                    {
                        "ownerId": item["owner_id"],
                        "namespace": "pim",
                        "key": "specs_json",
                        "type": "json",
                        "value": item["specs_json"],
                    },
                ])
            response = client.graphql(SET_MUTATION, {"metafields": metafields})
            user_errors = (response.get("metafieldsSet") or {}).get("userErrors") or []
            if user_errors:
                errors.append({
                    "handles": [item["handle"] for item in batch],
                    "userErrors": user_errors,
                })
            else:
                written_metafields += len(metafields)

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "apply": args.apply,
        "source_products": len(source),
        "shopify_skus": len(existing),
        "planned_products": len(planned),
        "planned_metafields": len(planned) * 2,
        "written_metafields": written_metafields,
        "conflicts": len(conflicts),
        "error_batches": len(errors),
        "skipped": dict(skipped),
        "conflict_samples": conflicts[:20],
        "samples": [
            {
                "handle": item["handle"],
                "title": item["title"],
                "skus": item["skus"],
                "specificaties": item["specificaties"].split("|")[:6],
            }
            for item in planned[:20]
        ],
        "errors": errors,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors or conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
