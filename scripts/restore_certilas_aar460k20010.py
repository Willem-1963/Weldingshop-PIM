#!/usr/bin/env python3
"""Restore AAR460K20010 as its own canonical draft product."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.shopify.client import ShopifyClient
from app.shopify.sync import _input, _source_products


SKU = "AAR460K20010"


def main() -> None:
    client = ShopifyClient.from_settings()
    source = next(item for item in _source_products("certilas") if item["sku"] == SKU)
    reference = client.graphql(
        """
        query{products(first:1,query:"sku:AAR460K30012"){
          nodes{descriptionHtml media(first:1){nodes{... on MediaImage{image{url}}}}}
        }}
        """
    )["products"]["nodes"][0]
    description = re.sub(
        r"<h2>.*?</h2>",
        "<h2>Certilas gevulde draad AA R460 – 1,0 mm – D-200</h2>",
        reference.get("descriptionHtml") or "",
        count=1,
        flags=re.DOTALL,
    )
    source.update({
        "ai_title": "Certilas gevulde draad AA R460 – 1,0 mm – D-200",
        "html_description": description,
        "inventory_policy": "continue",
        "_raw_data": json.loads(source.get("raw_data_json") or "{}"),
        "_shopify_field_mapping": {},
        "_shopify_metafield_mapping": {},
        "_out_of_stock_disclaimer": True,
    })
    if not source.get("images"):
        media = reference.get("media", {}).get("nodes") or []
        image_url = ((media[0].get("image") or {}).get("url") if media else "")
        if image_url:
            source["images"] = [{
                "image_url": image_url,
                "position": 1,
                "alt_text": source["ai_title"],
            }]
    product_input = _input(source, None)
    product_input.update({
        "title": source["ai_title"],
        "status": "DRAFT",
        "productOptions": [
            {"name": "Diameter", "position": 1, "values": [{"name": "1,0 mm"}]},
            {"name": "Verpakking", "position": 2, "values": [{"name": "D-200 · 20 kg"}]},
        ],
    })
    product_input["variants"][0]["optionValues"] = [
        {"optionName": "Diameter", "name": "1,0 mm"},
        {"optionName": "Verpakking", "name": "D-200 · 20 kg"},
    ]
    payload = client.graphql(
        """
        mutation($input:ProductSetInput!){productSet(synchronous:true,input:$input){
          product{id title status variants(first:10){nodes{sku}}}
          userErrors{field message code}
        }}
        """,
        {"input": product_input},
    )["productSet"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
    audit = {
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "product": payload["product"],
    }
    path = Path("data/audit") / "certilas-aar460k20010-restored.json"
    path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
