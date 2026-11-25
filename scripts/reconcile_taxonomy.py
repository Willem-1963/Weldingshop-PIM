#!/usr/bin/env python3
"""Read-only by default; apply needs independent digest-bound approval."""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.shopify.client import ShopifyClient
from app.suppliers.hub import KEY_PATH, REGISTRY_PATH
from app.shopify.taxonomy_reconciler import (
    load_manifest,
    reconcile,
    strict_readonly_decrypt,
    supplier_alias_map,
)

_QUERY = """query TaxonomyCatalog($cursor:String){products(first:100,after:$cursor,sortKey:ID){pageInfo{hasNextPage endCursor} nodes{id vendor tags variants(first:100){pageInfo{hasNextPage endCursor} nodes{sku}}}}}"""
_VARIANTS_QUERY = """query TaxonomyVariants($id:ID!,$cursor:String){node(id:$id){... on Product{variants(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{sku}}}}}"""
_AFFECTED_QUERY = """query TaxonomyAffected($ids:[ID!]!){nodes(ids:$ids){... on Product{id vendor tags variants(first:100){pageInfo{hasNextPage endCursor} nodes{sku}}}}}"""

_V2_PRODUCTS_QUERY = """query TaxonomyProductsV2($ids:[ID!]!){nodes(ids:$ids){... on Product{id vendor status updatedAt tags resourcePublications(first:100){pageInfo{hasNextPage} nodes{publication{id}}} variants(first:100){pageInfo{hasNextPage endCursor} nodes{id sku barcode}} metafields(first:100){pageInfo{hasNextPage} nodes{namespace key type value}} collections(first:100){pageInfo{hasNextPage} nodes{id ruleSet{appliedDisjunctively}}}}}}"""
_V2_VARIANTS_QUERY = """query TaxonomyVariantsV2($id:ID!,$cursor:String){node(id:$id){... on Product{variants(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{id sku barcode}}}}}"""
_PUBLICATIONS_QUERY = """query TaxonomyPublicationRegistry{publications(first:100){pageInfo{hasNextPage} nodes{id}}}"""
_COLLECTION_PRODUCTS_QUERY = """query TaxonomyCollectionMembers($id:ID!,$cursor:String){node(id:$id){... on Collection{id products(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{id}}}}}"""
_COLLECTION_ASSERT_QUERY = """query TaxonomyCollectionRegistry($ids:[ID!]!){nodes(ids:$ids){... on Collection{id handle updatedAt ruleSet{appliedDisjunctively rules{column relation condition}}}}}"""


def _supplier(vendor: str, aliases: dict[str, str]) -> str:
    return aliases.get((vendor or "").strip().casefold(), f"unowned:{vendor or 'unknown'}")


def _variant_nodes(client: ShopifyClient, product: dict[str, Any]) -> list[dict[str, Any]]:
    variants = product["variants"]
    nodes = list(variants["nodes"])
    cursor = variants["pageInfo"]["endCursor"]
    while variants["pageInfo"]["hasNextPage"]:
        data = client.graphql(_VARIANTS_QUERY, {"id": product["id"], "cursor": cursor})
        node = data.get("node") if isinstance(data, dict) else None
        if not isinstance(node, dict) or not isinstance(node.get("variants"), dict):
            raise RuntimeError(f"malformed variant pagination for {product['id']}")
        variants = node["variants"]
        nodes.extend(variants["nodes"])
        cursor = variants["pageInfo"]["endCursor"]
    return nodes


def _records(client: ShopifyClient, manifest: dict[str, Any], products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases = supplier_alias_map(manifest)
    result: list[dict[str, Any]] = []
    for product in products:
        if not isinstance(product, dict) or not product.get("id"):
            raise RuntimeError("malformed product readback")
        for variant in _variant_nodes(client, product):
            sku = (variant.get("sku") or "").strip()
            if sku:
                result.append({"id": product["id"], "sku": sku, "supplier": _supplier(product.get("vendor") or "", aliases), "tags": list(product.get("tags") or [])})
    return result



def _v2_variant_nodes(client: ShopifyClient, product: dict[str, Any]) -> list[dict[str, Any]]:
    variants = product["variants"]
    nodes = list(variants["nodes"])
    cursor = variants["pageInfo"]["endCursor"]
    while variants["pageInfo"]["hasNextPage"]:
        data = client.graphql(_V2_VARIANTS_QUERY, {"id": product["id"], "cursor": cursor})
        node = data.get("node") if isinstance(data, dict) else None
        if not isinstance(node, dict) or not isinstance(node.get("variants"), dict):
            raise RuntimeError(f"malformed v2 variant pagination for {product['id']}")
        variants = node["variants"]
        nodes.extend(variants["nodes"])
        cursor = variants["pageInfo"]["endCursor"]
    return nodes


def _v2_records(client: ShopifyClient, manifest: dict[str, Any], products: list[dict[str, Any]], live_publication_gids: set[str], controlled_memberships: dict[str, set[str]]) -> list[dict[str, Any]]:
    expected = {item["product_gid"]: item for item in manifest["products"]}
    result: list[dict[str, Any]] = []
    for product in products:
        if not isinstance(product, dict) or product.get("id") not in expected:
            raise RuntimeError("malformed or unrequested v2 product readback")
        for connection in ("resourcePublications", "metafields", "collections"):
            if not isinstance(product.get(connection), dict) or product[connection].get("pageInfo", {}).get("hasNextPage"):
                raise RuntimeError(f"incomplete {connection} readback for {product['id']}")
        controlled_keys = {(x["namespace"], x["key"]) for x in expected[product["id"]].get("expected_controlled_metafields", [])}
        manual = [node["id"] for node in product["collections"]["nodes"] if node.get("ruleSet") is None]
        manual = sorted(set(manual) | controlled_memberships.get(product["id"], set()))
        result.append({
            "product_gid": product["id"], "vendor": product.get("vendor") or "", "status": product.get("status"),
            "publication_gids": [node["publication"]["id"] for node in product["resourcePublications"]["nodes"] if node["publication"]["id"] in live_publication_gids],
            "variants": [{"variant_gid": node["id"], "sku": node.get("sku") or "", "barcode": node.get("barcode") or ""} for node in _v2_variant_nodes(client, product)],
            "tags": list(product.get("tags") or []),
            "controlled_metafields": [{"namespace": node["namespace"], "key": node["key"], "type": node["type"], "value": node["value"]} for node in product["metafields"]["nodes"] if (node["namespace"], node["key"]) in controlled_keys],
            "manual_collection_gids": manual, "updated_at": product.get("updatedAt") or "",
        })
    return result


def _assert_collection_registry(client: ShopifyClient, manifest: dict[str, Any]) -> None:
    entries = manifest.get("collection_registry", [])
    ids = [x["collection_gid"] for x in entries]
    if not ids:
        return
    data = client.graphql(_COLLECTION_ASSERT_QUERY, {"ids": ids})
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list) or len(nodes) != len(ids) or any(node is None for node in nodes):
        raise RuntimeError("collection registry CAS readback is incomplete")
    by_id = {node["id"]: node for node in nodes}
    for entry in entries:
        node = by_id.get(entry["collection_gid"])
        if not node or node.get("handle") != entry["handle"] or node.get("updatedAt") != entry["updated_at"]:
            raise RuntimeError(f"collection registry CAS drift for {entry['handle']}")
        ruleset = node.get("ruleSet")
        if entry["rule_mode"] == "manual" and ruleset is not None:
            raise RuntimeError(f"collection rule mode drift for {entry['handle']}")
        if entry["rule_mode"] == "automatic":
            expected = [{"column":"TAG", "relation":"EQUALS", "condition":entry["controlled_tag"]}]
            if not isinstance(ruleset, dict) or ruleset.get("appliedDisjunctively") is not False or ruleset.get("rules") != expected:
                raise RuntimeError(f"collection rule drift for {entry['handle']}")


def _controlled_manual_memberships(client: ShopifyClient, manifest: dict[str, Any], product_ids: set[str]) -> dict[str, set[str]]:
    collection_gids = {x["collection_gid"] for x in manifest.get("collection_registry", []) if x.get("rule_mode") == "manual"}
    collection_gids |= {x["collection_gid"] for x in manifest.get("manual_collection_tombstones", [])}
    result = {gid: set() for gid in product_ids}
    for collection_gid in sorted(collection_gids):
        cursor = None
        while True:
            data = client.graphql(_COLLECTION_PRODUCTS_QUERY, {"id": collection_gid, "cursor": cursor})
            node = data.get("node") if isinstance(data, dict) else None
            products = node.get("products") if isinstance(node, dict) else None
            if not isinstance(products, dict) or node.get("id") != collection_gid:
                raise RuntimeError(f"manual collection readback is incomplete for {collection_gid}")
            for product in products.get("nodes", []):
                if product.get("id") in result:
                    result[product["id"]].add(collection_gid)
            if not products.get("pageInfo", {}).get("hasNextPage"):
                break
            cursor = products["pageInfo"].get("endCursor")
            if not cursor:
                raise RuntimeError(f"manual collection pagination cursor missing for {collection_gid}")
    return result


def read_v2_catalog(client: ShopifyClient, manifest: dict[str, Any], product_ids: list[str] | None = None) -> list[dict[str, Any]]:
    ids = list(product_ids or [item["product_gid"] for item in manifest["products"]])
    _assert_collection_registry(client, manifest)
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate v2 product GID request")
    publication_data = client.graphql(_PUBLICATIONS_QUERY, {})
    publication_page = publication_data.get("publications") if isinstance(publication_data, dict) else None
    if not isinstance(publication_page, dict) or publication_page.get("pageInfo", {}).get("hasNextPage"):
        raise RuntimeError("current publication registry readback is incomplete")
    live_publication_gids = {node["id"] for node in publication_page.get("nodes", [])}
    if not live_publication_gids:
        raise RuntimeError("current publication registry is empty")
    products: list[dict[str, Any]] = []
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]
        data = client.graphql(_V2_PRODUCTS_QUERY, {"ids": chunk})
        nodes = data.get("nodes") if isinstance(data, dict) else None
        if not isinstance(nodes, list) or len(nodes) != len(chunk) or any(node is None for node in nodes):
            raise RuntimeError("v2 product readback is incomplete")
        products.extend(nodes)
    controlled_memberships = _controlled_manual_memberships(client, manifest, set(ids))
    return _v2_records(client, manifest, products, live_publication_gids, controlled_memberships)

def read_full_catalog(client: ShopifyClient, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    cursor = None
    while True:
        data = client.graphql(_QUERY, {"cursor": cursor})
        page = data.get("products") if isinstance(data, dict) else None
        if not isinstance(page, dict):
            raise RuntimeError("malformed full-catalog response")
        products.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return _records(client, manifest, products)
        cursor = page["pageInfo"]["endCursor"]


def read_affected_catalog(client: ShopifyClient, manifest: dict[str, Any], product_ids: list[str]) -> list[dict[str, Any]]:
    ids = sorted(set(product_ids))
    if not ids:
        return []
    data = client.graphql(_AFFECTED_QUERY, {"ids": ids})
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list) or len(nodes) != len(ids) or any(node is None for node in nodes):
        raise RuntimeError("affected-product readback is incomplete")
    return _records(client, manifest, nodes)


def _readonly_client(registry_path: Path = REGISTRY_PATH, key_path: Path = KEY_PATH) -> ShopifyClient:
    if not registry_path.parent.is_dir() or not registry_path.is_file():
        raise RuntimeError("existing read-only Shopify registry is required")
    uri = f"file:{registry_path.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        row = connection.execute("SELECT shop_domain,access_token_encrypted,api_version FROM shopify_settings WHERE id=1").fetchone()
    finally:
        connection.close()
    if not row or not row[0] or not row[1] or not row[2]:
        raise RuntimeError("complete existing Shopify settings are required")
    token = strict_readonly_decrypt(row[1], key_path)
    return ShopifyClient(row[0], token, row[2])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-manifest-sha256")
    parser.add_argument("--approved-plan-sha256")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    if args.apply and (not args.approved_manifest_sha256 or not args.approved_plan_sha256 or not args.confirm):
        parser.error("--apply requires approved manifest/plan sha256 and digest-bound --confirm")
    if not args.apply and (args.approved_manifest_sha256 or args.approved_plan_sha256 or args.confirm):
        parser.error("approval arguments are only valid together with --apply")
    manifest = load_manifest(args.manifest)
    client = _readonly_client()
    catalog = read_v2_catalog(client, manifest) if manifest["schema_version"] == 2 else read_full_catalog(client, manifest)
    initial = reconcile(manifest, catalog)
    affected_ids = sorted({op.get("product_gid", op.get("id")) for op in initial["operations"]})
    result = reconcile(
        manifest,
        catalog,
        client=client if args.apply else None,
        apply=args.apply,
        approved_manifest_sha256=args.approved_manifest_sha256,
        approved_plan_sha256=args.approved_plan_sha256,
        confirm=args.confirm,
        catalog_reader=((lambda: read_v2_catalog(client, manifest, affected_ids)) if manifest["schema_version"] == 2 else (lambda: read_affected_catalog(client, manifest, affected_ids))) if args.apply else None,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 2 if result["errors"] or result["unexpected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
