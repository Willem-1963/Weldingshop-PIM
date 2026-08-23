#!/usr/bin/env python3
"""Repair the 2026-08-03 Certilas Shopify family synchronization.

Dry-run is the default. Pass --execute to apply the reviewed plan.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.shopify.client import ShopifyClient
from app.shopify.sync import (
    _apply_canonical_family_content,
    _grouped_product_rows,
    _input,
    _load_family_variant_options,
    _source_products,
)


SLUG = "certilas"
BROKEN_MIXED_PRODUCT_SKU = "10053"
AUDIT_DIR = Path("data/audit")


def _shopify_products(client: ShopifyClient) -> list[dict]:
    query = """
    query($after:String){
      products(first:100,after:$after,query:"vendor:Certilas"){
        pageInfo{hasNextPage endCursor}
        nodes{
          id title handle status descriptionHtml
          media(first:100){nodes{id ... on MediaImage{image{url}}}}
          metafields(first:50,namespace:"custom"){nodes{key value type namespace}}
          variants(first:100){nodes{
            id sku price barcode inventoryPolicy
            inventoryItem{id unitCost{amount}}
            media(first:1){nodes{id ... on MediaImage{image{url}}}}
          }}
        }
      }
    }
    """
    after = None
    products: list[dict] = []
    while True:
        connection = client.graphql(query, {"after": after})["products"]
        products.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            return products
        after = connection["pageInfo"]["endCursor"]


def _existing_by_sku(products: list[dict]) -> dict[str, dict]:
    result = {}
    for product in products:
        for variant in product["variants"]["nodes"]:
            sku = str(variant.get("sku") or "").strip().upper()
            if sku:
                result[sku] = {
                    "product": product,
                    "variant": variant,
                }
    return result


def _number_title(product: dict) -> bool:
    title = str(product.get("title") or "").strip().upper()
    return any(
        title == str(variant.get("sku") or "").strip().upper()
        for variant in product["variants"]["nodes"]
    )


def _canonical_title(product: dict, families: dict[str, dict], sources: dict[str, dict]) -> str:
    titles = {
        families.get(str(variant.get("sku") or "").upper(), {}).get("family_title")
        for variant in product["variants"]["nodes"]
    } - {None, ""}
    if len(titles) == 1:
        return next(iter(titles))
    if len(titles) > 1:
        return ""
    first_sku = str(product["variants"]["nodes"][0].get("sku") or "").upper()
    source = sources.get(first_sku) or {}
    description = str(source.get("source_description") or "").strip()
    return f"Certilas {description}".strip()


def _product_update_title(client: ShopifyClient, product_id: str, title: str) -> None:
    payload = client.graphql(
        """
        mutation($input:ProductInput!){productUpdate(input:$input){
          product{id title} userErrors{field message}
        }}
        """,
        {"input": {"id": product_id, "title": title[:255]}},
    )["productUpdate"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))


def _product_set(client: ShopifyClient, product_input: dict) -> dict:
    payload = client.graphql(
        """
        mutation($input:ProductSetInput!){productSet(synchronous:true,input:$input){
          product{id title handle variants(first:100){nodes{id sku}}}
          userErrors{field message code}
        }}
        """,
        {"input": product_input},
    )["productSet"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
    return payload["product"]


def _product_delete(client: ShopifyClient, product_id: str) -> None:
    payload = client.graphql(
        """
        mutation($input:ProductDeleteInput!){productDelete(input:$input){
          deletedProductId userErrors{field message}
        }}
        """,
        {"input": {"id": product_id}},
    )["productDelete"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))


def _prepare_source(source: dict, family: dict) -> dict:
    source = dict(source)
    source["_raw_data"] = json.loads(source.get("raw_data_json") or "{}")
    source["_shopify_field_mapping"] = {}
    source["_shopify_metafield_mapping"] = {}
    source["inventory_policy"] = "continue"
    source["_out_of_stock_disclaimer"] = True
    source["_family_variant_options"] = dict(family)
    _apply_canonical_family_content(source, family)
    return source


def _synthetic_10325_family(source: dict, template: dict) -> dict:
    description = str(template.get("family_description") or "")
    description = re.sub(
        r"<h2>.*?</h2>",
        "<h2>Certilas SMAW laselektrode E 6013 T – 350 mm – 2,5 mm</h2>",
        description,
        count=1,
        flags=re.DOTALL,
    )
    return {
        "family_key": "certilas|smaw|e-6013-t|staaf|350-mm",
        "family_title": "Certilas SMAW laselektrode E 6013 T – 350 mm – 2,5 mm",
        "family_description": description,
        "family_image": str(template.get("family_image") or ""),
        "diameter": "2,5 mm",
        "packaging": "Doos 2 kg",
        "packaging_code": "Doos",
        "packaging_name": "doos",
        "package_weight_label": "2 kg",
    }


def _split_mixed_product(
    client: ShopifyClient,
    sources: dict[str, dict],
    families: dict[str, dict],
    existing: dict[str, dict],
) -> list[dict]:
    groups = [
        ["10053", "10054", "10055"],
        ["10320"],
        ["10325"],
        ["10332"],
    ]
    template = families["10320"]
    results = []
    for index, skus in enumerate(groups):
        prepared = []
        for sku in skus:
            family = (
                _synthetic_10325_family(sources[sku], template)
                if sku == "10325" else families[sku]
            )
            prepared.append(_prepare_source(sources[sku], family))
        target_existing = (
            {sku: existing[sku] for sku in skus if sku in existing}
            if index == 0 else {}
        )
        row = _grouped_product_rows(
            prepared, target_existing, group_variants=True
        )[0]["input"]
        if index == 0:
            row["id"] = existing[BROKEN_MIXED_PRODUCT_SKU]["product"]["id"]
            row["handle"] = existing[BROKEN_MIXED_PRODUCT_SKU]["product"]["handle"]
        results.append(_product_set(client, row))
    return results


def _merge_er100_family(
    client: ShopifyClient,
    sources: dict[str, dict],
    families: dict[str, dict],
    existing: dict[str, dict],
) -> dict:
    target_sku, merged_sku = "52505D200", "51514"
    prepared = [
        _prepare_source(sources[sku], families[sku])
        for sku in (target_sku, merged_sku)
    ]
    target_handle = existing[target_sku]["product"]["handle"]
    for product in prepared:
        product["shopify_handle"] = target_handle
    target_existing = {target_sku: existing[target_sku]}
    row = _grouped_product_rows(
        prepared, target_existing, group_variants=True
    )[0]["input"]
    row["id"] = existing[target_sku]["product"]["id"]
    row["handle"] = target_handle
    product = _product_set(client, row)
    _product_delete(client, existing[merged_sku]["product"]["id"])
    return product


def build_plan(client: ShopifyClient) -> tuple[dict, dict, dict, dict]:
    shopify = _shopify_products(client)
    existing = _existing_by_sku(shopify)
    sources = {row["sku"].upper(): row for row in _source_products(SLUG)}
    families = _load_family_variant_options(SLUG)
    title_repairs = []
    for product in shopify:
        if not _number_title(product):
            continue
        skus = [str(v.get("sku") or "").upper() for v in product["variants"]["nodes"]]
        title_repairs.append({
            "product_id": product["id"],
            "current_title": product["title"],
            "canonical_title": _canonical_title(product, families, sources),
            "skus": skus,
            "family_keys": sorted({
                families.get(sku, {}).get("family_key") for sku in skus
                if families.get(sku, {}).get("family_key")
            }),
        })
    plan = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "title_repairs": title_repairs,
        "mixed_product": next(
            item for item in title_repairs
            if BROKEN_MIXED_PRODUCT_SKU in item["skus"]
        ),
        "split_family_merge": {
            "family_key": families["51514"]["family_key"],
            "target_sku": "52505D200",
            "merged_sku": "51514",
            "delete_product_id_after_merge": existing["51514"]["product"]["id"],
        },
    }
    return plan, sources, families, existing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    client = ShopifyClient.from_settings()
    plan, sources, families, existing = build_plan(client)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = AUDIT_DIR / f"certilas-family-rebuild-{stamp}.json"
    result = {"mode": "execute" if args.execute else "dry-run", "plan": plan}
    if args.execute:
        repaired = []
        for item in plan["title_repairs"]:
            if BROKEN_MIXED_PRODUCT_SKU in item["skus"]:
                continue
            if not item["canonical_title"]:
                raise ValueError(f"Geen eenduidige titel voor {item['skus']}")
            _product_update_title(
                client, item["product_id"], item["canonical_title"]
            )
            repaired.append(item)
        split = _split_mixed_product(client, sources, families, existing)
        merged = _merge_er100_family(client, sources, families, existing)
        result.update({
            "title_repairs_applied": repaired,
            "split_products": split,
            "merged_product": merged,
        })
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"audit_path": str(path), **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
