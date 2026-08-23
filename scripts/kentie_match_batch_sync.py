from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/srv/ai-product-factory")

from app.shopify.client import ShopifyClient
from app.shopify.client import get_shopify_settings
from app.shopify.sync import (
    _common_product_content_errors, _complete_for_new_product, _input,
    _mapped_any, _selected_inventory_location, _source_products,
)
from app.suppliers.hub import get_supplier
from app.suppliers.on_demand_import import import_official_website_product


PROJECT = Path("/srv/ai-product-factory")
DB = PROJECT / "data/database/suppliers/kentie.sqlite"
SNAPSHOT = Path("/root/kentie-before-match-222-20260818.sqlite")
STATE = PROJECT / "data/jobs/kentie-match-222.json"


def manifest() -> list[str]:
    if STATE.exists():
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return list(data["manifest"])
    with sqlite3.connect(SNAPSHOT) as connection:
        skus = [row[0] for row in connection.execute(
            """SELECT sku FROM products WHERE source_present=1
               AND lower(coalesce(source_description,'')) LIKE '%match%'
               ORDER BY sku"""
        )]
    if len(skus) != 222:
        raise RuntimeError(f"Veiligheidsstop: verwacht 222 SKU's, gevonden {len(skus)}")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": skus, "completed_batches": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return skus


def prepared_sources(skus: list[str], live: dict, supplier: dict) -> list[dict]:
    by_sku = {row["sku"].upper(): row for row in _source_products("kentie")}
    prepared = []
    for sku in skus:
        product = by_sku[sku.upper()]
        product.update({
            "_raw_data": json.loads(product.get("raw_data_json") or "{}"),
            "_supplier_slug": "kentie",
            "_shopify_field_mapping": supplier.get("shopify_field_mapping") or {},
            "_shopify_metafield_mapping": supplier.get("shopify_metafield_mapping") or {},
            "inventory_policy": "continue",
            "_has_shopify_image": bool(
                (((live.get(sku.upper()) or {}).get("product") or {}).get("media") or {}).get("nodes")
            ),
        })
        errors = _common_product_content_errors(product)
        if errors:
            raise RuntimeError(f"{sku} niet Shopify-waardig: {'; '.join(errors)}")
        prepared.append(product)
    return prepared


def graphql_retry(client: ShopifyClient, query: str, variables: dict) -> dict:
    for attempt in range(1, 6):
        try:
            return client.graphql(query, variables)
        except RuntimeError as exc:
            if "Throttled" not in str(exc) or attempt == 5:
                raise
            time.sleep(attempt * 3)
    raise RuntimeError("Shopify-query kon niet worden uitgevoerd")


def targeted_live(client: ShopifyClient, skus: list[str]) -> dict[str, dict]:
    product_query = """query($query:String!){products(first:30,query:$query){nodes{
      id title handle status descriptionHtml tags
      metafields(first:50,namespace:"custom"){nodes{key value}}
      media(first:100){nodes{id status mediaErrors{code details message}
        ... on MediaImage{image{url width height}}}}
      variants(first:100){nodes{id sku price inventoryItem{id}
        media(first:1){nodes{id ... on MediaImage{image{url}}}}}}
    }}}"""
    search = " OR ".join(f"sku:{sku}" for sku in skus)
    data = graphql_retry(client, product_query, {"query": search})["products"]["nodes"]
    result = {}
    wanted = {value.upper() for value in skus}

    def collect(products: list[dict]) -> None:
      for product in products:
        for variant in product["variants"]["nodes"]:
            sku = str(variant.get("sku") or "").strip().upper()
            if sku in wanted:
                result[sku] = {"product": product, "variant": variant}
    collect(data)
    # Shopify's gecombineerde zoekparser laat incidenteel geldige numerieke
    # SKU's weg. Controleer alleen de ontbrekende waarden nogmaals exact,
    # voordat een nieuw product wordt aangemaakt.
    for sku in sorted(wanted - set(result)):
        exact = graphql_retry(
            client, product_query, {"query": f'sku:"{sku}"'}
        )["products"]["nodes"]
        collect(exact)
    return result


def run(batch_index: int, enrich: bool, apply: bool) -> dict:
    all_skus = manifest()
    start = (batch_index - 1) * 10
    skus = all_skus[start:start + 10]
    if not skus:
        raise ValueError("Batchnummer valt buiten het manifest")
    if enrich:
        for sku in skus:
            result = import_official_website_product(
                "kentie", sku, execution_context="bulk_enrichment"
            )
            if result.get("enrichment_skipped"):
                raise RuntimeError(f"{sku}: geen exacte officiële productpagina")
    client = ShopifyClient.from_settings()
    supplier = get_supplier("kentie") or {}
    live = targeted_live(client, skus)
    products = prepared_sources(skus, live, supplier)
    missing = [sku for sku in skus if sku.upper() not in live]
    needs_activation = [
        sku for sku in skus
        if sku in missing
        or str(live[sku.upper()]["product"].get("status") or "").upper() != "ACTIVE"
    ]
    for product in products:
        if product["sku"] in missing and not _complete_for_new_product(product):
            raise RuntimeError(f"{product['sku']}: basisgegevens voor nieuw product ontbreken")
    updated = []
    if apply:
        for product in products:
            sku = product["sku"].upper()
            match = live.get(sku)
            product_input = _input(product, match)
            product_input["status"] = (
                str(match["product"].get("status") or "ACTIVE").upper()
                if match else "DRAFT"
            )
            payload = graphql_retry(client,
                """mutation($input:ProductSetInput!){productSet(synchronous:true,input:$input){
                product{id status} userErrors{field message code}}}""",
                {"input": product_input},
            )["productSet"]
            if payload.get("userErrors"):
                raise RuntimeError(f"{sku}: {json.dumps(payload['userErrors'], ensure_ascii=False)}")
            updated.append({"sku": sku, "id": payload["product"]["id"]})
        after = targeted_live(client, skus)
        for _ in range(6):
            if all(sku.upper() in after for sku in skus):
                break
            time.sleep(5)
            after = targeted_live(client, skus)
        if any(sku.upper() not in after for sku in skus):
            raise RuntimeError("Niet alle SKU's zijn na productSet in Shopify gevonden")
        if needs_activation:
            location_id = _selected_inventory_location(supplier, get_shopify_settings())
            if not location_id:
                raise RuntimeError("Shopify-voorraadlocatie ontbreekt")
            for product in products:
                sku = product["sku"].upper()
                if sku not in {value.upper() for value in needs_activation}:
                    continue
                inventory_item_id = after[sku]["variant"]["inventoryItem"]["id"]
                activation = graphql_retry(client, """mutation(
                  $inventoryItemId:ID!,$updates:[InventoryBulkToggleActivationInput!]!){
                  inventoryBulkToggleActivation(inventoryItemId:$inventoryItemId,
                    inventoryItemUpdates:$updates){userErrors{field message code}}}""", {
                    "inventoryItemId": inventory_item_id,
                    "updates": [{"locationId": location_id, "activate": True}],
                })["inventoryBulkToggleActivation"]
                if activation.get("userErrors"):
                    raise RuntimeError(f"{sku}: voorraadlocatie {activation['userErrors']}")
                quantity = int(
                    _mapped_any(product, "variant.inventoryQuantities", "inventory.quantity")
                    or product.get("stock_quantity") or 0
                )
                inventory = graphql_retry(client, """mutation(
                  $input:InventorySetQuantitiesInput!,$key:String!){
                  inventorySetQuantities(input:$input) @idempotent(key:$key){
                  userErrors{field message code}}}""", {
                    "key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"kentie-match:{sku}")),
                    "input": {"name": "available", "reason": "correction",
                    "referenceDocumentUri": f"pim://weldingshop/kentie/match-repair/{sku}",
                    "quantities": [{"inventoryItemId": inventory_item_id,
                    "locationId": location_id, "quantity": quantity,
                    "changeFromQuantity": None}]},
                })["inventorySetQuantities"]
                if inventory.get("userErrors"):
                    raise RuntimeError(f"{sku}: voorraad {inventory['userErrors']}")
            new_ids = sorted({
                after[sku.upper()]["product"]["id"] for sku in needs_activation
            })
            publications = graphql_retry(
                client, "query{publications(first:100){nodes{id}}}", {}
            )["publications"]["nodes"]
            publication_input = [{"publicationId": row["id"]} for row in publications]
            for product_id in new_ids:
                active = graphql_retry(client, """mutation($input:ProductSetInput!){
                  productSet(synchronous:true,input:$input){product{id status}
                  userErrors{field message code}}}""",
                  {"input": {"id": product_id, "status": "ACTIVE"}})["productSet"]
                if active.get("userErrors"):
                    raise RuntimeError(str(active["userErrors"]))
                published = graphql_retry(client, """mutation($id:ID!,$input:[PublicationInput!]!){
                  publishablePublish(id:$id,input:$input){userErrors{field message}}}""",
                  {"id": product_id, "input": publication_input})["publishablePublish"]
                if published.get("userErrors"):
                    raise RuntimeError(str(published["userErrors"]))
            after = targeted_live(client, skus)
        failures = []
        for product in products:
            sku = product["sku"].upper()
            match = after.get(sku)
            if not match:
                failures.append(f"{sku}: ontbreekt")
                continue
            live_product = match["product"]
            if live_product.get("status") != "ACTIVE":
                failures.append(f"{sku}: status {live_product.get('status')}")
            if len(str(live_product.get("descriptionHtml") or "")) < 80:
                failures.append(f"{sku}: tekst ontbreekt")
            if len(live_product.get("tags") or []) < 2:
                failures.append(f"{sku}: tags ontbreken")
            if not ((live_product.get("media") or {}).get("nodes") or []):
                failures.append(f"{sku}: afbeelding ontbreekt")
        if failures:
            raise RuntimeError("Shopify-eindcontrole mislukt: " + "; ".join(failures))
        state = json.loads(STATE.read_text(encoding="utf-8"))
        completed = set(state.get("completed_batches") or [])
        completed.add(batch_index)
        state.update({
            "completed_batches": sorted(completed),
            "last_batch": batch_index, "last_skus": skus,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "batch": batch_index, "skus": skus, "validated": len(products),
        "updated": len(updated), "missing_in_shopify": missing,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--enrich", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.batch, args.enrich, args.apply), ensure_ascii=False, indent=2))
