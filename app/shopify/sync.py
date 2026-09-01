from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import sqlite3
import subprocess
import time
import uuid
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import requests
from PIL import Image, UnidentifiedImageError

from app.shopify.client import (
    ShopifyClient,
    collection_product_skus,
    get_shopify_settings,
    set_collection_inventory_policy,
)
from app.suppliers.hub import (
    apply_source_transformations,
    get_supplier,
    _stock_stays_active_at_zero,
    supplier_database_path,
)
from app.suppliers.quality import quality_policy_for
from app.suppliers.routes import supplier_route


Progress = Callable[[int, str], None]


def _shopify_file_url(value: Any) -> str:
    """Encode unsafe URL characters before sending remote files to Shopify."""
    return requests.utils.requote_uri(str(value or "").strip())


@lru_cache(maxsize=32768)
def _visual_image_hash(url: str) -> int | None:
    """Return a small perceptual hash; encoding/CDN changes remain equivalent."""
    if not str(url or "").startswith(("https://", "http://")):
        return None
    try:
        response = requests.get(str(url), timeout=20)
        response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as source:
            image = source.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(image.getdata())
        value = 0
        for row in range(8):
            for column in range(8):
                value = (value << 1) | int(
                    pixels[row * 9 + column]
                    > pixels[row * 9 + column + 1]
                )
        return value
    except (OSError, requests.RequestException, UnidentifiedImageError):
        return None


def _precache_visual_image_hashes(
    urls: list[str], progress_callback: Progress | None,
) -> None:
    """Fetch independent image hashes concurrently with visible heartbeats."""
    unique_urls = sorted({
        str(url).strip() for url in urls
        if str(url).startswith(("https://", "http://"))
    })
    total = len(unique_urls)
    if not total:
        _progress(progress_callback, 5, "Productafbeeldingen: niets te vergelijken")
        return
    completed = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_visual_image_hash, url) for url in unique_urls]
        for future in as_completed(futures):
            future.result()
            completed += 1
            if completed == total or completed % 50 == 0:
                _progress(
                    progress_callback,
                    3 + int(2 * completed / total),
                    f"Productafbeeldingen vergelijken: {completed} van {total}",
                )


def _visual_hash_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _missing_product_images(
    pim_images: list[dict[str, Any]], existing_media: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find actual missing images instead of assuming Shopify media positions."""
    existing_urls = [
        str((media.get("image") or {}).get("url") or "")
        for media in existing_media
        if (media.get("image") or {}).get("url")
    ]
    existing_hashes = [
        image_hash for image_hash in map(_visual_image_hash, existing_urls)
        if image_hash is not None
    ]
    if existing_urls and not existing_hashes:
        # Fail safely when remote images cannot be compared: preserve the old
        # count-based behavior rather than uploading every image again.
        return pim_images[len(existing_urls):]
    missing = []
    for image in pim_images:
        image_hash = _visual_image_hash(str(image.get("image_url") or ""))
        if image_hash is None:
            continue
        if not any(
            _visual_hash_distance(image_hash, existing_hash) <= 6
            for existing_hash in existing_hashes
        ):
            missing.append(image)
    return missing


def _duplicate_media_ids(media: list[dict[str, Any]]) -> list[str]:
    """Identify later Shopify media that are visually the same as an earlier one."""
    unique_hashes: list[int] = []
    duplicates: list[str] = []
    for item in media:
        media_id = str(item.get("id") or "")
        image_hash = _visual_image_hash(
            str((item.get("image") or {}).get("url") or "")
        )
        if not media_id or image_hash is None:
            continue
        if any(
            _visual_hash_distance(image_hash, existing_hash) == 0
            for existing_hash in unique_hashes
        ):
            duplicates.append(media_id)
        else:
            unique_hashes.append(image_hash)
    return duplicates


def _shopify_compatible_document(
    database: Path, document: dict[str, Any], path: Path,
) -> tuple[Path, str, str]:
    """Convert localized HTML to PDF because Shopify Files rejects text/html."""
    mime_type = str(document.get("mime_type") or "application/octet-stream")
    filename = str(document.get("filename") or path.name)
    if mime_type != "text/html" and path.suffix.casefold() not in {".htm", ".html"}:
        return path, filename, mime_type

    pdf_path = path.with_suffix(".pdf")
    if not os.access(path.parent, os.W_OK):
        # Oude verrijkingen kunnen door een beheertaak als root zijn gemaakt.
        # De webapp kan die SKU-map dan lezen maar er geen PDF naast schrijven.
        # Gebruik daarom een duurzame cache in de schrijfbare localized-map.
        cache_dir = path.parent.parent / "shopify-pdf-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
        pdf_path = cache_dir / f"{path.stem}-{cache_key}.pdf"
    chromium = Path("/opt/weldingshop-browser/bin/chromium")
    if not chromium.is_file():
        raise RuntimeError(
            f"Shopify-document {document['title']} is HTML en kan niet naar PDF "
            "worden omgezet: Chromium ontbreekt."
        )
    result = subprocess.run(
        [
            str(chromium), "--headless", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}", path.resolve().as_uri(),
        ],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode or not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise RuntimeError(
            f"Shopify-document {document['title']} kon niet naar PDF worden "
            f"omgezet: {detail or 'onbekende Chromium-fout'}"
        )
    filename = Path(filename).with_suffix(".pdf").name
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE product_documents SET local_path=?,filename=?,mime_type=?,
               size_bytes=?,sha256=?,updated_at=? WHERE id=?""",
            (
                str(pdf_path), filename, "application/pdf", pdf_path.stat().st_size,
                digest, datetime.now(timezone.utc).isoformat(), document["id"],
            ),
        )
    return pdf_path, filename, "application/pdf"


def _upload_product_documents(
    client: ShopifyClient, slug: str, sku: str
) -> list[dict[str, str]]:
    """Upload approved localized PIM documents to Shopify Files."""
    supplier = get_supplier(slug) or {}
    content_rules = (
        (supplier.get("request_options") or {}).get("enrichment_profile") or {}
    ).get("content") or {}
    if not content_rules.get("include_documents_in_shopify", slug == "tecweld"):
        return []
    database = supplier_database_path(slug)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        documents = [dict(row) for row in connection.execute(
            """SELECT * FROM product_documents WHERE sku=? AND language='nl-NL'
               AND quality_status='passed' ORDER BY document_type,title""",
            (sku,),
        ).fetchall()]
    uploaded: list[dict[str, str]] = []
    for document in documents:
        cached_url = str(document.get("shopify_cdn_url") or "")
        if cached_url.startswith("https://cdn.shopify.com/"):
            uploaded.append({"title": document["title"], "url": cached_url})
            continue
        path = Path(str(document.get("local_path") or ""))
        if not path.is_file():
            continue
        path, filename, mime_type = _shopify_compatible_document(
            database, document, path
        )
        stage = client.graphql(
            """mutation StageDocument($input:[StagedUploadInput!]!){
              stagedUploadsCreate(input:$input){
                stagedTargets{url resourceUrl parameters{name value}}
                userErrors{field message}
              }}""",
            {"input": [{
                "resource": "FILE", "filename": filename,
                "mimeType": mime_type, "httpMethod": "POST",
            }]},
        )["stagedUploadsCreate"]
        if stage.get("userErrors"):
            raise RuntimeError("Shopify documentupload: " + json.dumps(stage["userErrors"], ensure_ascii=False))
        target = stage["stagedTargets"][0]
        fields = {item["name"]: item["value"] for item in target["parameters"]}
        response = requests.post(
            target["url"], data=fields,
            files={"file": (filename, path.read_bytes(), mime_type)},
            timeout=180,
        )
        response.raise_for_status()
        created_payload = client.graphql(
            """mutation CreateDocument($files:[FileCreateInput!]!){
              fileCreate(files:$files){
                files{... on GenericFile{id fileStatus url}}
                userErrors{field message}
              }}""",
            {"files": [{
                "originalSource": target["resourceUrl"], "contentType": "FILE",
                "alt": document["title"], "filename": filename,
            }]},
        )["fileCreate"]
        if created_payload.get("userErrors"):
            raise RuntimeError("Shopify Files: " + json.dumps(created_payload["userErrors"], ensure_ascii=False))
        created = (created_payload.get("files") or [{}])[0]
        file_id = str(created.get("id") or "")
        cdn_url = str(created.get("url") or "")
        status = created.get("fileStatus")
        for _ in range(30):
            if status == "READY" and cdn_url.startswith("https://cdn.shopify.com/"):
                break
            time.sleep(1)
            node = client.graphql(
                """query DocumentFile($id:ID!){node(id:$id){
                  ... on GenericFile{id fileStatus url}}}""", {"id": file_id},
            ).get("node") or {}
            status, cdn_url = node.get("fileStatus"), str(node.get("url") or "")
        if not cdn_url.startswith("https://cdn.shopify.com/"):
            raise RuntimeError(f"Shopify-document {document['title']} is niet gereed.")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE product_documents SET shopify_file_id=?,shopify_cdn_url=? WHERE id=?",
                (file_id, cdn_url, document["id"]),
            )
        uploaded.append({"title": document["title"], "url": cdn_url})
    return uploaded


def _description_with_documents(description: str, documents: list[dict[str, str]]) -> str:
    if not documents:
        return description
    soup = re.sub(
        r"<section class=['\"]weldingshop-documenten['\"][\s\S]*?</section>",
        "", description, flags=re.IGNORECASE,
    ).rstrip()
    links = "".join(
        f"<li><a href='{html.escape(item['url'])}' target='_blank' rel='noopener'>"
        f"{html.escape(item['title'])}</a></li>" for item in documents
    )
    return soup + (
        "<section class='weldingshop-documenten'><h3>Documenten en downloads</h3>"
        f"<ul>{links}</ul></section>"
    )


class _RetryableBulkRowError(RuntimeError):
    def __init__(self, message: str, row_indexes: list[int]) -> None:
        super().__init__(message)
        self.row_indexes = row_indexes

OUT_OF_STOCK_DISCLAIMER = (
    "Let op: door het ontbreken van live synchronisatie met de leverancier "
    "kan de levertijd afwijken."
)


def _configured_delivery_time_notice(
    supplier: dict[str, Any], enabled: bool,
) -> str:
    if not enabled:
        return ""
    return str(
        (supplier.get("request_options") or {}).get(
            "delivery_time_notice_text", OUT_OF_STOCK_DISCLAIMER
        ) or ""
    ).strip()

def _description_meets_quality(product: dict[str, Any], description: str) -> bool:
    return quality_policy_for(
        product.get("vendor"), supplier_slug=product.get("_supplier_slug")
    ).description_is_complete(
        description
    )


def _tags_meet_quality(product: dict[str, Any], tags: list[str]) -> bool:
    return quality_policy_for(
        product.get("vendor"), supplier_slug=product.get("_supplier_slug")
    ).tags_are_complete(tags)


def _category_values(product: dict[str, Any]) -> list[str]:
    try:
        filters = json.loads(product.get("filter_values_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        filters = []
    return [
        str(value).strip() for value in [
            product.get("product_group_name"), *filters,
            product.get("execution"), product.get("subcategory_3"),
            product.get("subcategory_4"), product.get("subcategory_5"),
        ] if str(value or "").strip()
    ]


def _effective_tags(product: dict[str, Any]) -> list[str]:
    try:
        tags = [
            str(tag).strip() for tag in json.loads(
                product.get("ai_tags_json") or "[]"
            ) if str(tag).strip()
        ]
    except (json.JSONDecodeError, TypeError):
        tags = []
    tags = quality_policy_for(
        product.get("vendor"), supplier_slug=product.get("_supplier_slug")
    ).tags(product, tags)
    return list(dict.fromkeys(tag for tag in tags if tag))


def _effective_description(product: dict[str, Any]) -> str:
    description = str(product.get("html_description") or "").strip()
    if description:
        return description
    source = str(product.get("source_description") or "").strip()
    return quality_policy_for(
        product.get("vendor"), supplier_slug=product.get("_supplier_slug")
    ).description(product, source)


def _merge_product_verpakkingsopmerking(
    metafields: list[dict[str, Any]], *messages: str,
) -> list[dict[str, Any]]:
    """Merge managed customer notices into their dedicated product metafield."""
    merged = {
        (item.get("namespace"), item.get("key")): dict(item)
        for item in metafields
    }
    key = ("custom", "verpakkingsopmerking")
    existing = merged.get(key) or {}
    parts = [str(existing.get("value") or "").strip()]
    current = parts[0].casefold()
    for message in messages:
        message = str(message or "").strip()
        if message and message.casefold() not in current:
            parts.append(message)
            current = " ".join(parts).casefold()
    value = " ".join(filter(None, parts))
    if value:
        merged[key] = {
            "namespace": "custom",
            "key": "verpakkingsopmerking",
            "type": existing.get("type") or "multi_line_text_field",
            "value": value[:65535],
        }
    return list(merged.values())


def _set_product_verpakkingsopmerking(
    metafields: list[dict[str, Any]], value: str,
) -> list[dict[str, Any]]:
    """Set the product-family legend for the packaging codes in use."""
    merged = {
        (item.get("namespace"), item.get("key")): dict(item)
        for item in metafields
    }
    if value.strip():
        merged[("custom", "verpakkingsopmerking")] = {
            "namespace": "custom",
            "key": "verpakkingsopmerking",
            "type": "multi_line_text_field",
            "value": value.strip()[:65535],
        }
    return list(merged.values())


def _set_product_delivery_notice(
    metafields: list[dict[str, Any]], value: str,
) -> list[dict[str, Any]]:
    """Set the customer-facing expected delivery time metafield."""
    merged = {
        (item.get("namespace"), item.get("key")): dict(item)
        for item in metafields
    }
    notice = " ".join(str(value or "").split()).strip()
    if notice:
        merged[("custom", "verwachte_product_levertijd")] = {
            "namespace": "custom",
            "key": "verwachte_product_levertijd",
            "type": "single_line_text_field",
            "value": notice[:255],
        }
    return list(merged.values())


def _packaging_explanation(options: list[dict[str, str]]) -> str:
    """Describe each distinct packaging code used by a product family."""
    lines: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for option in options:
        code = str(option.get("packaging_code") or "").strip()
        name = str(option.get("packaging_name") or "").strip()
        weight = str(option.get("package_weight_label") or "").strip()
        identity = (code.casefold(), name.casefold(), weight.casefold())
        if not code or identity in seen:
            continue
        seen.add(identity)
        meaning = name or "verpakking"
        if weight:
            meaning += f" van {weight}"
        normalized_code = code.upper().replace(" ", "-")
        axle_size = ""
        if re.search(r"(?:^|-)D-?100(?:-|$)", normalized_code):
            axle_size = "16,5 mm"
        elif re.search(
            r"(?:^|-)(?:D-?(?:200|270|300)|BS-?300)(?:-|$)",
            normalized_code,
        ):
            axle_size = "52 mm"
        suffix = f"; asmaat Ø {axle_size}" if axle_size else ""
        lines.append(f"{code} = {meaning}{suffix}.")
    return "\n".join(lines)


def _progress(callback: Progress | None, percent: int, text: str) -> None:
    if callback:
        callback(percent, text)


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return (
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
        + "\n"
    ).encode()


def _stage(client: ShopifyClient, filename: str, content: bytes) -> str:
    result = client.graphql(
        """
        mutation($input:[StagedUploadInput!]!){
          stagedUploadsCreate(input:$input){
            stagedTargets{url parameters{name value}}
            userErrors{field message}
          }
        }
        """,
        {
            "input": [{
                "resource": "BULK_MUTATION_VARIABLES",
                "filename": filename,
                "mimeType": "text/jsonl",
                "httpMethod": "POST",
            }]
        },
    )["stagedUploadsCreate"]
    if result["userErrors"]:
        raise RuntimeError(str(result["userErrors"]))
    target = result["stagedTargets"][0]
    fields = {item["name"]: item["value"] for item in target["parameters"]}
    response = requests.post(
        target["url"],
        data=fields,
        files={"file": (filename, content, "text/jsonl")},
        timeout=180,
    )
    response.raise_for_status()
    return fields["key"]


def _start_bulk(
    client: ShopifyClient, mutation: str, staged_path: str, identifier: str
) -> str:
    result = client.graphql(
        """
        mutation($mutation:String!,$path:String!,$client:String){
          bulkOperationRunMutation(
            mutation:$mutation,stagedUploadPath:$path,clientIdentifier:$client
          ){
            bulkOperation{id status}
            userErrors{field message}
          }
        }
        """,
        {"mutation": mutation, "path": staged_path, "client": identifier},
    )["bulkOperationRunMutation"]
    if result["userErrors"]:
        raise RuntimeError(str(result["userErrors"]))
    return result["bulkOperation"]["id"]


def _wait_bulk(
    client: ShopifyClient,
    operation_id: str,
    callback: Progress | None,
    start_percent: int,
    end_percent: int,
    label: str,
) -> dict[str, Any]:
    query = """
    query($id:ID!){
      bulkOperation(id:$id){
        id status errorCode objectCount url partialDataUrl
      }
    }
    """
    last_count = 0
    while True:
        operation = client.graphql(query, {"id": operation_id})["bulkOperation"]
        count = int(operation.get("objectCount") or 0)
        last_count = max(last_count, count)
        fraction = min(last_count / 2805, 0.95)
        percent = start_percent + int((end_percent - start_percent) * fraction)
        try:
            _progress(callback, percent, f"{label}: {last_count} objecten verwerkt")
        except Exception:
            try:
                client.graphql(
                    """
                    mutation($id:ID!){
                      bulkOperationCancel(id:$id){
                        bulkOperation{id status} userErrors{field message}
                      }
                    }
                    """,
                    {"id": operation_id},
                )
            except Exception:
                pass
            raise
        if operation["status"] in {"COMPLETED", "FAILED", "CANCELED", "EXPIRED"}:
            if operation["status"] != "COMPLETED":
                raise RuntimeError(
                    f"{label} eindigde als {operation['status']}: "
                    f"{operation.get('errorCode') or 'onbekende fout'}"
                )
            _bulk_errors(operation)
            _progress(callback, end_percent, f"{label} voltooid")
            return operation
        time.sleep(3)


def _bulk_errors(operation: dict[str, Any]) -> None:
    url = operation.get("url") or operation.get("partialDataUrl")
    if not url:
        return
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    errors = []
    retryable_indexes = []
    only_product_locks = True
    for output_index, line in enumerate(response.text.splitlines()):
        row = json.loads(line)
        row_errors = []
        if row.get("errors"):
            row_errors.extend(row["errors"])
        for payload in (row.get("data") or {}).values():
            if isinstance(payload, dict) and payload.get("userErrors"):
                row_errors.extend(payload["userErrors"])
        if row_errors:
            errors.append(row_errors)
            codes = {
                str(error.get("extensions", {}).get("code") or error.get("code") or "")
                for error in row_errors if isinstance(error, dict)
            }
            if codes == {"TOO_MANY_PARALLEL_REQUESTS_FOR_THIS_PRODUCT"}:
                retryable_indexes.append(output_index)
            else:
                only_product_locks = False
        if len(errors) >= 20:
            break
    if errors:
        message = (
            "Shopify-bulkbewerking bevat fouten: "
            + json.dumps(errors, ensure_ascii=False)
        )
        if only_product_locks and retryable_indexes:
            raise _RetryableBulkRowError(message, retryable_indexes)
        raise RuntimeError(message)


def _source_products(slug: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(supplier_database_path(slug))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM products WHERE source_present=1 ORDER BY sku"
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["_supplier_slug"] = slug
            item["images"] = [
                dict(image)
                for image in conn.execute(
                    """
                    SELECT image_url,position,alt_text FROM product_images
                    WHERE sku=? ORDER BY position
                    """,
                    (item["sku"],),
                ).fetchall()
            ]
            if not item["images"]:
                fallback = quality_policy_for(
                    item.get("vendor"), supplier_slug=slug
                ).fallback_image(item)
                if fallback:
                    item["images"] = [{**fallback, "position": 1}]
            result.append(item)
        return result
    finally:
        conn.close()


def _sync_record_hash(
    product: dict[str, Any], supplier: dict[str, Any]
) -> str:
    ignored = {
        "source_updated_at", "shopify_draft_since",
        "_has_shopify_image", "raw_data_json",
    }
    payload = {
        "product": {
            key: value for key, value in product.items()
            if key not in ignored and not key.startswith("_")
        },
        "images": product.get("images") or [],
        "raw_data": product.get("_raw_data") or {},
        "field_mapping": supplier.get("shopify_field_mapping") or {},
        "metafield_mapping": (
            supplier.get("shopify_metafield_mapping") or {}
        ),
        "transformations": supplier.get("source_transformations") or {},
        "family_variant_options": product.get(
            "_family_variant_options"
        ) or {},
        "product_status": supplier.get("sync_product_status") or "active",
        "new_product_policy": (
            supplier.get("sync_new_product_policy") or "existing_only"
        ),
        "publish_all": bool(supplier.get("sync_publish_all", 1)),
        "continue_selling_when_out_of_stock": bool(
            (supplier.get("request_options") or {}).get(
                "continue_selling_when_out_of_stock", False
            )
        ),
        "continue_selling_collection_rules": (
            (supplier.get("request_options") or {}).get(
                "continue_selling_collection_rules", []
            )
        ),
        "draft_only_when_no_location_stock": bool(
            (supplier.get("request_options") or {}).get(
                "draft_only_when_no_location_stock", False
            )
        ),
        "keep_active_when_out_of_stock": bool(
            (supplier.get("request_options") or {}).get(
                "keep_active_when_out_of_stock", False
            )
        ),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _select_changed_products(
    products: list[dict[str, Any]],
    supplier: dict[str, Any],
    previous_hashes: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, str]]]:
    current_hashes = {
        product["sku"].upper(): _sync_record_hash(product, supplier)
        for product in products
    }
    selected = []
    details = []
    for product in products:
        normalized_sku = product["sku"].upper()
        previous_hash = previous_hashes.get(normalized_sku)
        if previous_hash == current_hashes[normalized_sku]:
            continue
        selected.append(product)
        details.append({
            "sku": product["sku"],
            "reason": (
                "Eerste meting of nieuw product"
                if previous_hash is None else "Bronrecord gewijzigd"
            ),
        })
    return selected, current_hashes, details


def _load_family_variant_options(slug: str) -> dict[str, dict[str, str]]:
    """Load the canonical PIM-family content for every Shopify variant."""
    path = supplier_database_path(slug)
    with sqlite3.connect(path) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "product_families" not in tables:
            return {}
        rows = conn.execute(
            "SELECT family_key,family_json FROM product_families"
        ).fetchall()
    result: dict[str, dict[str, str]] = {}
    for family_key, family_json in rows:
        try:
            family = json.loads(family_json or "{}")
        except json.JSONDecodeError:
            continue
        family_variants = family.get("variants") or []
        def family_content_score(variant: dict[str, Any]) -> tuple[int, int, int]:
            raw = variant.get("current_raw_data") or {}
            enrichment = raw.get("website_enrichment") or {}
            return (
                len(str(variant.get("html_description") or "").strip()),
                len(variant.get("images") or []),
                len((enrichment.get("facts") or {}).get("properties") or []),
            )

        richest_variant = max(
            family_variants, key=family_content_score, default={}
        )
        family_description = str(
            richest_variant.get("html_description") or ""
        ).strip()
        family_image = str(family.get("image_url") or "").strip()
        for variant in family.get("variants") or []:
            sku = str(variant.get("sku") or "").strip().upper()
            diameter = str(
                variant.get("diameter_label") or ""
            ).strip()
            packaging = str(
                variant.get("packaging_option_label")
                or variant.get("package_label") or ""
            ).strip()
            if sku and diameter and packaging:
                result[sku] = {
                    "family_key": str(family_key),
                    "diameter": diameter,
                    "packaging": packaging,
                    "packaging_code": str(
                        variant.get("packaging_code") or ""
                    ).strip(),
                    "packaging_name": str(
                        variant.get("packaging_name") or ""
                    ).strip(),
                    "package_weight_label": str(
                        variant.get("package_weight_label") or ""
                    ).strip(),
                    "family_title": str(
                        family.get("title") or ""
                    ).strip(),
                    "family_description": family_description,
                    "family_image": family_image,
                }
    return result


def _apply_canonical_family_content(
    product: dict[str, Any], family: dict[str, str]
) -> None:
    """Make the persisted product family authoritative for Shopify content."""
    if not family:
        return
    if family.get("family_title"):
        product["ai_title"] = family["family_title"]
    if family.get("family_description"):
        product["html_description"] = family["family_description"]
    family_image = str(family.get("family_image") or "").strip()
    if family_image and not product.get("images"):
        product["images"] = [{
            "image_url": family_image,
            "position": 1,
            "alt_text": family.get("family_title") or product.get("source_title") or "",
        }]


def _validate_existing_family_layout(
    products: list[dict[str, Any]], existing: dict[str, dict[str, Any]]
) -> None:
    """Abort instead of silently merging multiple PIM families in Shopify."""
    families_by_product: dict[str, set[str]] = {}
    for product in products:
        family_key = str(
            (product.get("_family_variant_options") or {}).get("family_key") or ""
        ).strip()
        match = existing.get(str(product.get("sku") or "").upper())
        if family_key and match:
            families_by_product.setdefault(match["product"]["id"], set()).add(
                family_key
            )
    conflicts = {
        product_id: sorted(families)
        for product_id, families in families_by_product.items()
        if len(families) > 1
    }
    if conflicts:
        raise ValueError(
            "Shopify-product bevat varianten uit meerdere PIM-productfamilies: "
            + json.dumps(conflicts, ensure_ascii=False)
        )
    products_by_family: dict[str, set[str]] = {}
    for product in products:
        family_key = str(
            (product.get("_family_variant_options") or {}).get("family_key") or ""
        ).strip()
        match = existing.get(str(product.get("sku") or "").upper())
        if family_key and match:
            products_by_family.setdefault(family_key, set()).add(
                match["product"]["id"]
            )
    split_families = {
        family_key: sorted(product_ids)
        for family_key, product_ids in products_by_family.items()
        if len(product_ids) > 1
    }
    if split_families:
        raise ValueError(
            "PIM-productfamilie staat verdeeld over meerdere Shopify-producten: "
            + json.dumps(split_families, ensure_ascii=False)
        )
    mapped_state_by_product: dict[str, set[bool]] = {}
    for product in products:
        match = existing.get(str(product.get("sku") or "").upper())
        if match:
            mapped_state_by_product.setdefault(
                match["product"]["id"], set()
            ).add(bool(
                (product.get("_family_variant_options") or {}).get("family_key")
            ))
    mixed_mapping = sorted(
        product_id for product_id, states in mapped_state_by_product.items()
        if len(states) > 1
    )
    if mixed_mapping:
        raise ValueError(
            "Shopify-product mengt familievarianten met losse PIM-artikelen: "
            + json.dumps(mixed_mapping, ensure_ascii=False)
        )


def _expand_selected_product_families(
    slug: str,
    all_products: list[dict[str, Any]],
    selected_products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Expand changed SKUs to members of their persisted PIM families."""
    if not selected_products:
        return [], []
    path = supplier_database_path(slug)
    with sqlite3.connect(path) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "product_family_variants" not in tables:
            return selected_products, []
        family_rows = conn.execute(
            "SELECT family_key,sku FROM product_family_variants"
        ).fetchall()

    families_by_sku: dict[str, set[str]] = {}
    skus_by_family: dict[str, set[str]] = {}
    for family_key, sku in family_rows:
        normalized_sku = str(sku).upper()
        families_by_sku.setdefault(normalized_sku, set()).add(
            str(family_key)
        )
        skus_by_family.setdefault(str(family_key), set()).add(normalized_sku)

    originally_selected = {
        product["sku"].upper() for product in selected_products
    }
    expanded_skus = set(originally_selected)
    triggering_skus: dict[str, set[str]] = {}
    for selected_sku in originally_selected:
        for family_key in families_by_sku.get(selected_sku, set()):
            for family_sku in skus_by_family.get(family_key, set()):
                expanded_skus.add(family_sku)
                triggering_skus.setdefault(family_sku, set()).add(
                    selected_sku
                )

    expanded = [
        product for product in all_products
        if product["sku"].upper() in expanded_skus
    ]
    details = [
        {
            "sku": product["sku"],
            "reason": "Familielid van gewijzigde SKU: " + ", ".join(
                sorted(triggering_skus[product["sku"].upper()])
            ),
        }
        for product in expanded
        if product["sku"].upper() not in originally_selected
    ]
    return expanded, details


def _load_sync_hashes(slug: str) -> dict[str, str]:
    with sqlite3.connect(supplier_database_path(slug)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shopify_sync_state (
                sku TEXT PRIMARY KEY,
                record_hash TEXT NOT NULL,
                synced_at TEXT NOT NULL
            )
            """
        )
        return {
            str(row[0]).upper(): str(row[1])
            for row in conn.execute(
                "SELECT sku,record_hash FROM shopify_sync_state"
            )
        }


def _save_sync_hashes(
    slug: str, hashes: dict[str, str], active_skus: set[str]
) -> None:
    now = datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()
    with sqlite3.connect(supplier_database_path(slug)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shopify_sync_state (
                sku TEXT PRIMARY KEY,
                record_hash TEXT NOT NULL,
                synced_at TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO shopify_sync_state(sku,record_hash,synced_at)
            VALUES(?,?,?)
            ON CONFLICT(sku) DO UPDATE SET
                record_hash=excluded.record_hash,
                synced_at=excluded.synced_at
            """,
            [
                (sku, record_hash, now)
                for sku, record_hash in hashes.items()
            ],
        )
        if active_skus:
            placeholders = ",".join("?" for _ in active_skus)
            conn.execute(
                f"DELETE FROM shopify_sync_state "
                f"WHERE UPPER(sku) NOT IN ({placeholders})",
                tuple(active_skus),
            )
        else:
            conn.execute("DELETE FROM shopify_sync_state")


def initialize_sync_baseline(slug: str) -> dict[str, int]:
    supplier = get_supplier(slug) or {}
    source = _source_products(slug)
    family_variant_options = _load_family_variant_options(slug)
    for product in source:
        try:
            product["_raw_data"] = json.loads(
                product.get("raw_data_json") or "{}"
            )
        except json.JSONDecodeError:
            product["_raw_data"] = {}
        product["_shopify_field_mapping"] = (
            supplier.get("shopify_field_mapping") or {}
        )
        product["_shopify_metafield_mapping"] = (
            supplier.get("shopify_metafield_mapping") or {}
        )
        product["_family_variant_options"] = family_variant_options.get(
            product["sku"].upper(), {}
        )
        _apply_canonical_family_content(
            product, product["_family_variant_options"]
        )
    hashes = {
        product["sku"].upper(): _sync_record_hash(product, supplier)
        for product in source
    }
    _save_sync_hashes(slug, hashes, set(hashes))
    return {"products": len(source)}


def _missing_products(slug: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(supplier_database_path(slug))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row) for row in conn.execute(
                """
                SELECT sku,shopify_draft_since
                FROM products
                WHERE source_present=0
                ORDER BY sku
                """
            ).fetchall()
        ]
    finally:
        conn.close()


def _staged_product_status(
    product_id: str,
    existing_status_by_product_id: dict[str, str],
) -> str:
    """Preserve live products while new/draft products await validation."""
    if existing_status_by_product_id.get(product_id, "").upper() == "ACTIVE":
        return "ACTIVE"
    return "DRAFT"


def _shopify_products(
    client: ShopifyClient, vendor: str, supplier_slug: str = "",
    progress_callback: Progress | None = None,
) -> dict[str, dict[str, Any]]:
    query = """
    query($after:String,$query:String!){
      products(first:20,after:$after,query:$query){
        pageInfo{hasNextPage endCursor}
        nodes{
          id title handle status descriptionHtml tags
          metafields(first:50,namespace:"custom"){
            nodes{key value}
          }
          media(first:100){
            nodes{
              id status mediaErrors{code details message}
              ... on MediaImage{image{url width height}}
            }
          }
          variants(first:100){
            nodes{
              id sku price inventoryItem{id}
              metafields(first:20,namespace:"custom"){
                nodes{key value}
              }
              media(first:1){
                nodes{id ... on MediaImage{image{url}}}
              }
            }
          }
        }
      }
    }
    """
    after = None
    by_sku: dict[str, dict[str, Any]] = {}
    product_count = 0
    page_count = 0
    route = supplier_route(supplier_slug) if supplier_slug else None
    vendor_names = (route.shopify_vendor_names if route else ()) or (vendor,)
    escaped_names = [
        name.replace("\\", "\\\\").replace('"', '\\"')
        for name in vendor_names
    ]
    vendor_query = (
        f'vendor:"{escaped_names[0]}"' if len(escaped_names) == 1
        else "(" + " OR ".join(f'vendor:"{name}"' for name in escaped_names) + ")"
    )
    while True:
        connection = client.graphql(
            query, {"after": after, "query": vendor_query}
        )["products"]
        page_count += 1
        product_count += len(connection["nodes"])
        for product in connection["nodes"]:
            for variant in product["variants"]["nodes"]:
                sku = (variant.get("sku") or "").strip().upper()
                if not sku:
                    continue
                if sku in by_sku:
                    existing_product = by_sku[sku]["product"]
                    if product.get("id") == existing_product.get("id"):
                        # Twee varianten binnen hetzelfde product gebruiken
                        # dezelfde SKU. Productinhoud kan veilig via één van
                        # beide varianten worden gekoppeld.
                        continue
                    def duplicate_rank(item: dict[str, Any]) -> tuple[int, int, int]:
                        status = str(item.get("status") or "").upper()
                        usable = int(status in {"ACTIVE", "DRAFT"})
                        variants = len(
                            (item.get("variants") or {}).get("nodes") or []
                        )
                        match = re.search(r"(\d+)$", str(item.get("id") or ""))
                        numeric_id = int(match.group(1)) if match else 0
                        # Status ACTIVE/DRAFT mag de keuze niet veranderen.
                        return usable, variants, numeric_id

                    if duplicate_rank(product) <= duplicate_rank(existing_product):
                        continue
                by_sku[sku] = {"product": product, "variant": variant}
        _progress(
            progress_callback,
            2,
            (
                f"Shopify inventariseren: {product_count} producten en "
                f"{len(by_sku)} SKU’s gevonden (pagina {page_count})…"
            ),
        )
        if not connection["pageInfo"]["hasNextPage"]:
            return by_sku
        after = connection["pageInfo"]["endCursor"]


def _inventory_items_with_stock_elsewhere(
    client: ShopifyClient,
    inventory_item_ids: list[str],
    excluded_location_id: str,
) -> set[str]:
    stocked: set[str] = set()
    for index in range(0, len(inventory_item_ids), 20):
        chunk = inventory_item_ids[index:index + 20]
        data = client.graphql(
            """
            query($ids:[ID!]!){
              nodes(ids:$ids){
                ... on InventoryItem{
                  id inventoryLevels(first:50){nodes{
                    location{id}
                    quantities(names:["available"]){name quantity}
                  }}
                }
              }
            }
            """,
            {"ids": chunk},
        )
        for item in data.get("nodes") or []:
            if not item:
                continue
            if any(
                str((level.get("location") or {}).get("id") or "")
                != excluded_location_id
                and any(
                    quantity.get("name") == "available"
                    and int(quantity.get("quantity") or 0) > 0
                    for quantity in level.get("quantities") or []
                )
                for level in (item.get("inventoryLevels") or {}).get("nodes") or []
            ):
                stocked.add(item["id"])
    return stocked


def _inventory_items_with_stock_at_location(
    client: ShopifyClient,
    inventory_item_ids: list[str],
    location_id: str,
) -> set[str]:
    stocked: set[str] = set()
    for index in range(0, len(inventory_item_ids), 20):
        chunk = inventory_item_ids[index:index + 20]
        data = client.graphql(
            """
            query($ids:[ID!]!){
              nodes(ids:$ids){
                ... on InventoryItem{
                  id inventoryLevels(first:50){nodes{
                    location{id}
                    quantities(names:["available"]){name quantity}
                  }}
                }
              }
            }
            """,
            {"ids": chunk},
        )
        for item in data.get("nodes") or []:
            if not item:
                continue
            if any(
                str((level.get("location") or {}).get("id") or "")
                == location_id
                and any(
                    quantity.get("name") == "available"
                    and int(quantity.get("quantity") or 0) > 0
                    for quantity in level.get("quantities") or []
                )
                for level in (item.get("inventoryLevels") or {}).get("nodes") or []
            ):
                stocked.add(str(item["id"]))
    return stocked


def _months_ago(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _shopify_media_is_rejected(media: dict[str, Any]) -> bool:
    image = media.get("image") or {}
    url = str(image.get("url") or "").casefold()
    filename_rejected = any(marker in url for marker in (
        "product_mini", "/img/tmp/", "thumbnail", "_thumb.", "-thumb.",
    ))
    width = int(image.get("width") or 0)
    height = int(image.get("height") or 0)
    is_small_thumbnail = width > 0 and height > 0 and width < 600 and height < 600
    return filename_rejected or is_small_thumbnail


def _expected_media_by_product(
    products: list[dict[str, Any]],
    shopify_products: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Collect unique PIM image URLs per actual Shopify product."""
    expected: dict[str, dict[str, Any]] = {}
    for product in products:
        match = shopify_products.get(str(product.get("sku") or "").upper())
        if not match:
            continue
        product_id = str(match["product"]["id"])
        item = expected.setdefault(product_id, {
            "product": match["product"],
            "skus": [],
            "images": [],
        })
        item["skus"].append(str(product.get("sku") or ""))
        known_urls = {image["image_url"] for image in item["images"]}
        for image in product.get("images") or []:
            url = str(image.get("image_url") or "").strip()
            if url and url not in known_urls:
                item["images"].append(image)
                known_urls.add(url)
    return expected


def _ready_shopify_images(product: dict[str, Any]) -> list[dict[str, Any]]:
    """Count every successfully processed image for source completeness.

    Image dimensions are handled by the separate thumbnail cleanup. Excluding
    small but READY source images here makes retries impossible: uploading the
    same supplier image naturally produces the same dimensions again.
    """
    return [
        media for media in (product.get("media") or {}).get("nodes") or []
        if (media.get("image") or {}).get("url")
        and str(media.get("status") or "READY").upper() == "READY"
        and not media.get("mediaErrors")
    ]


def _processing_shopify_images(product: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        media for media in (product.get("media") or {}).get("nodes") or []
        if str(media.get("status") or "").upper() in {"UPLOADED", "PROCESSING"}
    ]


def _media_repair_rows(
    products: list[dict[str, Any]],
    shopify_products: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build append-only repairs for products with fewer ready images than PIM."""
    rows: list[dict[str, Any]] = []
    deficiencies: list[dict[str, Any]] = []
    for product_id, item in _expected_media_by_product(
        products, shopify_products
    ).items():
        expected_images = item["images"]
        if not expected_images:
            continue
        ready_count = len(_ready_shopify_images(item["product"]))
        if ready_count >= len(expected_images):
            continue
        deficiency = {
            "product_id": product_id,
            "skus": item["skus"],
            "expected": len(expected_images),
            "ready": ready_count,
            "processing": len(_processing_shopify_images(item["product"])),
        }
        deficiencies.append(deficiency)
        if deficiency["processing"]:
            continue
        missing = expected_images[ready_count:]
        rows.append({
            "productId": product_id,
            "media": [{
                "originalSource": _shopify_file_url(image["image_url"]),
                "alt": image.get("alt_text") or item["product"].get("title") or "",
                "mediaContentType": "IMAGE",
            } for image in missing],
        })
    return rows, deficiencies


def _ensure_shopify_media_complete(
    client: ShopifyClient,
    vendor: str,
    products: list[dict[str, Any]],
    refreshed: dict[str, dict[str, Any]],
    callback: Progress | None = None,
    supplier_slug: str = "",
) -> tuple[
    dict[str, dict[str, Any]], int, list[dict[str, Any]],
]:
    """Wait for media and return any product-scoped unresolved deficiencies."""
    repair_count = 0
    current = refreshed
    for attempt in range(4):
        rows, deficiencies = _media_repair_rows(products, current)
        if not deficiencies:
            return current, repair_count, []
        if rows and attempt < 3:
            repair_count += len(rows)
            _run_rows(
                client,
                rows,
                """mutation call($productId:ID!,$media:[CreateMediaInput!]!){
                  productCreateMedia(productId:$productId,media:$media){
                    media{id status}
                    mediaUserErrors{field message code}
                  }}""",
                f"media-repair-{attempt + 1}.jsonl",
                f"pim-media-repair-{uuid.uuid4().hex[:8]}",
                callback,
                57,
                58,
                f"Ontbrekende productfoto's herstellen (poging {attempt + 1})",
            )
        if attempt < 3:
            time.sleep(2 * (attempt + 1))
            current = (
                _shopify_products(client, vendor, supplier_slug)
                if supplier_slug else _shopify_products(client, vendor)
            )
            continue
        return current, repair_count, deficiencies
    return current, repair_count, []


def _handle(title: str, sku: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{title}-{sku}".lower()).strip("-")[:240]


def _selling_price(product: dict[str, Any]) -> float | None:
    value = (
        product["sale_price"]
        if product.get("sale_price") is not None
        else product.get("price")
    )
    if value is not None:
        price = float(value)
        bundle_quantity = _certilas_mandatory_bundle_quantity(product)
        if bundle_quantity is not None and price > 0:
            return round(price * bundle_quantity, 2)
        return price
    gross_per_kg = product.get("gross_purchase_price_per_kg")
    package_weight = product.get("kg_per_sales_unit")
    if gross_per_kg is not None and package_weight is not None:
        return round(float(gross_per_kg) * float(package_weight), 2)
    return None


def _certilas_source_decimal(
    product: dict[str, Any], key: str,
) -> float | None:
    """Read one finite positive decimal from the raw Certilas dealer feed."""
    if str(product.get("_supplier_slug") or "").casefold() != "certilas":
        return None
    raw = product.get("_raw_data")
    if not isinstance(raw, dict):
        try:
            raw = json.loads(product.get("raw_data_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
    value = raw.get(key)
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value).strip().replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    if parsed != parsed or abs(parsed) == float("inf") or parsed <= 0:
        return None
    return parsed


def _certilas_mandatory_bundle_quantity(
    product: dict[str, Any],
) -> int | None:
    """Return the safe whole-package bundle quantity from the Certilas feed.

    The source fields are weights. A bundle is only considered mandatory when
    it is heavier than one package and their ratio is a whole number. This
    intentionally excludes missing prices, NaN values and ambiguous source
    rows instead of guessing a commercial quantity.
    """
    if str(product.get("_supplier_slug") or "").casefold() != "certilas":
        return None
    unit_weight = _certilas_source_decimal(product, "KG Ceweld Unit")
    bundle_weight = _certilas_source_decimal(product, "KG Ceweld Bundle")
    if not unit_weight or not bundle_weight:
        return None
    if bundle_weight <= unit_weight:
        return None
    ratio = bundle_weight / unit_weight
    quantity = round(ratio)
    if quantity < 2 or abs(ratio - quantity) > 1e-6:
        return None
    return int(quantity)


def _certilas_bundle_metafields(
    product: dict[str, Any],
) -> list[dict[str, str]]:
    """Build the managed variant bundle fields from the Certilas PIM row."""
    if str(product.get("_supplier_slug") or "").casefold() != "certilas":
        return []
    unit_weight = _certilas_source_decimal(product, "KG Ceweld Unit")
    bundle_weight = _certilas_source_decimal(product, "KG Ceweld Bundle")
    quantity = _certilas_mandatory_bundle_quantity(product)
    package_price_value = (
        product["sale_price"]
        if product.get("sale_price") is not None
        else product.get("price")
    )
    try:
        package_price = float(package_price_value)
    except (TypeError, ValueError):
        package_price = 0

    gross_per_kg = product.get("gross_purchase_price_per_kg")
    net_per_kg = product.get("net_purchase_price_per_kg")
    cost_price = product.get("cost_price")
    purchase_weight = product.get("kg_per_sales_unit") or unit_weight
    try:
        gross_total = float(gross_per_kg) * float(purchase_weight)
        net_total = float(net_per_kg) * float(purchase_weight)
        discount_percent = (1 - float(net_per_kg) / float(gross_per_kg)) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        gross_total = net_total = discount_percent = 0
    raw_pricing = product.get("_raw_data")
    if not isinstance(raw_pricing, dict):
        try:
            raw_pricing = json.loads(product.get("raw_data_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            raw_pricing = {}
    try:
        source_discount = float(
            str(raw_pricing.get("Discount") or "").strip().rstrip("%").replace(",", ".")
        )
        discount_percent = source_discount
    except (TypeError, ValueError):
        pass
    try:
        alloy_surcharge = max(0, float(cost_price) - net_total)
    except (TypeError, ValueError):
        alloy_surcharge = 0

    def decimal_text(value: float) -> str:
        return f"{value:.6f}".rstrip("0").rstrip(".")

    def dutch_decimal(value: float) -> str:
        return decimal_text(value).replace(".", ",")

    values: dict[str, tuple[str, str | None]] = {
        "certilas_product": ("boolean", "true"),
        "verplichte_bundel": ("boolean", "true" if quantity else "false"),
        "verpakkingen_per_bundel": (
            "number_integer", str(quantity) if quantity else None,
        ),
        "kg_per_verpakking": (
            "number_decimal", decimal_text(unit_weight) if unit_weight else None,
        ),
        "kg_per_bundel": (
            "number_decimal", decimal_text(bundle_weight) if bundle_weight else None,
        ),
        "prijs_per_verpakking": (
            "number_decimal", f"{package_price:.2f}" if package_price > 0 else None,
        ),
        "inkoop_brutoprijs": (
            "number_decimal", decimal_text(gross_total) if gross_total > 0 else None,
        ),
        "inkoop_korting_percentage": (
            "number_decimal",
            decimal_text(discount_percent) if gross_total > 0 else None,
        ),
        "inkoop_netto_prijs": (
            "number_decimal", decimal_text(net_total) if net_total > 0 else None,
        ),
        "inkoop_legeringstoeslag": (
            "number_decimal",
            decimal_text(alloy_surcharge) if net_total > 0 and alloy_surcharge > 0 else None,
        ),
        "inkoop_verpakkingsgewicht": (
            "number_decimal",
            decimal_text(float(purchase_weight)) if purchase_weight else None,
        ),
        "minimale_afname": (
            "single_line_text_field",
            (
                "Let op: deze prijs geldt voor één verplichte bundel van "
                f"{quantity} verpakkingen à {dutch_decimal(unit_weight)} kg. "
                f"Totaalgewicht {dutch_decimal(bundle_weight)} kg. "
                "Prijs per verpakking "
                f"€{f'{package_price:.2f}'.replace('.', ',')}."
            )
            if quantity and unit_weight and bundle_weight and package_price > 0
            else None,
        ),
    }
    return [
        {
            "namespace": "custom", "key": key, "type": metafield_type,
            "value": value,
        }
        for key, (metafield_type, value) in values.items()
        if value is not None
    ]


def _mapped(product: dict[str, Any], target: str) -> Any:
    source_field = (product.get("_shopify_field_mapping") or {}).get(target)
    if not source_field:
        return None
    return (product.get("_raw_data") or {}).get(source_field)


def _mapped_any(product: dict[str, Any], *targets: str) -> Any:
    for target in targets:
        value = _mapped(product, target)
        if value not in (None, ""):
            return value
    return None


def _metafield_value(value: Any, metafield_type: str) -> str | None:
    if value in (None, ""):
        return None
    kind = (metafield_type or "").strip()
    text = str(value).strip()
    try:
        if kind == "boolean":
            normalized = text.casefold()
            if normalized in {"1", "true", "yes", "ja", "y"}:
                return "true"
            if normalized in {"0", "false", "no", "nee", "n"}:
                return "false"
            return None
        if kind == "number_integer":
            number = float(text.replace(",", "."))
            return str(int(number)) if number.is_integer() else None
        if kind == "number_decimal":
            return str(float(text.replace(",", ".")))
        if kind == "json":
            parsed = json.loads(text) if isinstance(value, str) else value
            return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        if kind.startswith("list."):
            if isinstance(value, (list, tuple)):
                items = list(value)
            else:
                try:
                    parsed = json.loads(text)
                    items = parsed if isinstance(parsed, list) else [parsed]
                except json.JSONDecodeError:
                    items = [
                        item.strip()
                        for item in re.split(r"[\n,;|]", text)
                        if item.strip()
                    ]
            return json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        if kind.endswith("_reference"):
            return text if text.startswith("gid://shopify/") else None
        if kind.startswith(("money", "dimension", "volume", "weight", "rating")):
            parsed = json.loads(text)
            return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return text[:65535] if text else None


def _mapped_metafields(
    product: dict[str, Any], owner: str
) -> list[dict[str, str]]:
    raw = product.get("_raw_data") or {}
    mappings = product.get("_shopify_metafield_mapping") or {}
    result = []
    for config in mappings.values():
        if config.get("owner") != owner:
            continue
        source_field = config.get("source_field")
        metafield_type = config.get("type") or ""
        value = _metafield_value(raw.get(source_field), metafield_type)
        if value is None:
            continue
        result.append({
            "namespace": config["namespace"],
            "key": config["key"],
            "type": metafield_type,
            "value": value,
        })
    return result


def _eligible(product: dict[str, Any]) -> bool:
    price = _selling_price(product)
    has_image = bool(
        product.get("images") or product.get("_has_shopify_image")
    )
    return (
        (
            bool(product.get("available"))
            or str(product.get("inventory_policy") or "").casefold()
            == "continue"
            or bool(product.get("_has_other_shopify_stock"))
            or bool(product.get("_keep_active_when_out_of_stock"))
        )
        and price is not None
        and price > 0
        and has_image
    )


def _has_valid_price(product: dict[str, Any]) -> bool:
    price = _selling_price(product)
    return price is not None and price > 0


def _complete_for_new_product(product: dict[str, Any]) -> bool:
    """Nieuwe Shopify-producten vereisen alle commerciële basisgegevens."""
    title = product.get("ai_title") or product.get("source_title")
    description = (
        product.get("html_description")
        or product.get("source_description")
    )
    selling_price = _selling_price(product)
    try:
        cost_price = float(product.get("cost_price"))
    except (TypeError, ValueError):
        cost_price = 0
    return all((
        str(product.get("sku") or "").strip(),
        str(title or "").strip(),
        str(description or "").strip(),
        product.get("images"),
        selling_price is not None and selling_price > 0,
        cost_price > 0,
    ))


def _common_product_content_errors(product: dict[str, Any]) -> list[str]:
    """Return supplier-policy quality errors that can isolate one product."""
    policy = quality_policy_for(
        product.get("vendor"), supplier_slug=product.get("_supplier_slug")
    )
    errors = []
    if not _has_valid_price(product):
        errors.append("verkoopprijs ontbreekt of is nul")
    image_count = len(product.get("images") or [])
    if product.get("_has_shopify_image"):
        image_count = max(1, image_count)
    if image_count < policy.image_minimum:
        errors.append(
            f"minder dan {policy.image_minimum} goedgekeurde productafbeelding(en)"
        )
    if not _description_meets_quality(product, _effective_description(product)):
        errors.append("verrijkte HTML-producttekst ontbreekt")
    if not _tags_meet_quality(product, _effective_tags(product)):
        errors.append(f"minder dan {policy.tag_minimum} relevante PIM-tags")
    description = _effective_description(product)
    tags = _effective_tags(product)
    errors.extend(policy.validation_errors(product, description, tags))
    return errors


def _validate_product_content_gate(
    products: list[dict[str, Any]], all_source: list[dict[str, Any]],
) -> None:
    """Fail closed before Shopify receives commercially incomplete content."""
    errors: list[str] = []
    selected_skus = {str(item.get("sku") or "").upper() for item in products}
    expected_by_family: dict[str, set[str]] = {}
    for item in all_source:
        family_key = str(
            (item.get("_family_variant_options") or {}).get("family_key") or ""
        )
        # Prijsloze bronregels kunnen volgens het synchronisatiecontract niet
        # nieuw in Shopify worden aangemaakt en mogen een uploadbare familie
        # daarom niet permanent blokkeren.
        if family_key and _has_valid_price(item):
            expected_by_family.setdefault(family_key, set()).add(
                str(item.get("sku") or "").upper()
            )
    selected_families = {
        str((item.get("_family_variant_options") or {}).get("family_key") or "")
        for item in products
        if (item.get("_family_variant_options") or {}).get("family_key")
    }
    for family_key in selected_families:
        missing = expected_by_family.get(family_key, set()) - selected_skus
        if missing:
            errors.append(
                f"familie {family_key}: varianten ontbreken: {', '.join(sorted(missing))}"
            )
    for product in products:
        sku = str(product.get("sku") or "zonder SKU")
        policy = quality_policy_for(
            product.get("vendor"), supplier_slug=product.get("_supplier_slug")
        )
        if not _has_valid_price(product):
            errors.append(f"{sku}: verkoopprijs ontbreekt of is nul")
        image_count = len(product.get("images") or [])
        if product.get("_has_shopify_image"):
            image_count = max(1, image_count)
        if image_count < policy.image_minimum:
            errors.append(
                f"{sku}: minder dan {policy.image_minimum} goedgekeurde "
                "productafbeelding(en)"
            )
        description = _effective_description(product)
        if not _description_meets_quality(product, description):
            errors.append(f"{sku}: verrijkte HTML-producttekst ontbreekt")
        tags = _effective_tags(product)
        if not _tags_meet_quality(product, tags):
            required_tags = policy.tag_minimum
            errors.append(
                f"{sku}: minder dan {required_tags} relevante PIM-tags"
            )
        errors.extend(
            f"{sku}: {message}"
            for message in policy.validation_errors(product, description, tags)
        )
    if errors:
        raise ValueError(
            "Shopify-synchronisatie vóór upload geblokkeerd:\n- "
            + "\n- ".join(errors[:100])
        )


def _input(
    product: dict[str, Any], existing: dict[str, Any] | None
) -> dict[str, Any]:
    route = supplier_route(str(product.get("_supplier_slug") or ""))
    mapped_title = _mapped(product, "product.title")
    existing_title = (
        ((existing or {}).get("product") or {}).get("title")
    )
    title = (
        mapped_title
        or product.get("ai_title")
        or product.get("source_title")
        or existing_title
        or product["sku"]
    )
    # Shopify Product.title is limited to 255 characters.
    title = str(title).strip()[:255]
    description = (
        _effective_description(product)
        or (((existing or {}).get("product") or {}).get("descriptionHtml"))
        or ""
    )
    description = _mapped_any(
        product, "product.descriptionHtml", "product.description_html"
    ) or description
    price = _selling_price(product)
    mapped_price = _mapped(product, "variant.price")
    if mapped_price not in (None, ""):
        price = float(mapped_price)
    if price is None:
        price = 0
    variant: dict[str, Any] = {
        "optionValues": [{"optionName": "Title", "name": "Default Title"}],
        "sku": str(_mapped(product, "variant.sku") or product["sku"]),
        "price": f"{price:.2f}",
        "inventoryPolicy": (
            product.get("inventory_policy") or "continue"
        ).upper(),
        "taxable": True,
        "inventoryItem": {
            "tracked": True,
            "requiresShipping": True,
        },
    }
    if product.get("_exclude_inventory_policy"):
        variant.pop("inventoryPolicy", None)
    if product.get("ean"):
        variant["barcode"] = product["ean"]
    if _mapped(product, "variant.barcode") not in (None, ""):
        variant["barcode"] = str(_mapped(product, "variant.barcode"))
    compare_at = _mapped_any(
        product, "variant.compareAtPrice", "variant.compare_at_price"
    )
    if compare_at not in (None, "") and float(compare_at) > price:
        variant["compareAtPrice"] = f"{float(compare_at):.2f}"
    if product.get("cost_price") is not None:
        variant["inventoryItem"]["cost"] = f"{float(product['cost_price']):.2f}"
    mapped_cost = _mapped(product, "inventory.cost")
    if mapped_cost not in (None, ""):
        variant["inventoryItem"]["cost"] = f"{float(mapped_cost):.2f}"
    if existing:
        variant["id"] = existing["variant"]["id"]
    variant_metafields_by_key = {
        (item["namespace"], item["key"]): item
        for item in [
            *_mapped_metafields(product, "variant"),
            *_certilas_bundle_metafields(product),
        ]
    }
    variant_metafields = list(variant_metafields_by_key.values())
    if variant_metafields:
        variant["metafields"] = variant_metafields
    ai_tags = _effective_tags(product)
    product_vendor = str(product.get("vendor") or "").strip()
    if product_vendor.casefold() != "sp tools":
        ai_tags = [
            tag for tag in ai_tags
            if str(tag).strip().casefold() != "sp tools"
        ]
    mapped_tags = _mapped(product, "product.tags")
    if mapped_tags:
        ai_tags.extend(
            tag.strip()
            for tag in re.split(r"[,;|]", str(mapped_tags))
            if tag.strip()
        )
    result: dict[str, Any] = {
        "title": title,
        "handle": (
            existing["product"]["handle"]
            if existing else product.get("shopify_handle") or _handle(title, product["sku"])
        ),
        "descriptionHtml": description,
        "vendor": (
            _mapped(product, "product.vendor")
            or product.get("vendor")
            or "SP Tools"
        ),
        "productType": (
            _mapped_any(product, "product.productType", "product.product_type")
            or product.get("product_group_name")
            or product.get("category")
            or product.get("product_type")
            or ""
        ),
        "status": "ACTIVE" if _eligible(product) else "DRAFT",
        "tags": list(dict.fromkeys(filter(None, [
            product_vendor,
            product.get("category"), product.get("category_full"),
            *ai_tags,
        ])))[:250],
        "productOptions": [{
            "name": "Title",
            "position": 1,
            "values": [{"name": "Default Title"}],
        }],
        "variants": [variant],
    }
    try:
        filter_values = json.loads(product.get("filter_values_json") or "[]")
    except json.JSONDecodeError:
        filter_values = []
    if route.uses_certilas_filters:
        process = next((
            str(value).split(":", 1)[1].strip()
            for value in filter_values
            if str(value).startswith("Lasproces:")
        ), "")
        material = next((
            str(value).split(":", 1)[1].strip()
            for value in filter_values
            if str(value).startswith("Materiaal:")
        ), "")
        pim_metafields = [
            ("productgroep", process),
            ("uitvoering", material),
        ]
    else:
        pim_metafields = [
            ("productgroep", product.get("product_group_name")),
            ("uitvoering", product.get("execution")),
            ("filter", " | ".join(str(value) for value in filter_values if value)),
            ("subcategorie_3", product.get("subcategory_3")),
            ("subcategorie_4", product.get("subcategory_4")),
            ("subcategorie_5", product.get("subcategory_5")),
        ]
    base_metafields = [
        {
            "namespace": "custom",
            "key": key,
            "type": "single_line_text_field",
            "value": str(value)[:65535],
        }
        for key, value in pim_metafields
        if value not in (None, "")
    ]
    configured_product_metafields = _mapped_metafields(product, "product")
    merged_metafields = {
        (item["namespace"], item["key"]): item
        for item in [*base_metafields, *configured_product_metafields]
    }
    result["metafields"] = _set_product_delivery_notice(
        list(merged_metafields.values()),
        str(product.get("_delivery_time_notice") or ""),
    )
    seo_title = _mapped(product, "seo.title")
    seo_description = _mapped(product, "seo.description")
    if seo_title or seo_description:
        result["seo"] = {
            "title": str(seo_title or title)[:70],
            "description": str(seo_description or "")[:320],
        }
    if existing:
        result["id"] = existing["product"]["id"]
    existing_media = (
        ((existing or {}).get("product") or {}).get("media") or {}
    ).get("nodes") or []
    mapped_files = _mapped(product, "product.files")
    if mapped_files:
        file_urls = [
            value.strip()
            for value in re.split(r"[\n,;|]", str(mapped_files))
            if value.strip().startswith(("https://", "http://"))
        ]
        if file_urls:
            result["files"] = [
                {
                    "originalSource": _shopify_file_url(url),
                    "alt": title,
                    "contentType": "IMAGE",
                }
                for url in file_urls
            ]
    else:
        # Shopify geeft een andere CDN-URL terug dan de leveranciersbron.
        # Vergelijk daarom de werkelijke beeldinhoud: een bestaande detailfoto
        # mag nooit meer ten onrechte als de ontbrekende hoofdfoto tellen.
        missing_images = _missing_product_images(
            product["images"], existing_media
        )
        if missing_images:
            result["files"] = [
                {
                    "originalSource": _shopify_file_url(image["image_url"]),
                    "alt": image.get("alt_text") or title,
                    "contentType": "IMAGE",
                }
                for image in missing_images
            ]
    variant_media = (
        ((existing or {}).get("variant") or {}).get("media") or {}
    ).get("nodes") or []
    if product["images"] and not variant_media and not existing_media:
        best_image = product["images"][0]
        variant_file = {
            "originalSource": _shopify_file_url(best_image["image_url"]),
            "alt": best_image.get("alt_text") or title,
            "contentType": "IMAGE",
        }
        variant["file"] = variant_file
        files = result.setdefault("files", [])
        if not any(
            item.get("originalSource") == variant_file["originalSource"]
            for item in files
        ):
            files.append(variant_file)
    return result


def _grouped_product_rows(
    products: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
    *,
    group_variants: bool,
) -> list[dict[str, Any]]:
    # Een eerdere gedeeltelijke bulkbewerking kan SKU's van verschillende
    # gewenste handles tijdelijk onder hetzelfde Shopify-product hebben gezet.
    # Groepeer bestaande SKU's daarom primair op het actuele product-ID. Zo
    # wordt ieder Shopify-product hoogstens één keer per bulkbewerking gewijzigd
    # en kunnen variant-ID's niet halverwege door een tweede regel vervallen.
    handle_product_ids: dict[str, set[str]] = {}
    for product in products:
        handle = str(product.get("shopify_handle") or "").strip()
        match = existing.get(product["sku"].upper())
        if handle and match:
            handle_product_ids.setdefault(handle, set()).add(
                match["product"]["id"]
            )

    groups: dict[str, list[dict[str, Any]]] = {}
    for product in products:
        title = (
            product.get("ai_title")
            or product.get("source_title")
            or product["sku"]
        )
        handle = str(product.get("shopify_handle") or "").strip()
        match = existing.get(product["sku"].upper())
        if match:
            group_key = f"id:{match['product']['id']}"
        elif (
            group_variants
            and handle
            and len(handle_product_ids.get(handle, set())) == 1
        ):
            group_key = f"id:{next(iter(handle_product_ids[handle]))}"
        elif group_variants:
            family_key = str(
                (product.get("_family_variant_options") or {}).get("family_key")
                or ""
            ).strip()
            group_key = (
                f"family:{family_key}"
                if family_key
                else f"handle:{handle or str(title).strip().casefold()}"
            )
        else:
            # XML/API-leveranciers leveren normaal één product per SKU.
            # Nieuwe producten blijven daarom los, maar bestaande varianten
            # die al hetzelfde Shopify-product delen worden hierboven wel als
            # één productSet verwerkt. Dat voorkomt dat opeenvolgende regels
            # elkaars variant-ID verwijderen.
            group_key = f"sku:{product['sku'].strip().upper()}"
        groups.setdefault(str(group_key), []).append(product)

    rows = []
    for group in groups.values():
        first_existing = next(
            (
                existing[product["sku"].upper()]
                for product in group
                if product["sku"].upper() in existing
            ),
            None,
        )
        product_input = _input(group[0], first_existing)
        use_family_options = any(
            product.get("_family_variant_options") for product in group
        )
        if len(group) == 1 and not use_family_options:
            rows.append({"input": product_input})
            continue

        used_values: set[str] = set()
        variants = []
        option_values = []
        diameter_values: list[dict[str, str]] = []
        packaging_values: list[dict[str, str]] = []
        seen_diameters: set[str] = set()
        seen_packaging: set[str] = set()
        group_files = list(product_input.get("files") or [])
        fallback_product = next(
            (product for product in group if product.get("images")),
            None,
        )
        for product in group:
            match = existing.get(product["sku"].upper())
            variant_input = _input(product, match)
            variant = variant_input["variants"][0]
            for file_input in variant_input.get("files") or []:
                if not any(
                    item.get("originalSource")
                    == file_input.get("originalSource")
                    for item in group_files
                ):
                    group_files.append(file_input)
            existing_variant_media = (
                ((match or {}).get("variant") or {}).get("media") or {}
            ).get("nodes") or []
            if (
                "file" not in variant
                and not existing_variant_media
                and fallback_product
            ):
                fallback_image = fallback_product["images"][0]
                fallback_file = {
                    "originalSource": _shopify_file_url(fallback_image["image_url"]),
                    "alt": (
                        fallback_image.get("alt_text")
                        or product_input["title"]
                    ),
                    "contentType": "IMAGE",
                }
                variant["file"] = fallback_file
                if not any(
                    item.get("originalSource")
                    == fallback_file["originalSource"]
                    for item in group_files
                ):
                    group_files.append(fallback_file)
            family_options = product.get("_family_variant_options") or {}
            if use_family_options:
                diameter = str(
                    family_options.get("diameter") or product["sku"]
                ).strip()
                packaging = str(
                    family_options.get("packaging")
                    or "Standaardverpakking"
                ).strip()
                variant["optionValues"] = [
                    {
                        "optionName": "Diameter",
                        "name": diameter[:255],
                    },
                    {
                        "optionName": "Verpakking",
                        "name": packaging[:255],
                    },
                ]
                if diameter.casefold() not in seen_diameters:
                    diameter_values.append({"name": diameter[:255]})
                    seen_diameters.add(diameter.casefold())
                if packaging.casefold() not in seen_packaging:
                    packaging_values.append({"name": packaging[:255]})
                    seen_packaging.add(packaging.casefold())
            else:
                raw = product.get("_raw_data") or {}
                base_value = str(
                    raw.get("maat")
                    or raw.get("size")
                    or raw.get("kleur")
                    or raw.get("color")
                    or product.get("execution")
                    or product["sku"]
                ).strip()
                option_value = base_value or product["sku"]
                if option_value.casefold() in used_values:
                    option_value = f"{option_value} · {product['sku']}"
                used_values.add(option_value.casefold())
                variant["optionValues"] = [{
                    "optionName": "Maat en uitvoering",
                    "name": option_value[:255],
                }]
                option_values.append({"name": option_value[:255]})
            variants.append(variant)
        product_input["status"] = (
            "ACTIVE" if any(_eligible(product) for product in group)
            else "DRAFT"
        )
        product_input["productOptions"] = (
            [
                {
                    "name": "Diameter",
                    "position": 1,
                    "values": diameter_values,
                },
                {
                    "name": "Verpakking",
                    "position": 2,
                    "values": packaging_values,
                },
            ]
            if use_family_options
            else [{
                "name": "Maat en uitvoering",
                "position": 1,
                "values": option_values,
            }]
        )
        product_input["variants"] = variants
        if use_family_options:
            product_input["metafields"] = _set_product_verpakkingsopmerking(
                product_input.get("metafields") or [],
                _packaging_explanation([
                    product.get("_family_variant_options") or {}
                    for product in group
                ]),
            )
        if group_files:
            product_input["files"] = group_files
        rows.append({"input": product_input})
    return rows


def _price_only_variant_rows(
    products: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build minimal Shopify price/cost mutations without rewriting content.

    Deliberately update variants through ``productVariantsBulkUpdate`` instead
    of ``productSet``. Omitting fields from this input preserves families,
    options, titles, descriptions, media, tags, status and stock quantities.
    """
    variants_by_product: dict[str, list[dict[str, Any]]] = {}
    for product in products:
        match = existing.get(str(product.get("sku") or "").upper())
        price = _selling_price(product)
        if not match:
            continue
        product_id = str(match["product"]["id"])
        variant = {
            "id": str(match["variant"]["id"]),
        }
        if price is not None and price > 0:
            variant["price"] = f"{price:.2f}"
        # Een prijsupdate moet ook de actuele PIM-kostprijs doorzetten. Dit is
        # vooral belangrijk voor Certilas: cost_price bevat daar de netto
        # inkoopprijs inclusief de door ons te betalen legeringstoeslag.
        if product.get("cost_price") is not None:
            variant["inventoryItem"] = {
                "cost": f"{float(product['cost_price']):.2f}",
            }
        mapped_cost = _mapped(product, "inventory.cost")
        if mapped_cost not in (None, ""):
            variant["inventoryItem"] = {
                "cost": f"{float(mapped_cost):.2f}",
            }
        bundle_metafields = _certilas_bundle_metafields(product)
        if bundle_metafields:
            variant["metafields"] = bundle_metafields
        if len(variant) == 1:
            continue
        variants_by_product.setdefault(product_id, []).append(variant)
    return [
        {"productId": product_id, "variants": variants}
        for product_id, variants in variants_by_product.items()
    ]


def _certilas_stale_bundle_metafield_identifiers(
    products: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Return stale managed fields that must be removed before an update."""
    managed_variant_keys = {
        "verplichte_bundel", "verpakkingen_per_bundel",
        "kg_per_verpakking", "kg_per_bundel", "prijs_per_verpakking",
        "minimale_afname", "custom_minimale_afname",
    }
    identifiers: dict[tuple[str, str], dict[str, str]] = {}
    for product in products:
        if str(product.get("_supplier_slug") or "").casefold() != "certilas":
            continue
        match = existing.get(str(product.get("sku") or "").upper())
        if not match:
            continue
        desired_keys = {
            item["key"] for item in _certilas_bundle_metafields(product)
        }
        variant = match.get("variant") or {}
        for item in (variant.get("metafields") or {}).get("nodes") or []:
            key = str(item.get("key") or "")
            if key in managed_variant_keys and key not in desired_keys:
                owner_id = str(variant.get("id") or "")
                identifiers[(owner_id, key)] = {
                    "ownerId": owner_id, "namespace": "custom", "key": key,
                }
        shopify_product = match.get("product") or {}
        for item in (shopify_product.get("metafields") or {}).get("nodes") or []:
            key = str(item.get("key") or "")
            if key in {"minimale_afname", "custom_minimale_afname"}:
                owner_id = str(shopify_product.get("id") or "")
                identifiers[(owner_id, key)] = {
                    "ownerId": owner_id, "namespace": "custom", "key": key,
                }
    return [item for item in identifiers.values() if item["ownerId"]]


def _delete_certilas_stale_bundle_metafields(
    client: ShopifyClient,
    products: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
) -> int:
    identifiers = _certilas_stale_bundle_metafield_identifiers(
        products, existing
    )
    mutation = """
    mutation($metafields:[MetafieldIdentifierInput!]!){
      metafieldsDelete(metafields:$metafields){
        deletedMetafields{ownerId namespace key}
        userErrors{field message}
      }
    }
    """
    deleted = 0
    for offset in range(0, len(identifiers), 25):
        data = client.graphql(
            mutation, {"metafields": identifiers[offset:offset + 25]}
        )
        payload = data.get("metafieldsDelete") or {}
        errors = payload.get("userErrors") or []
        if errors:
            raise RuntimeError(
                "; ".join(str(error.get("message") or error) for error in errors)
            )
        deleted += len(payload.get("deletedMetafields") or [])
    return deleted


def _sync_prices_only(
    client: ShopifyClient,
    slug: str,
    products: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
    progress_callback: Progress | None,
) -> dict[str, Any]:
    deleted_stale_metafields = _delete_certilas_stale_bundle_metafields(
        client, products, existing
    )
    rows = _price_only_variant_rows(products, existing)
    _run_rows(
        client,
        rows,
        """
        mutation call($productId:ID!,$variants:[ProductVariantsBulkInput!]!){
          productVariantsBulkUpdate(productId:$productId,variants:$variants){
            product{id}
            productVariants{id sku price}
            userErrors{field message code}
          }
        }
        """,
        f"{slug}-prices-only.jsonl",
        f"pim-{slug}-prices-only-{uuid.uuid4().hex[:8]}",
        progress_callback,
        5,
        95,
        "Alleen bestaande variantprijzen bijwerken",
    )
    return {
        "ok": True,
        "mode": "prices_only",
        "products": len(rows),
        "variants": sum(len(row["variants"]) for row in rows),
        "content_fields_changed": 0,
        "deleted_stale_bundle_metafields": deleted_stale_metafields,
    }


def _run_rows(
    client: ShopifyClient,
    rows: list[dict[str, Any]],
    mutation: str,
    filename: str,
    identifier: str,
    callback: Progress | None,
    start_percent: int,
    end_percent: int,
    label: str,
) -> None:
    if not rows:
        _progress(callback, end_percent, f"{label}: niets te wijzigen")
        return
    pending_rows = rows
    for attempt in range(3):
        attempt_filename = filename if attempt == 0 else f"retry-{attempt}-{filename}"
        attempt_identifier = identifier if attempt == 0 else f"{identifier}-retry-{attempt}"
        path = _stage(client, attempt_filename, _jsonl(pending_rows))
        operation_id = _start_bulk(
            client, mutation, path, attempt_identifier[:255]
        )
        try:
            _wait_bulk(
                client, operation_id, callback, start_percent, end_percent, label
            )
            return
        except _RetryableBulkRowError as exc:
            if attempt == 2:
                raise
            pending_rows = [
                pending_rows[index] for index in exc.row_indexes
                if 0 <= index < len(pending_rows)
            ]
            if not pending_rows:
                raise
            _progress(
                callback,
                start_percent,
                f"{label}: {len(pending_rows)} tijdelijk vergrendelde "
                "producten opnieuw proberen",
            )
            time.sleep(10 * (attempt + 1))


def _selected_inventory_location(
    supplier: dict[str, Any], settings: dict[str, Any]
) -> str:
    transformations = supplier.get("source_transformations") or {}
    stock_source_field = (
        (supplier.get("field_mapping") or {}).get("stock") or ""
    )
    stock_rules = transformations.get(stock_source_field) or {}
    if not stock_rules.get("inventory_location_id"):
        stock_rules = next(
            (
                config for config in transformations.values()
                if (
                    config.get("processing_type") == "stock"
                    and config.get("inventory_location_id")
                )
            ),
            {},
        )
    return str(
        supplier.get("shopify_location_id")
        or stock_rules.get("inventory_location_id")
        or settings.get("location_id")
        or ""
    )


def _collection_inventory_policy_skus(
    client: ShopifyClient, supplier: dict[str, Any]
) -> tuple[set[str], set[str], set[str]]:
    """Resolve overrides; exclusion wins, followed by deny and continue."""
    continue_skus: set[str] = set()
    deny_skus: set[str] = set()
    excluded_skus: set[str] = set()
    rules = (supplier.get("request_options") or {}).get(
        "continue_selling_collection_rules"
    ) or []
    for rule in rules:
        collection_id = str(rule.get("collection_id") or "").strip()
        if not collection_id:
            continue
        members = collection_product_skus(client, collection_id)
        if rule.get("exclude"):
            excluded_skus.update(members)
        elif rule.get("continue_selling"):
            continue_skus.update(members)
        else:
            deny_skus.update(members)
    continue_skus.difference_update(deny_skus)
    continue_skus.difference_update(excluded_skus)
    deny_skus.difference_update(excluded_skus)
    return continue_skus, deny_skus, excluded_skus


def _apply_collection_inventory_policies(
    client: ShopifyClient, supplier: dict[str, Any]
) -> int:
    """Apply collection rules before the long supplier batch can fail."""
    rules = (supplier.get("request_options") or {}).get(
        "continue_selling_collection_rules"
    ) or []
    updated = 0
    # Apply CONTINUE first and DENY last, matching the deny-wins conflict rule.
    for rule in sorted(rules, key=lambda item: not bool(item.get("continue_selling"))):
        collection_id = str(rule.get("collection_id") or "").strip()
        if collection_id and not rule.get("exclude"):
            updated += set_collection_inventory_policy(
                client,
                collection_id,
                continue_selling=bool(rule.get("continue_selling")),
            )
    return updated


def sync_all_products(
    slug: str, progress_callback: Progress | None = None
) -> dict[str, Any]:
    settings = get_shopify_settings()
    if not settings.get("enabled"):
        raise ValueError("Shopify-synchronisatietoestemming staat uit.")
    supplier = get_supplier(slug) or {}
    location_id = _selected_inventory_location(supplier, settings)
    if not location_id:
        raise ValueError("Shopify-voorraadlocatie ontbreekt.")
    client = ShopifyClient.from_settings()
    started = datetime.now(timezone.utc)
    source = _source_products(slug)
    family_variant_options = _load_family_variant_options(slug)
    vendor = supplier.get("name") or slug
    missing_source = _missing_products(slug)
    shopify_field_mapping = supplier.get("shopify_field_mapping") or {}
    shopify_metafield_mapping = (
        supplier.get("shopify_metafield_mapping") or {}
    )
    continue_selling = bool(
        (supplier.get("request_options") or {}).get(
            "continue_selling_when_out_of_stock", False
        )
    )
    _apply_collection_inventory_policies(client, supplier)
    collection_continue_skus, collection_deny_skus, collection_excluded_skus = (
        _collection_inventory_policy_skus(client, supplier)
    )
    keep_active_with_other_stock = bool(
        (supplier.get("request_options") or {}).get(
            "draft_only_when_no_location_stock", False
        )
    )
    keep_active_when_out_of_stock = bool(
        (supplier.get("request_options") or {}).get(
            "keep_active_when_out_of_stock", False
        )
    )
    for product in source:
        try:
            product["_raw_data"] = json.loads(product.get("raw_data_json") or "{}")
        except json.JSONDecodeError:
            product["_raw_data"] = {}
        product["_supplier_slug"] = slug
        product["_supplier_slug"] = slug
        product["_shopify_field_mapping"] = shopify_field_mapping
        product["_shopify_metafield_mapping"] = (
            shopify_metafield_mapping
        )
        normalized_sku = str(product.get("sku") or "").strip().upper()
        inventory_policy_excluded = normalized_sku in collection_excluded_skus
        product_continue_selling = (
            False if normalized_sku in collection_deny_skus
            else True if normalized_sku in collection_continue_skus
            else continue_selling
        )
        if inventory_policy_excluded:
            product["_exclude_inventory_policy"] = True
            product["_delivery_time_notice"] = ""
        else:
            product["inventory_policy"] = (
                "continue" if product_continue_selling else "deny"
            )
            product["_delivery_time_notice"] = _configured_delivery_time_notice(
                supplier, product_continue_selling
            )
        product["_keep_active_when_out_of_stock"] = (
            keep_active_when_out_of_stock
            or _stock_stays_active_at_zero(
                product["_raw_data"].get(
                    (supplier.get("field_mapping") or {}).get("stock") or ""
                ),
                supplier,
            )
        )
        product["_family_variant_options"] = family_variant_options.get(
            product["sku"].upper(), {}
        )
        _apply_canonical_family_content(
            product, product["_family_variant_options"]
        )
    all_source = source
    previous_hashes = _load_sync_hashes(slug)
    selected_source, current_hashes, detected_changes = (
        _select_changed_products(
            all_source, supplier, previous_hashes
        )
    )
    changed_only = bool(supplier.get("sync_changed_only"))
    if changed_only:
        source = selected_source
        change_details = detected_changes
        if (supplier.get("request_options") or {}).get(
            "sync_family_on_change"
        ):
            source, family_details = _expand_selected_product_families(
                slug, all_source, source
            )
            change_details = [*change_details, *family_details]
    else:
        change_details = [
            {"sku": product["sku"], "reason": "Volledige synchronisatie"}
            for product in all_source
        ]
    preferred_vendor_duplicates: list[dict[str, str]] = []
    if slug == "valkenpower":
        _progress(
            progress_callback, 2,
            "Rhodius-voorrang op gelijke SKU's controleren…",
        )
        rhodius_products = _shopify_products(client, "Rhodius")
        rhodius_by_sku = {
            str(match["variant"].get("sku") or "").strip().upper(): {
                "sku": str(match["variant"].get("sku") or ""),
                "product_id": str(match["product"].get("id") or ""),
            }
            for match in rhodius_products.values()
            if str(match["variant"].get("sku") or "").strip()
        }
        preferred_vendor_duplicates = [
            {
                "sku": str(product.get("sku") or ""),
                "matched_on": "exact_supplier_sku",
                "preferred_vendor": "Rhodius",
                "preferred_sku": rhodius_by_sku[
                    str(
                        product.get("supplier_sku")
                        or product.get("sku")
                        or ""
                    ).strip().upper().removeprefix("VP-")
                ]["sku"],
                "preferred_product_id": rhodius_by_sku[
                    str(
                        product.get("supplier_sku")
                        or product.get("sku")
                        or ""
                    ).strip().upper().removeprefix("VP-")
                ]["product_id"],
            }
            for product in source
            if str(
                product.get("supplier_sku")
                or product.get("sku")
                or ""
            ).strip().upper().removeprefix("VP-") in rhodius_by_sku
        ]
        duplicate_skus = {
            item["sku"].upper() for item in preferred_vendor_duplicates
        }
        source = [
            product for product in source
            if str(product.get("sku") or "").upper() not in duplicate_skus
        ]
    priced = [product for product in source if _has_valid_price(product)]
    unpriced = [product for product in source if not _has_valid_price(product)]
    _progress(progress_callback, 2, "Bestaande Shopify-SKU’s inventariseren…")
    existing = _shopify_products(
        client, vendor, slug, progress_callback=progress_callback
    )
    if (supplier.get("request_options") or {}).get("shopify_prices_only"):
        result = _sync_prices_only(
            client, slug, source, existing, progress_callback
        )
        _save_sync_hashes(slug, current_hashes, {
            product["sku"].upper() for product in all_source
        })
        _progress(
            progress_callback, 100,
            "Prijzen bijgewerkt; families, media en teksten zijn vergrendeld",
        )
        return result
    _delete_certilas_stale_bundle_metafields(client, source, existing)
    _validate_existing_family_layout(source, existing)
    inventory_ids_with_other_stock: set[str] = set()
    if keep_active_with_other_stock:
        relevant_inventory_ids = [
            match["variant"]["inventoryItem"]["id"]
            for product in source
            if not product.get("available")
            for match in [existing.get(product["sku"].upper())]
            if match
        ]
        inventory_ids_with_other_stock = _inventory_items_with_stock_elsewhere(
            client, relevant_inventory_ids, location_id
    )
    for product in source:
        match = existing.get(product["sku"].upper())
        media = (
            ((match or {}).get("product") or {}).get("media") or {}
        ).get("nodes") or []
        product["_has_shopify_image"] = any(
            not _shopify_media_is_rejected(item) for item in media
        )
        product["_has_other_shopify_stock"] = (
            keep_active_with_other_stock
            and bool(match)
            and match["variant"]["inventoryItem"]["id"]
            in inventory_ids_with_other_stock
        )
    new_product_policy = (
        supplier.get("sync_new_product_policy") or "existing_only"
    )
    existing_priced = [
        product for product in priced
        if product["sku"].upper() in existing
    ]
    new_priced = [
        product for product in priced
        if product["sku"].upper() not in existing
    ]
    complete_new = (
        [
            product for product in new_priced
            if _complete_for_new_product(product)
        ]
        if new_product_policy == "add_complete"
        else []
    )
    skipped_new_incomplete = [
        product for product in new_priced
        if product not in complete_new
    ]
    priced_to_sync = [*existing_priced, *complete_new]
    # Prijsloze artikelen worden nooit nieuw aangemaakt. Als een SKU al in
    # Shopify staat, wordt deze wel meegenomen zodat productSet hem veilig op
    # Concept zet en hij niet verkoopbaar blijft.
    existing_unpriced = [
        product for product in unpriced
        if product["sku"].upper() in existing
    ]
    directly_incomplete = [
        product for product in priced_to_sync
        if quality_policy_for(
            product.get("vendor"), supplier_slug=product.get("_supplier_slug")
        ).isolate_incomplete_content
        and _common_product_content_errors(product)
    ]
    incomplete_family_keys = {
        str((product.get("_family_variant_options") or {}).get("family_key") or "")
        for product in directly_incomplete
        if (product.get("_family_variant_options") or {}).get("family_key")
    }
    isolated_incomplete_content = [
        product for product in priced_to_sync
        if product in directly_incomplete
        or str(
            (product.get("_family_variant_options") or {}).get("family_key") or ""
        ) in incomplete_family_keys
    ]
    priced_to_sync = [
        product for product in priced_to_sync
        if product not in isolated_incomplete_content
    ]
    # SP Tools levert incidenteel zelf geen productbeeld. Zo'n bestaand artikel
    # blijft Concept, maar blokkeert niet de volledige leveranciersbatch.
    isolated_missing_image = [
        product for product in priced_to_sync
        if quality_policy_for(
            product.get("vendor"), supplier_slug=product.get("_supplier_slug")
        ).isolate_missing_image
        and not (product.get("images") or product.get("_has_shopify_image"))
    ]
    priced_to_sync = [
        product for product in priced_to_sync
        if product not in isolated_missing_image
    ]
    eligible = [
        product for product in priced_to_sync if _eligible(product)
    ]
    unavailable = [
        product for product in priced_to_sync if not _eligible(product)
    ]
    _validate_product_content_gate(priced_to_sync, all_source)
    sync_source = list(priced_to_sync)
    excluded = [
        *unavailable, *existing_unpriced, *isolated_missing_image,
        *isolated_incomplete_content,
    ]
    image_urls: list[str] = []
    for product in sync_source:
        image_urls.extend(
            str(image.get("image_url") or "")
            for image in product.get("images") or []
        )
        match = existing.get(str(product.get("sku") or "").upper())
        image_urls.extend(
            str((media.get("image") or {}).get("url") or "")
            for media in (
                (((match or {}).get("product") or {}).get("media") or {})
                .get("nodes") or []
            )
        )
    _precache_visual_image_hashes(image_urls, progress_callback)
    group_variants = "upload" in supplier.get("source_type", "")
    rows = _grouped_product_rows(
        sync_source, existing, group_variants=group_variants
    )
    desired_status = supplier.get("sync_product_status") or "active"
    publish_all = bool(supplier.get("sync_publish_all", 1))
    # Bestaande actieve producten mogen tijdens een update nooit tijdelijk
    # verdwijnen. Alleen nieuwe en reeds bestaande conceptproducten wachten als
    # Concept op de live Shopify-controle.
    existing_status_by_product_id = {
        str(match["product"].get("id") or ""): str(
            match["product"].get("status") or ""
        )
        for match in existing.values()
    }
    for row in rows:
        row["input"]["status"] = _staged_product_status(
            str(row["input"].get("id") or ""),
            existing_status_by_product_id,
        )
    protected_draft_products = [
        *existing_unpriced,
        *isolated_missing_image,
        *isolated_incomplete_content,
    ]
    unpriced_draft_rows = [
        {"input": {"id": existing[product["sku"].upper()]["product"]["id"],
                   "status": "DRAFT"}}
        for product in protected_draft_products
        if product["sku"].upper() in existing
    ]
    _run_rows(
        client, unpriced_draft_rows,
        """mutation call($input:ProductSetInput!){
          productSet(synchronous:true,input:$input){
            product{id status} userErrors{field message code}
          }}""",
        f"{slug}-unpriced-drafts.jsonl",
        f"pim-{slug}-unpriced-drafts-{uuid.uuid4().hex[:8]}",
        progress_callback, 3, 5,
        "Prijsloze bestaande producten veilig op Concept zetten",
    )
    _run_rows(
        client,
        rows,
        """
        mutation call($input:ProductSetInput!){
          productSet(synchronous:true,input:$input){
            product{id status}
            userErrors{field message code}
          }
        }
        """,
        f"{slug}-products.jsonl",
        f"pim-{slug}-products-{uuid.uuid4().hex[:8]}",
        progress_callback,
        5,
        58,
        "Producten naar Shopify",
    )
    refreshed = _shopify_products(client, vendor, slug)
    missing = [
        product["sku"] for product in sync_source
        if product["sku"].upper() not in refreshed
    ]
    if missing:
        raise RuntimeError(f"Shopify mist na import SKU’s: {missing[:20]}")
    (
        refreshed,
        repaired_media_products,
        incomplete_media_products,
    ) = _ensure_shopify_media_complete(
        client, vendor, sync_source, refreshed, progress_callback,
        supplier_slug=slug,
    )
    incomplete_media_product_ids = {
        item["product_id"] for item in incomplete_media_products
    }
    media_blocked_product_ids = {
        item["product_id"] for item in incomplete_media_products
        if int(item.get("ready") or 0) == 0
    }
    incomplete_media_skus = {
        str(sku).upper()
        for item in incomplete_media_products
        for sku in item.get("skus") or []
    }
    live_errors = []
    for product in sync_source:
        match = refreshed[product["sku"].upper()]
        live_product = match["product"]
        live_variant = match["variant"]
        if float(live_variant.get("price") or 0) <= 0:
            live_errors.append(f"{product['sku']}: Shopify-prijs is nul")
        product_media = (live_product.get("media") or {}).get("nodes") or []
        variant_media = (live_variant.get("media") or {}).get("nodes") or []
        if (
            not variant_media and not product_media
            and live_product["id"] not in media_blocked_product_ids
        ):
            live_errors.append(f"{product['sku']}: Shopify-media ontbreekt")
        if not _description_meets_quality(
            product, str(live_product.get("descriptionHtml") or "")
        ):
            live_errors.append(f"{product['sku']}: Shopify-tekst ontbreekt")
        if not _tags_meet_quality(product, live_product.get("tags") or []):
            live_errors.append(f"{product['sku']}: Shopify-tags ontbreken")
        expected_status = _staged_product_status(
            str(live_product.get("id") or ""),
            existing_status_by_product_id,
        )
        if live_product.get("status") != expected_status:
            live_errors.append(
                f"{product['sku']}: veiligheidsstatus is niet {expected_status}"
            )
    if live_errors:
        raise RuntimeError(
            "Shopify-controle na conceptupload mislukt:\n- "
            + "\n- ".join(live_errors[:100])
        )

    products_by_id = {
        match["product"]["id"]: match["product"]
        for match in refreshed.values()
    }
    source_by_product_id = {
        refreshed[product["sku"].upper()]["product"]["id"]: product
        for product in sync_source
    }
    location_cleanup_rows = []
    for product_id, shopify_product in products_by_id.items():
        source_product = source_by_product_id.get(product_id)
        if not source_product:
            continue
        try:
            expected_filters = json.loads(
                source_product.get("filter_values_json") or "[]"
            )
        except json.JSONDecodeError:
            expected_filters = []
        expected_filter_value = " | ".join(
            str(value) for value in expected_filters if value
        )
        current_metafields = {
            item.get("key"): item.get("value")
            for item in (
                (shopify_product.get("metafields") or {}).get("nodes") or []
            )
        }
        # Herstel uitsluitend waarden die aantoonbaar door de eerdere foutieve
        # PIM-koppeling zijn geschreven. Echte magazijnlocaties blijven staan.
        if (
            expected_filter_value
            and current_metafields.get("locatie") == expected_filter_value
        ):
            location_cleanup_rows.append({
                "metafields": [{
                    "ownerId": product_id,
                    "namespace": "custom",
                    "key": "locatie",
                }]
            })
        if (
            supplier_route(slug).uses_certilas_filters
            and current_metafields.get("filter") is not None
        ):
            location_cleanup_rows.append({
                "metafields": [{
                    "ownerId": product_id,
                    "namespace": "custom",
                    "key": "filter",
                }]
            })
    _run_rows(
        client,
        location_cleanup_rows,
        """
        mutation call($metafields:[MetafieldIdentifierInput!]!){
          metafieldsDelete(metafields:$metafields){
            deletedMetafields{ownerId namespace key}
            userErrors{field message}
          }
        }
        """,
        f"{slug}-location-repair.jsonl",
        f"pim-{slug}-location-repair-{uuid.uuid4().hex[:8]}",
        progress_callback,
        57,
        58,
        "Foutieve magazijnlocaties herstellen",
    )
    media_cleanup_rows = []
    media_reorder_rows = []
    rejected_media_count = 0
    for product_id, product in products_by_id.items():
        if product_id not in source_by_product_id:
            continue
        media = (product.get("media") or {}).get("nodes") or []
        rejected = [
            item["id"] for item in media
            if item.get("id") and _shopify_media_is_rejected(item)
        ]
        rejected = list(dict.fromkeys([
            *rejected, *_duplicate_media_ids(media),
        ]))
        accepted = [
            item for item in media
            if item.get("id") and item["id"] not in rejected
        ]
        # Nooit de laatste productfoto verwijderen. Eerst moet er aantoonbaar
        # minstens één geaccepteerde Shopify-foto overblijven.
        if rejected and accepted:
            media_cleanup_rows.append({
                "productId": product["id"],
                "mediaIds": rejected,
            })
            rejected_media_count += len(rejected)
        if media:
            source_images = (
                source_by_product_id.get(product_id, {}).get("images") or []
            )
            primary_hash = (
                _visual_image_hash(str(source_images[0].get("image_url") or ""))
                if source_images else None
            )
            primary_media_id = next((
                str(item.get("id")) for item in media
                if primary_hash is not None
                and item.get("id") not in rejected
                and (shopify_hash := _visual_image_hash(
                    str((item.get("image") or {}).get("url") or "")
                )) is not None
                and _visual_hash_distance(primary_hash, shopify_hash) <= 6
            ), "")
            if primary_media_id and primary_media_id != str(media[0].get("id") or ""):
                media_reorder_rows.append({
                    "id": product_id,
                    "moves": [{"id": primary_media_id, "newPosition": "0"}],
                })
    _run_rows(
        client,
        media_cleanup_rows,
        """
        mutation call($productId:ID!,$mediaIds:[ID!]!){
          productDeleteMedia(productId:$productId,mediaIds:$mediaIds){
            deletedMediaIds
            mediaUserErrors{field message code}
          }
        }
        """,
        f"{slug}-poor-media.jsonl",
        f"pim-{slug}-poor-media-{uuid.uuid4().hex[:8]}",
        progress_callback,
        58,
        59,
        "Afgekeurde miniaturen verwijderen",
    )
    _run_rows(
        client,
        media_reorder_rows,
        """
        mutation call($id:ID!,$moves:[MoveInput!]!){
          productReorderMedia(id:$id,moves:$moves){
            job{id done}
            mediaUserErrors{field message code}
          }
        }
        """,
        f"{slug}-media-order.jsonl",
        f"pim-{slug}-media-order-{uuid.uuid4().hex[:8]}",
        progress_callback,
        59,
        60,
        "Officiële hoofdfoto's vooraan plaatsen",
    )

    missing_matches: dict[str, dict[str, Any]] = {}
    for product in missing_source:
        match = refreshed.get(product["sku"].upper())
        if match:
            missing_matches[match["product"]["id"]] = {
                **product,
                "shopify_product_id": match["product"]["id"],
                "shopify_inventory_item_id": match["variant"][
                    "inventoryItem"
                ]["id"],
            }
    missing_stocked_at_weldingshop = _inventory_items_with_stock_at_location(
        client,
        [
            product["shopify_inventory_item_id"]
            for product in missing_matches.values()
        ],
        location_id,
    )
    missing_matches = {
        product_id: product
        for product_id, product in missing_matches.items()
        if product["shopify_inventory_item_id"]
        not in missing_stocked_at_weldingshop
    }
    draft_missing_enabled = bool(supplier.get("missing_products_to_draft", 1))
    delete_missing_enabled = (
        draft_missing_enabled
        and bool(supplier.get("delete_missing_products", 0))
    )
    draft_rows = (
        [
            {
                "input": {
                    "id": product["shopify_product_id"],
                    "status": "DRAFT",
                }
            }
            for product in missing_matches.values()
        ]
        if draft_missing_enabled else []
    )
    _run_rows(
        client,
        draft_rows,
        """
        mutation call($input:ProductSetInput!){
          productSet(synchronous:true,input:$input){
            product{id status}
            userErrors{field message code}
          }
        }
        """,
        f"{slug}-missing-drafts.jsonl",
        f"pim-{slug}-missing-drafts-{uuid.uuid4().hex[:8]}",
        progress_callback,
        59,
        60,
        "Ontbrekende producten op Concept zetten",
    )

    retention_months = int(supplier.get("delete_missing_after_months") or 2)
    cutoff = _months_ago(started, retention_months)
    expired = []
    if delete_missing_enabled:
        for product in missing_matches.values():
            draft_since = product.get("shopify_draft_since")
            if not draft_since:
                continue
            try:
                started_missing = datetime.fromisoformat(draft_since)
            except ValueError:
                continue
            if started_missing.tzinfo is None:
                started_missing = started_missing.replace(tzinfo=timezone.utc)
            if started_missing <= cutoff:
                expired.append(product)
    delete_rows = [
        {"input": {"id": product["shopify_product_id"]}}
        for product in expired
    ]
    _run_rows(
        client,
        delete_rows,
        """
        mutation call($input:ProductDeleteInput!){
          productDelete(input:$input){
            deletedProductId
            userErrors{field message code}
          }
        }
        """,
        f"{slug}-missing-deletes.jsonl",
        f"pim-{slug}-missing-delete-{uuid.uuid4().hex[:8]}",
        progress_callback,
        60,
        61,
        "Verlopen conceptproducten verwijderen",
    )

    _progress(progress_callback, 60, "Voorraadlocatie activeren…")
    activation_rows = [
        {
            "inventoryItemId": refreshed[product["sku"].upper()]["variant"]["inventoryItem"]["id"],
            "inventoryItemUpdates": [{"locationId": location_id, "activate": True}],
        }
        for product in sync_source
    ]
    _run_rows(
        client,
        activation_rows,
        """
        mutation call(
          $inventoryItemId:ID!,
          $inventoryItemUpdates:[InventoryBulkToggleActivationInput!]!
        ){
          inventoryBulkToggleActivation(
            inventoryItemId:$inventoryItemId,
            inventoryItemUpdates:$inventoryItemUpdates
          ){userErrors{field message code}}
        }
        """,
        f"{slug}-activation.jsonl",
        f"pim-{slug}-activate-{uuid.uuid4().hex[:8]}",
        progress_callback,
        60,
        70,
        "Voorraadlocatie koppelen",
    )

    inventory_errors = []
    for index in range(0, len(sync_source), 100):
        chunk = sync_source[index:index + 100]
        quantities = [
            {
                "inventoryItemId": refreshed[product["sku"].upper()]["variant"]["inventoryItem"]["id"],
                "locationId": location_id,
                "quantity": int(
                    _mapped_any(
                        product,
                        "variant.inventoryQuantities",
                        "inventory.quantity",
                    )
                    or product.get("stock_quantity")
                    or 0
                ),
                # Shopify 2026-07 vereist dat changeFromQuantity expliciet
                # aanwezig is. null betekent: absolute bronwaarde toepassen
                # zonder een verouderde vergelijkingswaarde te veronderstellen.
                "changeFromQuantity": None,
            }
            for product in chunk
        ]
        result = client.graphql(
            """
            mutation(
              $input:InventorySetQuantitiesInput!,
              $idempotencyKey:String!
            ){
              inventorySetQuantities(input:$input)
              @idempotent(key:$idempotencyKey){
                userErrors{field message code}
              }
            }
            """,
            {
                "idempotencyKey": str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"pim://weldingshop/{slug}/"
                        f"{started:%Y%m%dT%H%M%SZ}/{index // 100 + 1}"
                    ),
                )),
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "referenceDocumentUri": (
                        f"pim://weldingshop/{slug}/"
                        f"{started:%Y%m%dT%H%M%SZ}/{index // 100 + 1}"
                    ),
                    "quantities": quantities,
                }
            },
        )["inventorySetQuantities"]
        inventory_errors.extend(result["userErrors"])
        percent = 70 + int(
            12 * min(index + len(chunk), len(sync_source))
            / max(len(sync_source), 1)
        )
        _progress(
            progress_callback,
            percent,
            "Voorraad: "
            f"{min(index + len(chunk), len(sync_source))} "
            f"van {len(sync_source)}",
        )
    if inventory_errors:
        raise RuntimeError(
            "Voorraadfouten: " + json.dumps(inventory_errors[:20], ensure_ascii=False)
        )

    publications = client.graphql(
        "query{publications(first:100){nodes{id name}}}"
    )["publications"]["nodes"]
    publication_input = [
        {"publicationId": publication["id"]} for publication in publications
    ]
    refreshed_product_ids = {
        product["sku"].upper():
        refreshed[product["sku"].upper()]["product"]["id"]
        for product in sync_source
    }
    active_product_ids = (
        {
            refreshed_product_ids[product["sku"].upper()]
            for product in eligible
            if refreshed_product_ids[product["sku"].upper()]
            not in media_blocked_product_ids
        }
        if desired_status == "active" else set()
    )
    activation_status_rows = [
        {"input": {"id": product_id, "status": "ACTIVE"}}
        for product_id in sorted(active_product_ids)
    ]
    _run_rows(
        client, activation_status_rows,
        """mutation call($input:ProductSetInput!){
          productSet(synchronous:true,input:$input){
            product{id status} userErrors{field message code}
          }}""",
        f"{slug}-verified-active.jsonl",
        f"pim-{slug}-verified-active-{uuid.uuid4().hex[:8]}",
        progress_callback, 82, 83,
        "Gecontroleerde producten activeren",
    )
    publish_rows = [
        {
            "id": product_id,
            "input": publication_input,
        }
        for product_id in sorted(active_product_ids)
    ] if publish_all else []
    unpublish_rows = [
        {
            "id": product_id,
            "input": publication_input,
        }
        for product_id in sorted(set(refreshed_product_ids.values()))
    ] if not publish_all else []
    _run_rows(
        client,
        unpublish_rows,
        """
        mutation call($id:ID!,$input:[PublicationInput!]!){
          publishableUnpublish(id:$id,input:$input){
            userErrors{field message}
          }
        }
        """,
        f"{slug}-unpublish.jsonl",
        f"pim-{slug}-unpublish-{uuid.uuid4().hex[:8]}",
        progress_callback,
        83,
        90,
        "Alle verkoopkanalen uitschakelen",
    )
    _run_rows(
        client,
        publish_rows,
        """
        mutation call($id:ID!,$input:[PublicationInput!]!){
          publishablePublish(id:$id,input:$input){
            userErrors{field message}
          }
        }
        """,
        f"{slug}-publications.jsonl",
        f"pim-{slug}-publish-{uuid.uuid4().hex[:8]}",
        progress_callback,
        90,
        98,
        "Verkoopkanalen activeren",
    )
    final = _shopify_products(client, vendor, slug)
    final_products = {
        match["product"]["id"]: match["product"]
        for match in final.values()
    }
    synced_product_ids = set(refreshed_product_ids.values())
    preserved_active_product_ids = {
        product_id for product_id in synced_product_ids
        if existing_status_by_product_id.get(product_id, "").upper() == "ACTIVE"
    }
    expected_active_ids = active_product_ids | preserved_active_product_ids
    expected_draft_ids = synced_product_ids - expected_active_ids
    active_count = sum(
        final_products[product_id]["status"] == "ACTIVE"
        for product_id in expected_active_ids
    )
    draft_count = sum(
        final_products[product_id]["status"] == "DRAFT"
        for product_id in expected_draft_ids
    )
    if (
        active_count != len(expected_active_ids)
        or draft_count != len(expected_draft_ids)
    ):
        raise RuntimeError(
            "Eindcontrole status wijkt af: actief "
            f"{active_count}/{len(expected_active_ids)}, concept "
            f"{draft_count}/{len(expected_draft_ids)}"
        )
    _save_sync_hashes(
        slug,
        {
            product["sku"].upper(): current_hashes[
                product["sku"].upper()
            ]
            for product in source
            if product["sku"].upper() not in incomplete_media_skus
        },
        {product["sku"].upper() for product in all_source},
    )
    result = {
        "products": len(all_source),
        "change_detection": {
            "mode": "changed_only" if changed_only else "full",
            "total": len(all_source),
            "selected": len(source),
            "unchanged_skipped": len(all_source) - len(source),
            "details": change_details,
        },
        "uploaded_or_updated": len(sync_source),
        "new_product_policy": new_product_policy,
        "new_complete_added": len(complete_new),
        "new_skipped_by_policy_or_incomplete": len(
            skipped_new_incomplete
        ),
        "preferred_vendor_duplicates_skipped": preferred_vendor_duplicates,
        "skipped_without_price": len(unpriced) - len(existing_unpriced),
        "existing_without_price_set_to_draft": len(existing_unpriced),
        "supplier_incomplete_set_to_draft": len(isolated_missing_image),
        "supplier_incomplete_skipped": len(isolated_incomplete_content),
        "without_approved_image": sum(
            not bool(
                product.get("images")
                or product.get("_has_shopify_image")
            )
            for product in priced
        ),
        "active": active_count,
        "draft": draft_count,
        "preserved_active_during_sync": len(
            preserved_active_product_ids - active_product_ids
        ),
        "missing_in_source": len(missing_source),
        "missing_set_to_draft": len(draft_rows),
        "missing_deleted": len(delete_rows),
        "missing_retention_months": retention_months,
        "rejected_shopify_images": rejected_media_count,
        "repaired_incomplete_media_products": repaired_media_products,
        "incomplete_media_products": incomplete_media_products,
        "incomplete_media_product_count": len(
            incomplete_media_product_ids
        ),
        "media_blocked_draft_count": len(media_blocked_product_ids),
        "repaired_location_metafields": len(location_cleanup_rows),
        "publications": len(publications),
        "product_status_setting": desired_status,
        "publish_all_channels": publish_all,
        "location_id": location_id,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    output = Path(__file__).resolve().parents[2] / "data/output/shopify"
    output.mkdir(parents=True, exist_ok=True)
    report = output / f"{slug}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    result["report"] = str(report)
    _progress(progress_callback, 100, "Shopify-synchronisatie voltooid")
    return result


def upload_test_product(
    slug: str,
    sku: str,
    *,
    description_html: str | None = None,
) -> dict[str, Any]:
    """Create or update one isolated draft product for visual testing."""
    settings = get_shopify_settings()
    if not settings.get("enabled"):
        raise ValueError("Shopify-synchronisatietoestemming staat uit.")
    supplier = get_supplier(slug) or {}
    source = next(
        (
            product for product in _source_products(slug)
            if product["sku"].strip().upper() == sku.strip().upper()
        ),
        None,
    )
    if not source:
        raise ValueError(f"Product {sku} staat niet actueel in de PIM.")

    try:
        source["_raw_data"] = json.loads(
            source.get("raw_data_json") or "{}"
        )
    except json.JSONDecodeError:
        source["_raw_data"] = {}
    source["_raw_data"] = apply_source_transformations(
        source["_raw_data"],
        supplier.get("source_transformations") or {},
    )
    pim_mapping = supplier.get("field_mapping") or {}
    mapped_title = source["_raw_data"].get(
        pim_mapping.get("title") or ""
    )
    mapped_description = source["_raw_data"].get(
        pim_mapping.get("description") or ""
    )
    if mapped_title not in (None, ""):
        source["source_title"] = str(mapped_title)
    if mapped_description not in (None, ""):
        source["source_description"] = str(mapped_description)
        source["html_description"] = str(mapped_description)
    source["_shopify_field_mapping"] = (
        supplier.get("shopify_field_mapping") or {}
    )
    source["_shopify_metafield_mapping"] = (
        supplier.get("shopify_metafield_mapping") or {}
    )
    continue_selling = bool(
        (supplier.get("request_options") or {}).get(
            "continue_selling_when_out_of_stock", False
        )
    )
    source["inventory_policy"] = (
        "continue" if continue_selling else "deny"
    )
    source["_delivery_time_notice"] = _configured_delivery_time_notice(
        supplier, continue_selling
    )
    if description_html is not None:
        source["html_description"] = description_html

    original_sku = source["sku"]
    test_sku = f"TEST-{original_sku}"[:255]
    source["sku"] = test_sku
    source["source_title"] = (
        f"[TEST] {source.get('source_title') or source.get('source_description') or original_sku}"
    )[:255]
    source["ai_title"] = ""
    source["shopify_handle"] = (
        f"test-{source.get('shopify_handle') or _handle('', original_sku)}"
    )[:255]
    try:
        tags = json.loads(source.get("ai_tags_json") or "[]")
    except json.JSONDecodeError:
        tags = []
    source["ai_tags_json"] = json.dumps([
        *tags, "testproduct_verwijder_deze",
    ])

    client = ShopifyClient.from_settings()
    existing_variant = client.find_variant_by_sku(test_sku)
    existing = (
        {
            "product": {
                **(existing_variant.get("product") or {}),
                "media": {"nodes": []},
            },
            "variant": {
                **existing_variant,
                "media": {"nodes": []},
            },
        }
        if existing_variant else None
    )
    product_input = _input(source, existing)
    product_input["title"] = source["source_title"]
    product_input["handle"] = (
        existing["product"]["handle"]
        if existing else source["shopify_handle"]
    )
    product_input["status"] = "DRAFT"
    product_input["tags"] = list(dict.fromkeys([
        *(product_input.get("tags") or []),
        "testproduct_verwijder_deze",
    ]))
    product_input["variants"][0]["sku"] = test_sku

    data = client.graphql(
        """
        mutation TestProduct($input:ProductSetInput!){
          productSet(synchronous:true,input:$input){
            product{id title handle status tags}
            userErrors{field message code}
          }
        }
        """,
        {"input": product_input},
    )
    payload = data.get("productSet") or {}
    if payload.get("userErrors"):
        raise RuntimeError(
            "Shopify weigerde het testproduct: "
            + json.dumps(payload["userErrors"], ensure_ascii=False)
        )
    product = payload.get("product") or {}
    if not product.get("id"):
        raise RuntimeError("Shopify gaf geen product-ID terug.")
    numeric_id = str(product["id"]).rsplit("/", 1)[-1]
    return {
        "id": product["id"],
        "title": product.get("title"),
        "handle": product.get("handle"),
        "status": product.get("status"),
        "sku": test_sku,
        "tag": "testproduct_verwijder_deze",
        "admin_url": (
            f"https://{client.shop_domain}/admin/products/{numeric_id}"
        ),
    }


def upload_pim_product_draft(slug: str, sku: str) -> dict[str, Any]:
    """Create/update the real SKU in Shopify as a draft, including PIM media."""
    settings = get_shopify_settings()
    if not settings.get("enabled"):
        raise ValueError("Shopify-synchronisatietoestemming staat uit.")
    supplier = get_supplier(slug) or {}
    source = next(
        (item for item in _source_products(slug)
         if str(item.get("sku") or "").strip().upper() == sku.strip().upper()),
        None,
    )
    if not source:
        raise ValueError(f"Product {sku} staat niet actueel in de PIM van deze leverancier.")
    try:
        source["_raw_data"] = json.loads(source.get("raw_data_json") or "{}")
    except json.JSONDecodeError:
        source["_raw_data"] = {}
    source["_shopify_field_mapping"] = supplier.get("shopify_field_mapping") or {}
    source["_shopify_metafield_mapping"] = supplier.get("shopify_metafield_mapping") or {}
    source["inventory_policy"] = "continue"
    source["_supplier_slug"] = slug

    client = ShopifyClient.from_settings()
    existing_variant = client.find_variant_by_sku(source["sku"])
    if existing_variant:
        existing_vendor = str((existing_variant.get("product") or {}).get("vendor") or "").strip()
        allowed_vendors = {
            str(supplier.get("name") or "").strip().casefold(),
            *(name.casefold() for name in supplier_route(slug).shopify_vendor_names),
        }
        if existing_vendor and existing_vendor.casefold() not in allowed_vendors:
            raise ValueError(
                f"SKU {sku} bestaat al bij Shopify-leverancier {existing_vendor}; "
                "de leveranciersroutes blijven gescheiden."
            )
    product = (existing_variant or {}).get("product") or {}
    variant_title = str((existing_variant or {}).get("title") or "").strip()
    selected_options = (existing_variant or {}).get("selectedOptions") or []
    product_options = product.get("options") or []
    is_variant = bool(existing_variant) and (
        len(((product.get("variants") or {}).get("nodes") or [])) > 1
        or any(
            str(option.get("name") or "").strip().casefold() != "title"
            or str(option.get("value") or "").strip().casefold()
            != "default title"
            for option in selected_options
        )
        or variant_title.casefold() not in {"", "default title"}
        or any(
            str(option.get("name") or "").strip().casefold() != "title"
            for option in product_options
        )
    )
    existing = (
        {
            "product": {
                **(existing_variant.get("product") or {}),
                "media": (existing_variant.get("product") or {}).get("media") or {"nodes": []},
            },
            "variant": {
                **existing_variant,
                "media": existing_variant.get("media") or {"nodes": []},
            },
        }
        if existing_variant else None
    )
    product_input = _input(source, existing)
    if is_variant:
        variant_input = product_input["variants"][0]
        variant_input.pop("optionValues", None)
        data = client.graphql(
            """mutation SavePimVariant(
              $productId:ID!,$variants:[ProductVariantsBulkInput!]!
            ){
              productVariantsBulkUpdate(
                productId:$productId,variants:$variants
              ){
                productVariants{id sku}
                userErrors{field message code}
              }
            }""",
            {
                "productId": existing_variant["product"]["id"],
                "variants": [variant_input],
            },
        )
        payload = data.get("productVariantsBulkUpdate") or {}
        if payload.get("userErrors"):
            raise RuntimeError(
                "Shopify weigerde de variantupdate: "
                + json.dumps(payload["userErrors"], ensure_ascii=False)
            )
        variants = payload.get("productVariants") or []
        if not variants:
            raise RuntimeError("Shopify gaf geen bijgewerkte variant terug.")
        product = existing_variant["product"]
        numeric_id = str(product["id"]).rsplit("/", 1)[-1]
        with sqlite3.connect(supplier_database_path(slug)) as connection:
            connection.execute(
                "UPDATE products SET shopify_handle=?,shopify_status=?,updated_at=? WHERE sku=?",
                (
                    product.get("handle") or "",
                    str(product.get("status") or "").casefold(),
                    datetime.now(timezone.utc).isoformat(),
                    source["sku"],
                ),
            )
        return {
            "id": variants[0]["id"], "title": product.get("title"),
            "handle": product.get("handle"), "status": product.get("status"),
            "sku": source["sku"], "target_type": "variant",
            "admin_url": f"https://{client.shop_domain}/admin/products/{numeric_id}",
            "documents": 0,
        }

    uploaded_documents = _upload_product_documents(client, slug, source["sku"])
    source["html_description"] = _description_with_documents(
        str(source.get("html_description") or ""), uploaded_documents
    )
    product_input = _input(source, existing)
    product_input["status"] = "DRAFT"
    data = client.graphql(
        """mutation SavePimDraft($input:ProductSetInput!){
          productSet(synchronous:true,input:$input){
            product{id title handle status}
            userErrors{field message code}
          }
        }""",
        {"input": product_input},
    )
    payload = data.get("productSet") or {}
    if payload.get("userErrors"):
        raise RuntimeError(
            "Shopify weigerde het conceptproduct: "
            + json.dumps(payload["userErrors"], ensure_ascii=False)
        )
    product = payload.get("product") or {}
    if not product.get("id"):
        raise RuntimeError("Shopify gaf geen product-ID terug.")
    numeric_id = str(product["id"]).rsplit("/", 1)[-1]
    with sqlite3.connect(supplier_database_path(slug)) as connection:
        connection.execute(
            "UPDATE products SET shopify_handle=?,shopify_status='draft',updated_at=? WHERE sku=?",
            (product.get("handle") or "", datetime.now(timezone.utc).isoformat(), source["sku"]),
        )
    return {
        "id": product["id"], "title": product.get("title"),
        "handle": product.get("handle"), "status": product.get("status"),
        "sku": source["sku"], "target_type": "product",
        "admin_url": f"https://{client.shop_domain}/admin/products/{numeric_id}",
        "documents": len(uploaded_documents),
    }


def get_shopify_variant_target(target_sku: str) -> dict[str, Any]:
    """Return the exact Shopify product containing ``target_sku``."""
    target_sku = target_sku.strip()
    if not target_sku:
        raise ValueError("Vul een bestaande Shopify-SKU in.")
    escaped = target_sku.replace("\\", "\\\\").replace('"', '\\"')
    data = ShopifyClient.from_settings().graphql(
        """
        query VariantTarget($query:String!){
          productVariants(first:10,query:$query){nodes{
            id sku selectedOptions{name value}
            product{
              id title handle vendor status
              options{id name position optionValues{id name hasVariants}}
              variants(first:250){nodes{id title sku selectedOptions{name value}}}
            }
          }}
        }
        """,
        {"query": f'sku:"{escaped}"'},
    )
    exact = [
        node for node in (data.get("productVariants") or {}).get("nodes") or []
        if str(node.get("sku") or "").strip().upper() == target_sku.upper()
    ]
    if not exact:
        raise ValueError(f"Shopify-SKU {target_sku} is niet gevonden.")
    if len(exact) > 1:
        raise RuntimeError(f"Meerdere Shopify-varianten hebben SKU {target_sku}.")
    product = exact[0]["product"]
    selected_options = exact[0].get("selectedOptions") or []
    if not selected_options:
        selected_options = next(
            (
                variant.get("selectedOptions") or []
                for variant in (product.get("variants") or {}).get("nodes") or []
                if str(variant.get("sku") or "").strip().upper()
                == target_sku.upper()
            ),
            [],
        )
    return {
        **product,
        "_target_selected_options": {
            str(item.get("name") or ""): str(item.get("value") or "")
            for item in selected_options
            if item.get("name")
        },
    }


def _create_variant_media(
    client: ShopifyClient, product_id: str, image_url: str, alt: str,
) -> str:
    data = client.graphql(
        """
        mutation CreatePimVariantMedia($productId:ID!,$media:[CreateMediaInput!]!){
          productCreateMedia(productId:$productId,media:$media){
            media{id status}
            mediaUserErrors{field message code}
          }
        }
        """,
        {
            "productId": product_id,
            "media": [{
                "originalSource": _shopify_file_url(image_url),
                "alt": alt,
                "mediaContentType": "IMAGE",
            }],
        },
    )
    payload = data.get("productCreateMedia") or {}
    if payload.get("mediaUserErrors"):
        raise RuntimeError(
            "Shopify weigerde de variantfoto: "
            + json.dumps(payload["mediaUserErrors"], ensure_ascii=False)
        )
    media = (payload.get("media") or [None])[0]
    if not media or not media.get("id"):
        raise RuntimeError("Shopify gaf geen media-ID voor de variantfoto terug.")
    return str(media["id"])


def _attach_media_to_existing_variant(
    client: ShopifyClient, product_id: str, variant_id: str, media_id: str,
) -> None:
    data = client.graphql(
        """
        mutation AttachPimVariantMedia(
          $productId:ID!,$variants:[ProductVariantsBulkInput!]!
        ){
          productVariantsBulkUpdate(productId:$productId,variants:$variants){
            productVariants{id sku media(first:10){nodes{id}}}
            userErrors{field message code}
          }
        }
        """,
        {
            "productId": product_id,
            "variants": [{"id": variant_id, "mediaId": media_id}],
        },
    )
    payload = data.get("productVariantsBulkUpdate") or {}
    if payload.get("userErrors"):
        raise RuntimeError(
            "Shopify weigerde de fotokoppeling: "
            + json.dumps(payload["userErrors"], ensure_ascii=False)
        )


def add_pim_product_as_shopify_variant(
    slug: str, sku: str, target_sku: str, option_values: dict[str, str],
) -> dict[str, Any]:
    """Add one PIM SKU to an existing Shopify product without rewriting it."""
    settings = get_shopify_settings()
    if not settings.get("enabled"):
        raise ValueError("Shopify-synchronisatietoestemming staat uit.")
    supplier = get_supplier(slug) or {}
    source = next(
        (item for item in _source_products(slug)
         if str(item.get("sku") or "").strip().upper() == sku.strip().upper()),
        None,
    )
    if not source:
        raise ValueError(f"Product {sku} staat niet actueel in de PIM.")
    client = ShopifyClient.from_settings()
    existing_source_variant = client.find_variant_by_sku(source["sku"])
    target = get_shopify_variant_target(target_sku)
    allowed_vendors = {
        str(supplier.get("name") or "").strip().casefold(),
        *(name.casefold() for name in supplier_route(slug).shopify_vendor_names),
    }
    target_vendor = str(target.get("vendor") or "").strip()
    if target_vendor and target_vendor.casefold() not in allowed_vendors:
        raise ValueError(
            f"Doelproduct heeft Shopify-leverancier {target_vendor}; "
            "leveranciersroutes mogen niet worden gemengd."
        )
    if existing_source_variant:
        existing_product_id = str(
            (existing_source_variant.get("product") or {}).get("id") or ""
        )
        if existing_product_id != str(target["id"]):
            raise ValueError(
                f"SKU {source['sku']} bestaat al bij een ander Shopify-product."
            )
    option_names = [str(option["name"]) for option in target.get("options") or []]
    current_values = target.get("_target_selected_options") or {}
    supplied = {
        name: str(option_values.get(name) or "").strip()
        or str(current_values.get(name) or "").strip()
        for name in option_names
    }
    missing = [name for name in option_names if not supplied.get(name)]
    if missing:
        raise ValueError("Vul alle variantopties in: " + ", ".join(missing))
    requested = tuple((name, supplied[name]) for name in option_names)
    for existing in (target.get("variants") or {}).get("nodes") or []:
        if (
            existing_source_variant
            and str(existing.get("sku") or "").strip().upper()
            == source["sku"].strip().upper()
        ):
            continue
        values = {item["name"]: item["value"] for item in existing.get("selectedOptions") or []}
        current = tuple((name, values.get(name, "")) for name in option_names)
        if current == requested:
            raise ValueError(
                "Deze optiecombinatie bestaat al als variant "
                f"{existing.get('sku') or existing.get('title')}."
            )
    try:
        source["_raw_data"] = json.loads(source.get("raw_data_json") or "{}")
    except json.JSONDecodeError:
        source["_raw_data"] = {}
    source["_shopify_field_mapping"] = supplier.get("shopify_field_mapping") or {}
    source["_shopify_metafield_mapping"] = supplier.get("shopify_metafield_mapping") or {}
    source["inventory_policy"] = "continue"
    source["_supplier_slug"] = slug
    existing_context = (
        {
            "product": {**target, "media": {"nodes": []}},
            "variant": existing_source_variant,
        }
        if existing_source_variant else None
    )
    variant = _input(source, existing_context)["variants"][0]
    variant_file = variant.pop("file", None)
    existing_media = (
        (existing_source_variant.get("media") or {}).get("nodes") or []
        if existing_source_variant else []
    )
    media_repaired = False
    if (
        not existing_media
        and
        isinstance(variant_file, dict)
        and str(variant_file.get("originalSource") or "").startswith(
            ("https://", "http://")
        )
    ):
        variant["mediaId"] = _create_variant_media(
            client,
            str(target["id"]),
            str(variant_file["originalSource"]),
            str(variant_file.get("alt") or source.get("source_title") or source["sku"]),
        )
        media_repaired = bool(existing_source_variant)
    variant_sku = str(variant.pop("sku", "") or source["sku"]).strip()
    inventory_item = dict(variant.get("inventoryItem") or {})
    inventory_item["sku"] = variant_sku
    variant["inventoryItem"] = inventory_item
    variant["optionValues"] = [
        {"optionName": name, "name": supplied[name]} for name in option_names
    ]
    mutation_name = (
        "productVariantsBulkUpdate" if existing_source_variant
        else "productVariantsBulkCreate"
    )
    data = client.graphql(
        f"""
        mutation SavePimVariant($productId:ID!,$variants:[ProductVariantsBulkInput!]!){{
          {mutation_name}(productId:$productId,variants:$variants){{
            productVariants{{id title sku price}}
            userErrors{{field message code}}
          }}
        }}
        """,
        {"productId": target["id"], "variants": [variant]},
    )
    payload = data.get(mutation_name) or {}
    if payload.get("userErrors"):
        raise RuntimeError(
            "Shopify weigerde de variantupdate: "
            + json.dumps(payload["userErrors"], ensure_ascii=False)
        )
    saved = (payload.get("productVariants") or [None])[0]
    if not saved:
        raise RuntimeError("Shopify gaf geen opgeslagen variant terug.")
    with sqlite3.connect(supplier_database_path(slug)) as connection:
        connection.execute(
            "UPDATE products SET shopify_handle=?,shopify_status=?,updated_at=? WHERE sku=?",
            (target.get("handle") or "", str(target.get("status") or "").lower(),
             datetime.now(timezone.utc).isoformat(), source["sku"]),
        )
    numeric_id = str(target["id"]).rsplit("/", 1)[-1]
    return {
        "id": saved["id"], "sku": source["sku"], "title": saved.get("title"),
        "product_title": target.get("title"),
        "updated": bool(existing_source_variant),
        "media_repaired": media_repaired,
        "admin_url": f"https://{client.shop_domain}/admin/products/{numeric_id}",
    }


def upload_test_product_family(
    slug: str, sku: str, *, description_html: str | None = None,
) -> dict[str, Any]:
    """Maak één Concept-testproduct met alle varianten uit de PIM-familie."""
    from app.product_families import get_or_create_product_families
    from app.shopify.derived_inventory import list_mappings

    supplier = get_supplier(slug) or {}
    family = next((
        item for item in get_or_create_product_families(slug, supplier=supplier)
        if any(v.get("sku") == sku for v in item.get("variants") or [])
    ), None)
    if not family:
        return upload_test_product(slug, sku)
    sources = {item["sku"]: item for item in _source_products(slug)}
    client = ShopifyClient.from_settings()
    continue_selling = bool(
        (supplier.get("request_options") or {}).get(
            "continue_selling_when_out_of_stock", False
        )
    )
    variants = []
    family_files: list[dict[str, Any]] = []
    existing_product = None
    family_members = family.get("variants") or []
    family_skus = {member.get("sku") for member in family_members}
    member_by_sku = {member.get("sku"): member for member in family_members}
    missing_sources = sorted(sku for sku in family_skus if sku not in sources)
    if missing_sources:
        raise ValueError(
            "Familiesynchronisatie gestopt; PIM-varianten ontbreken: "
            + ", ".join(missing_sources)
        )
    invalid_prices = sorted(
        member_sku for member_sku in family_skus
        if float((sources.get(member_sku) or {}).get("sale_price") or 0) <= 0
    )
    if invalid_prices:
        raise ValueError(
            "Familiesynchronisatie gestopt; verkoopprijs ontbreekt of is nul: "
            + ", ".join(invalid_prices)
        )
    enriched_source = next((
        sources[member["sku"]] for member in family_members
        if str(sources[member["sku"]].get("html_description") or "").strip()
    ), None)
    family_images = next((
        list(sources[member["sku"]].get("images") or [])
        for member in family_members
        if sources[member["sku"]].get("images")
    ), [])
    if not enriched_source:
        raise ValueError(
            "Familiesynchronisatie gestopt; verrijkte producttekst ontbreekt."
        )
    if not family_images:
        raise ValueError(
            "Familiesynchronisatie gestopt; familieafbeeldingen ontbreken."
        )
    derived_mappings = [
        mapping for mapping in list_mappings()
        if mapping.get("enabled") and mapping.get("base_sku") in family_skus
    ]
    has_packaging_options = bool(derived_mappings)
    diameter_names = []
    packaging_names = []
    packaging_notices = []
    for member in family_members:
        source = sources.get(member["sku"])
        if not source:
            continue
        source = dict(source)
        if not source.get("images"):
            source["images"] = family_images
        source["_raw_data"] = json.loads(source.get("raw_data_json") or "{}")
        source["_shopify_field_mapping"] = supplier.get("shopify_field_mapping") or {}
        source["_shopify_metafield_mapping"] = supplier.get("shopify_metafield_mapping") or {}
        source["inventory_policy"] = "continue" if continue_selling else "deny"
        source["_delivery_time_notice"] = _configured_delivery_time_notice(
            supplier, continue_selling
        )
        test_sku = f"TEST-{source['sku']}"[:255]
        existing_variant = client.find_variant_by_sku(test_sku)
        existing = None
        if existing_variant:
            existing_product = existing_product or existing_variant.get("product")
            existing = {
                "product": {**(existing_variant.get("product") or {}), "media": {"nodes": []}},
                "variant": {**existing_variant, "media": {"nodes": []}},
            }
        variant_input = _input(source, existing)
        variant = variant_input["variants"][0]
        for file_input in variant_input.get("files") or []:
            if not any(
                saved.get("originalSource") == file_input.get("originalSource")
                for saved in family_files
            ):
                family_files.append(file_input)
        if "file" in variant and (
            not isinstance(variant.get("file"), dict)
            or not variant["file"].get("originalSource")
        ):
            variant.pop("file", None)
        existing_variant_media = (
            ((existing or {}).get("variant") or {}).get("media") or {}
        ).get("nodes") or []
        if not existing_variant_media and "file" not in variant:
            variant["file"] = {
                "originalSource": _shopify_file_url(family_images[0]["image_url"]),
                "alt": family_images[0].get("alt_text") or family["title"],
                "contentType": "IMAGE",
            }
        diameter_name = str(
            member.get("diameter_option_label")
            or f"Diameter {member.get('diameter_label')}"
        ).strip()
        diameter_names.append(diameter_name)
        option_values = [{"optionName": "Draaddiameter", "name": diameter_name}]
        if has_packaging_options:
            packaging_name = str(
                member.get("packaging_option_label") or "Volledige verpakking"
            ).strip()
            packaging_names.append(packaging_name)
            option_values.append({"optionName": "Verpakking", "name": packaging_name})
        variant["sku"] = test_sku
        variant["optionValues"] = option_values
        variants.append(variant)
    for mapping in derived_mappings:
        source = sources.get(mapping["base_sku"])
        if not source:
            continue
        source = dict(source)
        if not source.get("images"):
            source["images"] = family_images
        source["_raw_data"] = json.loads(source.get("raw_data_json") or "{}")
        source["_shopify_field_mapping"] = supplier.get("shopify_field_mapping") or {}
        source["_shopify_metafield_mapping"] = supplier.get("shopify_metafield_mapping") or {}
        source["inventory_policy"] = "continue" if continue_selling else "deny"
        source["_delivery_time_notice"] = _configured_delivery_time_notice(
            supplier, continue_selling
        )
        test_sku = f"TEST-{mapping['derived_sku']}"[:255]
        existing_variant = client.find_variant_by_sku(test_sku)
        existing = None
        if existing_variant:
            existing_product = existing_product or existing_variant.get("product")
            existing = {
                "product": {**(existing_variant.get("product") or {}), "media": {"nodes": []}},
                "variant": {**existing_variant, "media": {"nodes": []}},
            }
        variant_input = _input(source, existing)
        variant = variant_input["variants"][0]
        for file_input in variant_input.get("files") or []:
            if not any(
                saved.get("originalSource") == file_input.get("originalSource")
                for saved in family_files
            ):
                family_files.append(file_input)
        if "file" in variant and (
            not isinstance(variant.get("file"), dict)
            or not variant["file"].get("originalSource")
        ):
            variant.pop("file", None)
        existing_variant_media = (
            ((existing or {}).get("variant") or {}).get("media") or {}
        ).get("nodes") or []
        if not existing_variant_media and "file" not in variant:
            variant["file"] = {
                "originalSource": _shopify_file_url(family_images[0]["image_url"]),
                "alt": family_images[0].get("alt_text") or family["title"],
                "contentType": "IMAGE",
            }
        ratio = float(mapping["base_quantity"]) / float(mapping["base_package_quantity"])
        variant["sku"] = test_sku
        variant.pop("barcode", None)
        derived_price = float(variant.get("price") or 0) * ratio
        adjustment_type = mapping.get("price_adjustment_type") or "none"
        adjustment_value = float(mapping.get("price_adjustment_value") or 0)
        if adjustment_type == "fixed":
            derived_price += adjustment_value
        elif adjustment_type == "percent":
            derived_price *= 1 + adjustment_value / 100
        variant["price"] = f"{derived_price:.2f}"
        if (variant.get("inventoryItem") or {}).get("cost") is not None:
            variant["inventoryItem"]["cost"] = (
                f"{float(variant['inventoryItem']['cost']) * ratio:.2f}"
            )
        quantity = float(mapping["base_quantity"])
        if mapping.get("unit_label") == "kilogram":
            amount_label = f"{quantity:g} kg".replace(".", ",")
        elif mapping.get("unit_label") == "gram":
            amount_label = (
                f"{quantity / 1000:.1f} kg".replace(".", ",")
                if quantity >= 1000 and quantity % 1000 == 0
                else f"{quantity:g} gram".replace(".", ",")
            )
        elif mapping.get("unit_label") == "lengte":
            amount_label = f"{quantity:g} mm".replace(".", ",")
        else:
            amount_label = f"{quantity:g} stuks".replace(".", ",")
        member = member_by_sku.get(mapping["base_sku"]) or {}
        diameter_name = str(
            member.get("diameter_option_label")
            or f"Diameter {member.get('diameter_label') or mapping['base_sku']}"
        ).strip()
        packaging_name = f"Deelverpakking {amount_label}"
        diameter_names.append(diameter_name)
        packaging_names.append(packaging_name)
        variant["optionValues"] = [
            {"optionName": "Draaddiameter", "name": diameter_name},
            {"optionName": "Verpakking", "name": packaging_name},
        ]
        notice = str(mapping.get("customer_notice") or "").strip()
        if notice:
            packaging_notices.append(notice)
        variants.append(variant)
    if not variants:
        raise ValueError("De productfamilie bevat geen actuele PIM-varianten.")
    anchor = dict(sources[sku])
    anchor["html_description"] = enriched_source["html_description"]
    anchor["images"] = family_images
    try:
        enrichment = json.loads(
            enriched_source.get("raw_data_json") or "{}"
        ).get("website_enrichment") or {}
    except json.JSONDecodeError:
        enrichment = {}
    facts = enrichment.get("facts") or {}
    classifications = facts.get("classifications") or {}
    family_tags = list(filter(None, [
        enriched_source.get("vendor") or enriched_source.get("brand"),
        family.get("base"), family.get("process"),
        "MMA lassen" if family.get("process") == "SMAW" else "",
        "laselektrode" if family.get("process") == "SMAW" else "lasdraad",
        "Cortenstaal" if "corten" in family["title"].casefold() else "",
        "weervast staal" if "corten" in family["title"].casefold() else "",
        "basische elektrode" if "basisch" in str(facts.get("type") or "").casefold() else "",
        *(f"{name} {value}" for name, value in classifications.items()),
        *(facts.get("approvals") or []),
    ]))
    anchor["ai_tags_json"] = json.dumps(family_tags, ensure_ascii=False)
    anchor["_raw_data"] = json.loads(anchor.get("raw_data_json") or "{}")
    anchor["_shopify_field_mapping"] = supplier.get("shopify_field_mapping") or {}
    anchor["_shopify_metafield_mapping"] = supplier.get("shopify_metafield_mapping") or {}
    anchor["inventory_policy"] = "continue" if continue_selling else "deny"
    anchor["_delivery_time_notice"] = _configured_delivery_time_notice(
        supplier, continue_selling
    )
    if description_html is not None:
        anchor["html_description"] = description_html
    product_input = _input(anchor, None)
    for file_input in product_input.get("files") or []:
        if not any(
            saved.get("originalSource") == file_input.get("originalSource")
            for saved in family_files
        ):
            family_files.append(file_input)
    product_input["metafields"] = _merge_product_verpakkingsopmerking(
        product_input.get("metafields") or [],
        *packaging_notices,
    )
    product_input.update({
        "title": f"[TEST] {anchor.get('ai_title') or family['title']}"[:255],
        "handle": (existing_product or {}).get("handle") or f"test-familie-{sku.lower()}",
        "status": "DRAFT",
        "tags": list(dict.fromkeys([*(product_input.get("tags") or []), "testproduct_verwijder_deze"])),
        "productOptions": [
            {
                "name": "Draaddiameter", "position": 1,
                "values": [
                    {"name": name} for name in dict.fromkeys(diameter_names)
                ],
            },
            *([{
                "name": "Verpakking", "position": 2,
                "values": [
                    {"name": name} for name in dict.fromkeys(packaging_names)
                ],
            }] if has_packaging_options else []),
        ],
        "variants": variants,
        "files": family_files,
    })
    if existing_product:
        product_input["id"] = existing_product["id"]
    payload = client.graphql(
        """
        mutation($input:ProductSetInput!){productSet(synchronous:true,input:$input){
          product{id title handle status variants(first:100){nodes{sku}}}
          userErrors{field message code}
        }}
        """,
        {"input": product_input},
    )["productSet"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
    product = payload["product"]
    actual_variant_skus = [v["sku"] for v in product["variants"]["nodes"]]
    expected_variant_skus = [variant["sku"] for variant in variants]
    if set(actual_variant_skus) != set(expected_variant_skus):
        raise RuntimeError(
            "Shopify-variantcontrole mislukt; verwacht "
            + ", ".join(expected_variant_skus)
            + "; ontvangen " + ", ".join(actual_variant_skus)
        )
    return {
        "id": product["id"], "title": product["title"],
        "status": product["status"],
        "variants": actual_variant_skus,
        "admin_url": f"https://{client.shop_domain}/admin/products/{product['id'].rsplit('/', 1)[-1]}",
    }
