"""Identify internal Shopify inventory components from three strong signals."""
from __future__ import annotations

from typing import Any


def internal_component_signals(product: dict[str, Any], sku: str = "") -> dict[str, bool]:
    variants = ((product.get("variants") or {}).get("nodes") or [])
    skus = [str(sku or ""), *(str(item.get("sku") or "") for item in variants)]
    vendor = str(product.get("vendor") or "").strip().casefold()
    product_type = str(product.get("productType") or product.get("product_type") or "").strip().casefold()
    return {
        "sku_prefix": any(value.strip().upper().startswith("STOCK-") for value in skus),
        "vendor": vendor == "openclaw stock component".casefold(),
        "product_type": product_type == "internal stock component".casefold(),
    }


def is_internal_component(product: dict[str, Any], sku: str = "") -> bool:
    return all(internal_component_signals(product, sku).values())


def scan_internal_components(client: Any) -> dict[str, list[dict[str, Any]]]:
    """Read all component-vendor products and separate proven from mismatched rows."""
    query = """
    query($after:String){
      products(first:50,after:$after,query:"vendor:\\"OpenClaw Stock Component\\""){
        pageInfo{hasNextPage endCursor}
        nodes{
          id title handle status vendor productType
          variants(first:20){nodes{id sku}}
          resourcePublicationsV2(first:50){
            nodes{isPublished publication{id name}}
          }
        }
      }
    }
    """
    after = None
    protected: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    while True:
        connection = client.graphql(query, {"after": after})["products"]
        for product in connection["nodes"]:
            product = dict(product)
            product["signals"] = internal_component_signals(product)
            (protected if is_internal_component(product) else mismatched).append(product)
        if not connection["pageInfo"]["hasNextPage"]:
            break
        after = connection["pageInfo"]["endCursor"]
    return {"protected": protected, "mismatched": mismatched}


def publication_cleanup_plan(scan: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    rows = []
    protected_skus = []
    for product in scan["protected"]:
        protected_skus.extend(
            str(item.get("sku") or "")
            for item in ((product.get("variants") or {}).get("nodes") or [])
            if str(item.get("sku") or "")
        )
        publications = [
            {"publicationId": item["publication"]["id"]}
            for item in ((product.get("resourcePublicationsV2") or {}).get("nodes") or [])
            if item.get("isPublished") and (item.get("publication") or {}).get("id")
        ]
        if publications:
            rows.append({"id": product["id"], "input": publications})
    return {
        "rows": rows,
        "protected_stock_skus": sorted(set(protected_skus)),
        "mismatched": scan["mismatched"],
    }
