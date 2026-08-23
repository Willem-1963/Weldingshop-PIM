"""Sync only repaired Certilas descriptions to existing Shopify products."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/srv/ai-product-factory")

from app.shopify.client import ShopifyClient
from app.shopify.sync import _load_family_variant_options, _shopify_products


AUDIT_DIR = Path("/srv/ai-product-factory/data/audit")


def run(*, apply: bool, limit: int = 0) -> dict[str, object]:
    client = ShopifyClient.from_settings()
    families = _load_family_variant_options("certilas")
    live = _shopify_products(client, "Certilas")
    plan: dict[str, dict[str, object]] = {}
    for sku, family in families.items():
        desired = str(family.get("family_description") or "")
        if "certilas-laspositie-" not in desired:
            continue
        match = live.get(sku)
        if not match:
            continue
        product = match["product"]
        current = str(product.get("descriptionHtml") or "")
        expected_icons = set(re.findall(r"certilas-laspositie-[a-z]+\.png", desired))
        if expected_icons and all(icon in current for icon in expected_icons):
            continue
        entry = plan.setdefault(product["id"], {
            "product_id": product["id"],
            "title": product["title"],
            "handle": product["handle"],
            "description_html": desired,
            "skus": [],
        })
        if entry["description_html"] != desired:
            raise RuntimeError(f"Conflicterende familiebeschrijvingen voor {product['id']}")
        entry["skus"].append(sku)
    if limit:
        plan = dict(list(plan.items())[:limit])
    applied = []
    if apply:
        for row in plan.values():
            payload = client.graphql(
                """mutation($input:ProductInput!){productUpdate(input:$input){
                product{id updatedAt} userErrors{field message}}}""",
                {"input": {
                    "id": row["product_id"],
                    "descriptionHtml": row["description_html"],
                }},
            )["productUpdate"]
            if payload.get("userErrors"):
                raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
            applied.append(row["product_id"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = AUDIT_DIR / f"certilas-description-repair-{stamp}.json"
    output = {
        "mode": "apply" if apply else "audit",
        "products": len(plan),
        "applied": len(applied),
        "plan": list(plan.values()),
    }
    audit_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**output, "audit_path": str(audit_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    result = run(apply=args.apply, limit=args.limit)
    print(json.dumps({k: v for k, v in result.items() if k != "plan"}, ensure_ascii=False, indent=2))
