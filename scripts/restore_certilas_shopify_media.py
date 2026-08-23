"""Audit and restore Certilas Shopify media without rewriting product content."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, "/srv/ai-product-factory")

from app.shopify.client import ShopifyClient
from app.shopify.sync import (
    _load_family_variant_options,
    _shopify_products,
    _source_products,
)


AUDIT_DIR = Path("/srv/ai-product-factory/data/audit")


def _image_key(url: str) -> str:
    name = unquote(Path(urlparse(url).path).stem).casefold()
    return re.sub(r"[^a-z0-9]+", "", name)


def _same_image(source_url: str, live_url: str) -> bool:
    source = _image_key(source_url)
    live = _image_key(live_url)
    # Shopify may append a UUID when the same supplier filename was uploaded
    # previously. The original normalized filename remains the prefix.
    return bool(source and live and (source == live or live.startswith(source)))


def build_plan(client: ShopifyClient) -> list[dict[str, object]]:
    source = {row["sku"].upper(): row for row in _source_products("certilas")}
    family_options = _load_family_variant_options("certilas")
    live = _shopify_products(client, "Certilas")
    by_product: dict[str, dict[str, object]] = {}
    for sku, match in live.items():
        if sku not in source:
            continue
        product = match["product"]
        entry = by_product.setdefault(product["id"], {
            "product_id": product["id"],
            "title": product["title"],
            "current_media": (product.get("media") or {}).get("nodes") or [],
            "source_images": [],
            "empty_variants": [],
        })
        known_urls = {
            image["image_url"] for image in entry["source_images"]
        }
        for image in source[sku].get("images") or []:
            if image.get("image_url") and image["image_url"] not in known_urls:
                entry["source_images"].append(image)
                known_urls.add(image["image_url"])
        family = family_options.get(sku) or {}
        variant_media = (match["variant"].get("media") or {}).get("nodes") or []
        if family and not variant_media:
            entry["empty_variants"].append({
                "variantId": match["variant"]["id"], "sku": sku,
            })

    plan = []
    for entry in by_product.values():
        current_urls = [
            (item.get("image") or {}).get("url") or ""
            for item in entry["current_media"]
        ]
        missing = [
            image for image in entry["source_images"]
            if not any(
                _same_image(image["image_url"], live_url)
                for live_url in current_urls
            )
        ]
        if missing or entry["empty_variants"]:
            plan.append({**entry, "missing_images": missing})
    return plan


def run(*, apply: bool) -> dict[str, object]:
    client = ShopifyClient.from_settings()
    plan = build_plan(client)
    result: dict[str, object] = {
        "mode": "apply" if apply else "audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "products": len(plan),
        "missing_images": sum(len(row["missing_images"]) for row in plan),
        "empty_family_variants": sum(len(row["empty_variants"]) for row in plan),
        "plan": plan,
        "applied": [],
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = AUDIT_DIR / f"certilas-media-restore-{stamp}.json"
    if apply:
        for row in plan:
            created_ids: list[str] = []
            missing = row["missing_images"]
            if missing:
                payload = client.graphql(
                    """mutation($productId:ID!,$media:[CreateMediaInput!]!){
                      productCreateMedia(productId:$productId,media:$media){
                        media{id status}
                        mediaUserErrors{field message code}
                      }}""",
                    {
                        "productId": row["product_id"],
                        "media": [{
                            "originalSource": image["image_url"],
                            "alt": image.get("alt_text") or row["title"],
                            "mediaContentType": "IMAGE",
                        } for image in missing],
                    },
                ).get("productCreateMedia") or {}
                errors = payload.get("mediaUserErrors") or []
                if errors:
                    raise RuntimeError(json.dumps(errors, ensure_ascii=False))
                created_ids = [item["id"] for item in payload.get("media") or []]
            # Shopify media creation is asynchronous. Newly created media can
            # be PROCESSING for a short time and cannot yet be attached to a
            # variant. A subsequent idempotent run attaches it once READY.
            preferred = [
                item["id"] for item in row["current_media"]
                if item.get("id") and item.get("status") == "READY"
            ][:1]
            attached = 0
            if preferred and row["empty_variants"]:
                payload = client.graphql(
                    """mutation($productId:ID!,
                    $variantMedia:[ProductVariantAppendMediaInput!]!){
                      productVariantAppendMedia(
                        productId:$productId,variantMedia:$variantMedia){
                        userErrors{field message}
                      }}""",
                    {
                        "productId": row["product_id"],
                        "variantMedia": [{
                            "variantId": item["variantId"],
                            "mediaIds": preferred,
                        } for item in row["empty_variants"]],
                    },
                ).get("productVariantAppendMedia") or {}
                errors = payload.get("userErrors") or []
                if errors:
                    raise RuntimeError(json.dumps(errors, ensure_ascii=False))
                attached = len(row["empty_variants"])
            result["applied"].append({
                "product_id": row["product_id"],
                "images_created": len(created_ids),
                "variants_attached": attached,
            })
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    result["audit_path"] = str(path)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    output = run(apply=args.apply)
    print(json.dumps({
        key: value for key, value in output.items()
        if key not in {"plan"}
    }, ensure_ascii=False, indent=2))
