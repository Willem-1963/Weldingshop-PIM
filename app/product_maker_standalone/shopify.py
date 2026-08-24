from __future__ import annotations

import json
import mimetypes
import os
import re
from pathlib import Path
import unicodedata
import uuid
from typing import Any

import requests

from app.shopify.client import ShopifyClient, get_metafield_definitions, get_shopify_locations
from app.ai.providers.openai_provider import OpenAIProvider

from .service import ProductMakerService, utc_now


def _shopify_image_source(client: ShopifyClient, value: str) -> str:
    """Upload a local productmaker image to Shopify's staged storage."""
    path = Path(value)
    if not path.is_absolute():
        return value
    if not path.is_file():
        raise ValueError(f"Geüploade foto niet gevonden: {path.name}")
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = client.graphql(
        """mutation($input:[StagedUploadInput!]!){stagedUploadsCreate(input:$input){
        stagedTargets{url resourceUrl parameters{name value}}
        userErrors{field message}}}""",
        {"input": [{"resource": "IMAGE", "filename": path.name,
                    "mimeType": mime_type, "httpMethod": "POST"}]},
    ).get("stagedUploadsCreate") or {}
    errors = payload.get("userErrors") or []
    if errors:
        raise RuntimeError("Foto-upload naar Shopify mislukt: " + str(errors))
    target = (payload.get("stagedTargets") or [])[0]
    fields = {item["name"]: item["value"] for item in target.get("parameters") or []}
    response = requests.post(
        target["url"], data=fields,
        files={"file": (path.name, path.read_bytes(), mime_type)}, timeout=180,
    )
    response.raise_for_status()
    return str(target["resourceUrl"])


def product_metafield_definitions() -> list[dict[str, Any]]:
    return get_metafield_definitions("PRODUCT")


def shopify_locations() -> list[dict[str, Any]]:
    return get_shopify_locations()


def _publish_to_all_channels(client: ShopifyClient, product_id: str) -> int:
    publications = client.graphql(
        "query{publications(first:100){nodes{id name}}}"
    ).get("publications", {}).get("nodes") or []
    publication_input = [
        {"publicationId": item["id"]} for item in publications if item.get("id")
    ]
    if not publication_input:
        raise RuntimeError("Shopify heeft geen beschikbare verkoopkanalen teruggegeven")
    payload = client.graphql(
        """mutation($id:ID!,$input:[PublicationInput!]!){
        publishablePublish(id:$id,input:$input){userErrors{field message}}
        }""",
        {"id": product_id, "input": publication_input},
    ).get("publishablePublish") or {}
    errors = payload.get("userErrors") or []
    if errors:
        raise RuntimeError(
            "Publiceren naar verkoopkanalen mislukt: "
            + "; ".join(str(item.get("message") or item) for item in errors)
        )
    return len(publication_input)


def suggest_categories(query: str) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    if not query:
        return []
    client = ShopifyClient.from_settings()
    def search(term: str) -> list[dict[str, Any]]:
        data = client.graphql(
            """query($search:String!){taxonomy{categories(first:25,search:$search){
            nodes{id name fullName isLeaf}}}}""", {"search": term},
        )
        return ((data.get("taxonomy") or {}).get("categories") or {}).get("nodes") or []
    direct = search(query)
    if direct:
        return direct
    # Shopify-taxonomie zoekt hoofdzakelijk op Engelse categoriebenamingen.
    # Deze vertaling levert alleen zoektermen; de gebruiker kiest de categorie.
    try:
        provider = OpenAIProvider()
        response = provider.client.with_options(timeout=30.0, max_retries=0).responses.create(
            model=os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-5.6-terra"),
            input=(
                "Vertaal deze Nederlandse productcategorie naar maximaal drie korte "
                "Engelse Shopify-taxonomiezoektermen. Geen uitleg, alleen JSON-array: "
                + json.dumps(query, ensure_ascii=False)
            ),
        )
        output = response.output_text.strip()
        if output.startswith("```"):
            output = output.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        terms = json.loads(output)
    except Exception:
        terms = []
    merged: dict[str, dict[str, Any]] = {}
    for term in terms[:3] if isinstance(terms, list) else []:
        for item in search(str(term)):
            if item.get("id"):
                merged[str(item["id"])] = item
    return list(merged.values())


def _validated_metafields(
    configured: list[dict[str, Any]], definitions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed = {
        (str(item.get("namespace") or ""), str(item.get("key") or "")):
        str((item.get("type") or {}).get("name") or item.get("type") or "")
        for item in definitions
    }
    result = []
    for item in configured:
        namespace = str(item.get("namespace") or "").strip()
        key = str(item.get("key") or "").strip()
        value = item.get("value")
        kind = allowed.get((namespace, key))
        if not kind or value in (None, ""):
            continue
        encoded = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
        result.append({"namespace": namespace, "key": key, "type": kind, "value": encoded})
    return result


def _product_handle(draft: dict[str, Any]) -> str:
    """Build a compact supplier/SKU handle, e.g. Kentie -> knt-998042."""
    supplier = str(draft.get("supplier_name") or draft.get("vendor") or "product")
    # The registered supplier name is authoritative; legal suffixes and extra
    # words must not leak into customer-facing product URLs.
    first_name = re.split(r"[\s/&]+", supplier.strip(), maxsplit=1)[0]
    ascii_name = unicodedata.normalize("NFKD", first_name).encode("ascii", "ignore").decode()
    consonants = re.sub(r"[^a-z0-9]", "", ascii_name.lower())
    consonants = re.sub(r"[aeiou]", "", consonants)
    code = consonants or re.sub(r"[^a-z0-9]", "", ascii_name.lower()) or "prd"
    sku = re.sub(r"[^a-z0-9]+", "-", str(draft.get("sku") or "").lower()).strip("-")
    return f"{code}-{sku}" if sku else code


def publish(
    service: ProductMakerService, draft_id: int, location_id: str, *, active: bool = False,
    publish_all_channels: bool = True, continue_selling: bool = False,
) -> dict[str, Any]:
    report = service.quality_report(draft_id)
    if not report["ready"]:
        failed = [label for label, passed in report["checks"].items() if not passed]
        raise ValueError("Kwaliteitspoort geblokkeerd: " + "; ".join(failed))
    draft = service.get_draft(draft_id)
    if draft["status"] not in {"draft", "shopify_draft", "active"}:
        raise ValueError("Deze productstatus kan niet via de productmaker worden gepubliceerd")
    if not str(location_id).startswith("gid://shopify/Location/"):
        raise ValueError("Kies een geldige Shopify-locatie")
    client = ShopifyClient.from_settings()
    escaped = draft["sku"].replace("\\", "\\\\").replace('"', '\\"')
    existing_result = client.graphql(
        """query($query:String!,$locationId:ID!){
        productVariants(first:10,query:$query){nodes{
        id sku inventoryItem{id inventoryLevel(locationId:$locationId){
        quantities(names:["available"]){name quantity}}}
        product{id title status}}}}""",
        {"query": f'sku:"{escaped}"', "locationId": location_id},
    )
    exact = [
        item for item in (existing_result.get("productVariants") or {}).get("nodes") or []
        if str(item.get("sku") or "").strip().upper() == draft["sku"].upper()
    ]
    existing_product_ids = {
        str((item.get("product") or {}).get("id") or "") for item in exact
    } - {""}
    if len(existing_product_ids) > 1:
        raise ValueError(
            f"Shopify bevat SKU {draft['sku']} in meerdere producten; "
            "automatisch bijwerken is daardoor niet veilig"
        )
    if draft["status"] in {"shopify_draft", "active"} and not exact:
        raise ValueError(
            "De gekoppelde actieve SKU bestaat niet meer exact in Shopify"
        )
    # Een historische lokale product-ID kan achterlopen nadat een SKU eerder
    # handmatig is verplaatst. Eén exacte Shopify-SKU is de veilige autoriteit:
    # productSet werkt dat product bij en de succesvolle uitkomst herstelt
    # hieronder automatisch de lokale product- en variantkoppeling.
    selected_images = [
        _shopify_image_source(client, item["url"]) for item in draft["assets"]
        if item["kind"] == "image" and item["selected"] and item["official"]
        and item["identifier_verified"]
    ]
    definitions = product_metafield_definitions()
    metafields = _validated_metafields(draft["metafields"], definitions)
    if draft["ean"]:
        # Alleen toevoegen wanneer de standaarddefinitie werkelijk in deze shop bestaat.
        facts_ean = next(
            (item for item in definitions
             if item.get("namespace") == "facts" and item.get("key") == "ean"), None,
        )
        if facts_ean and not any(
            item["namespace"] == "facts" and item["key"] == "ean" for item in metafields
        ):
            metafields.append({"namespace": "facts", "key": "ean",
                               "type": str((facts_ean.get("type") or {}).get("name") or "single_line_text_field"),
                               "value": draft["ean"]})
    handle = _product_handle(draft)
    variant: dict[str, Any] = {
        "optionValues": [{"optionName": "Title", "name": "Default Title"}],
        "price": draft["sale_price"], "sku": draft["sku"], "taxable": True,
        "inventoryPolicy": "CONTINUE" if continue_selling else "DENY",
        "inventoryItem": {"sku": draft["sku"], "cost": draft["purchase_price"],
                          "tracked": True, "requiresShipping": True},
    }
    if draft["compare_at_price"]:
        variant["compareAtPrice"] = draft["compare_at_price"]
    existing_target = exact[0] if exact else None
    updating_existing = bool(existing_target)
    if not updating_existing:
        variant["inventoryQuantities"] = [{"locationId": location_id, "name": "available",
                                           "quantity": int(draft["initial_quantity"])}]
    else:
        variant["id"] = existing_target["id"]
    product_input: dict[str, Any] = {
        "title": draft["title"], "handle": handle,
        "descriptionHtml": draft["description_html"], "vendor": draft["vendor"],
        "productType": draft["product_type"], "tags": draft["tags"],
        "status": "ACTIVE" if active else "DRAFT",
        "seo": {"title": draft["seo_title"] or draft["title"],
                "description": draft["seo_description"] or draft["short_description"]},
        "category": draft["category_id"],
        "productOptions": [{"name": "Title", "position": 1,
                            "values": [{"name": "Default Title"}]}],
        "variants": [variant], "metafields": metafields,
    }
    if not updating_existing:
        product_input["files"] = [
            {"originalSource": url, "contentType": "IMAGE", "alt": draft["title"]}
            for url in selected_images
        ]
    else:
        product_input["id"] = str((existing_target.get("product") or {})["id"])
    with service.connect() as db:
        db.execute("UPDATE pm_drafts SET status='publishing',updated_at=? WHERE id=?",
                   (utc_now(), int(draft_id)))
    try:
        payload = client.graphql(
            """mutation($input:ProductSetInput!){productSet(synchronous:true,input:$input){
            product{id title handle status variants(first:10){nodes{id sku}}}
            userErrors{code field message}}}""", {"input": product_input},
        ).get("productSet") or {}
        errors = payload.get("userErrors") or []
        if errors:
            raise RuntimeError("; ".join(str(item.get("message") or item) for item in errors))
        product = payload.get("product") or {}
        variants = (product.get("variants") or {}).get("nodes") or []
        target = next(
            (item for item in variants if str(item.get("sku") or "").upper() == draft["sku"].upper()),
            None,
        )
        if not product.get("id") or not target:
            raise RuntimeError("Shopify gaf het aangemaakte product niet volledig terug")
        if updating_existing:
            inventory_item_id = str(
                (existing_target.get("inventoryItem") or {}).get("id") or ""
            )
            if inventory_item_id:
                quantities = (
                    ((existing_target.get("inventoryItem") or {})
                     .get("inventoryLevel") or {}).get("quantities") or []
                )
                current_quantity = next(
                    (int(item.get("quantity") or 0) for item in quantities
                     if item.get("name") == "available"),
                    0,
                )
                inventory_payload = client.graphql(
                    """mutation($input:InventorySetQuantitiesInput!,$key:String!) {
                    inventorySetQuantities(input:$input) @idempotent(key:$key) {
                    userErrors{field message}}
                    }""",
                    {"key": str(uuid.uuid4()), "input": {
                        "name": "available", "reason": "correction",
                        "quantities": [{
                            "inventoryItemId": inventory_item_id,
                            "locationId": location_id,
                            "changeFromQuantity": current_quantity,
                            "quantity": int(draft["initial_quantity"]),
                        }],
                    }},
                ).get("inventorySetQuantities") or {}
                inventory_errors = inventory_payload.get("userErrors") or []
                if inventory_errors:
                    raise RuntimeError(
                        "Voorraad bijwerken mislukt: "
                        + "; ".join(
                            str(item.get("message") or item)
                            for item in inventory_errors
                        )
                    )
        published_channels = (
            _publish_to_all_channels(client, str(product["id"]))
            if active and publish_all_channels else 0
        )
        admin_url = f"https://{client.shop_domain}/admin/products/{str(product['id']).rsplit('/', 1)[-1]}"
        final_status = "active" if active else "shopify_draft"
        with service.connect() as db:
            db.execute(
                """UPDATE pm_drafts SET status=?,shopify_product_id=?,shopify_variant_id=?,
                   shopify_admin_url=?,updated_at=? WHERE id=?""",
                (final_status, product["id"], target["id"], admin_url, utc_now(), int(draft_id)),
            )
            service._audit(db, int(draft_id), "shopify_published",
                           {"status": final_status, "product_id": product["id"]})
        removed_from_pim = bool(active and draft.get("incidental"))
        if removed_from_pim:
            service.delete_incidental_draft(draft_id)
        return {
            "status": final_status,
            "product_id": product["id"],
            "admin_url": admin_url,
            "storefront_url": (
                f"https://weldingshop.nl/products/{product.get('handle') or handle}"
                if active else ""
            ),
            "published_channels": published_channels,
            "removed_from_pim": removed_from_pim,
        }
    except Exception:
        with service.connect() as db:
            db.execute("UPDATE pm_drafts SET status=?,updated_at=? WHERE id=?",
                       (draft["status"], utc_now(), int(draft_id)))
        raise
