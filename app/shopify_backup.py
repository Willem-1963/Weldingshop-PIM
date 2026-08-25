from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import subprocess
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from app.shopify.client import ShopifyClient


DEFAULT_SHOPIFY_BACKUP_DIR = Path("/root/backup/shopify")
_BULK_POLL_SECONDS = 2
_BULK_TIMEOUT_SECONDS = 60 * 60 * 6

PRODUCTS_QUERY = """
{
  products {
    edges { node {
      id title handle descriptionHtml vendor productType status tags
      createdAt updatedAt publishedAt templateSuffix
      seo { title description }
      options { id name position optionValues { id name hasVariants } }
      variants {
        edges { node {
          id title displayName sku barcode price compareAtPrice position
          taxable taxCode inventoryPolicy inventoryQuantity
          selectedOptions { name value }
          inventoryItem { id tracked requiresShipping measurement { weight { value unit } } }
        } }
      }
      media {
        edges { node {
          id alt mediaContentType status
          ... on MediaImage { image { url width height } }
          ... on Video { sources { url mimeType format height width } }
          ... on ExternalVideo { embedUrl host originUrl }
          ... on Model3d { sources { url mimeType format filesize } }
        } }
      }
      metafields {
        edges { node { id namespace key type value createdAt updatedAt } }
      }
    } }
  }
}
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _start_bulk_export(client: ShopifyClient, query: str) -> str:
    data = client.graphql(
        """
        mutation ShopifyBackupBulk($query: String!) {
          bulkOperationRunQuery(query: $query) {
            bulkOperation { id status }
            userErrors { field message }
          }
        }
        """,
        {"query": query},
    )["bulkOperationRunQuery"]
    errors = data.get("userErrors") or []
    if errors:
        raise RuntimeError("; ".join(item["message"] for item in errors))
    return data["bulkOperation"]["id"]


def _wait_for_bulk_export(
    client: ShopifyClient, operation_id: str, notify: Callable[[str], None]
) -> dict[str, Any]:
    started = time.monotonic()
    while time.monotonic() - started < _BULK_TIMEOUT_SECONDS:
        operation = client.graphql(
            """
            query ShopifyBackupStatus($id: ID!) {
              bulkOperation(id: $id) {
                id status objectCount rootObjectCount fileSize url partialDataUrl errorCode
              }
            }
            """,
            {"id": operation_id},
        )["bulkOperation"]
        status = operation["status"]
        notify(
            f"Shopify exporteert de catalogus: {operation.get('rootObjectCount') or 0} "
            "producten verwerkt…"
        )
        if status == "COMPLETED":
            if not operation.get("url"):
                raise RuntimeError("Shopify voltooide de export zonder downloadbestand")
            return operation
        if status in {"FAILED", "CANCELED", "EXPIRED"}:
            raise RuntimeError(
                f"Shopify-bulkexport eindigde met {status}: "
                f"{operation.get('errorCode') or 'onbekende fout'}"
            )
        time.sleep(_BULK_POLL_SECONDS)
    raise TimeoutError("Shopify-bulkexport duurde langer dan zes uur")


def _download(url: str, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=(20, 180)) as response:
        response.raise_for_status()
        with target.open("wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    output.write(chunk)
    return target.stat().st_size


def _media_urls(jsonl_path: Path) -> list[str]:
    urls: set[str] = set()
    with jsonl_path.open("rb") as source:
        for line in source:
            item = json.loads(line)
            image_url = (item.get("image") or {}).get("url")
            if image_url:
                urls.add(image_url)
            for media_source in item.get("sources") or []:
                if media_source.get("url"):
                    urls.add(media_source["url"])
    return sorted(urls)


def _download_media(
    urls: list[str], target: Path, notify: Callable[[str], None]
) -> tuple[list[dict[str, Any]], int]:
    def fetch(index_url: tuple[int, str]) -> dict[str, Any]:
        index, url = index_url
        suffix = Path(urlparse(url).path).suffix[:12] or ".bin"
        relative = Path("media") / f"{index:06d}{suffix}"
        path = target / relative
        size = _download(url, path)
        return {
            "url": url, "path": str(relative), "size": size,
            "sha256": _sha256(path),
        }

    records: list[dict[str, Any]] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch, item) for item in enumerate(urls, 1)]
        for future in as_completed(futures):
            records.append(future.result())
            completed += 1
            if completed == 1 or completed % 100 == 0 or completed == len(urls):
                notify(f"Originele Shopify-media downloaden: {completed}/{len(urls)}…")
    records.sort(key=lambda item: item["path"])
    return records, sum(item["size"] for item in records)


def create_shopify_catalog_backup(
    password: str, *, backup_dir: str | Path = DEFAULT_SHOPIFY_BACKUP_DIR,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create an encrypted snapshot of current products and their original media."""
    if len(password) < 12:
        raise ValueError("Gebruik een back-upwachtwoord van minimaal 12 tekens")
    notify = progress or (lambda message: None)
    root = Path(backup_dir)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"weldingshop-shopify-catalogus-{stamp}"
    encrypted = root / f"{name}.tar.gz.enc"
    checksum_path = root / f"{name}.sha256"
    client = ShopifyClient.from_settings()

    with tempfile.TemporaryDirectory(prefix=".building-", dir=root) as temporary:
        staging = Path(temporary) / name
        staging.mkdir()
        notify("Shopify-productexport starten…")
        operation_id = _start_bulk_export(client, PRODUCTS_QUERY)
        operation = _wait_for_bulk_export(client, operation_id, notify)
        jsonl = staging / "products.jsonl"
        notify("Productgegevens van Shopify downloaden…")
        _download(operation["url"], jsonl)
        urls = _media_urls(jsonl)
        media, media_bytes = _download_media(urls, staging, notify)
        (staging / "MEDIA.json").write_text(
            json.dumps(media, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {
            "format": 1,
            "kind": "shopify-product-catalog",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "shop": client.shop_domain,
            "api_version": client.api_version,
            "encrypted": True,
            "bulk_operation_id": operation_id,
            "products": int(operation.get("rootObjectCount") or 0),
            "objects": int(operation.get("objectCount") or 0),
            "product_data_bytes": jsonl.stat().st_size,
            "media_files": len(media),
            "media_bytes": media_bytes,
        }
        (staging / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        checksums = []
        for path in sorted(item for item in staging.rglob("*") if item.is_file()):
            checksums.append(f"{_sha256(path)}  {path.relative_to(staging)}")
        (staging / "CHECKSUMS.sha256").write_text(
            "\n".join(checksums) + "\n", encoding="utf-8"
        )
        notify("Shopify-catalogus comprimeren en versleutelen…")
        plaintext = Path(temporary) / f"{name}.tar.gz"
        with tarfile.open(plaintext, "w:gz") as archive:
            archive.add(staging, arcname=name)
        try:
            subprocess.run(
                ["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2",
                 "-iter", "250000", "-md", "sha256", "-pass", "stdin",
                 "-in", str(plaintext), "-out", str(encrypted)],
                input=password + "\n", text=True, check=True, capture_output=True,
            )
        finally:
            plaintext.unlink(missing_ok=True)
    os.chmod(encrypted, 0o600)
    checksum = _sha256(encrypted)
    checksum_path.write_text(f"{checksum}  {encrypted.name}\n", encoding="utf-8")
    os.chmod(checksum_path, 0o600)
    return {**manifest, "path": str(encrypted), "checksum_path": str(checksum_path),
            "sha256": checksum, "size": encrypted.stat().st_size}


def list_shopify_catalog_backups(
    backup_dir: str | Path = DEFAULT_SHOPIFY_BACKUP_DIR,
) -> list[dict[str, Any]]:
    root = Path(backup_dir)
    if not root.exists():
        return []
    result = []
    for path in sorted(root.glob("weldingshop-shopify-catalogus-*.tar.gz.enc"), reverse=True):
        checksum = path.with_suffix("").with_suffix("").with_suffix(".sha256")
        expected = checksum.read_text(encoding="utf-8").split()[0] if checksum.is_file() else ""
        result.append({"name": path.name, "path": str(path), "size": path.stat().st_size,
                       "sha256": expected, "checksum_path": str(checksum) if checksum.is_file() else ""})
    return result


def verify_shopify_catalog_backup(
    path: str | Path, *, backup_dir: str | Path = DEFAULT_SHOPIFY_BACKUP_DIR,
) -> dict[str, Any]:
    archive = Path(path).resolve()
    if archive.parent != Path(backup_dir).resolve() or not archive.is_file():
        raise ValueError("Onbekend Shopify-back-upbestand")
    checksum = archive.with_suffix("").with_suffix("").with_suffix(".sha256")
    if not checksum.is_file():
        raise ValueError("SHA-256-bestand ontbreekt")
    expected = checksum.read_text(encoding="utf-8").split()[0]
    actual = _sha256(archive)
    return {"valid": expected == actual, "expected": expected, "actual": actual}


def _extract_catalog_backup(path: str | Path, password: str, target: Path) -> Path:
    """Decrypt and safely extract a catalog archive into *target*."""
    archive = Path(path).resolve()
    if archive.parent != DEFAULT_SHOPIFY_BACKUP_DIR.resolve() or not archive.is_file():
        raise ValueError("Onbekend Shopify-back-upbestand")
    if not verify_shopify_catalog_backup(archive)["valid"]:
        raise ValueError("SHA-256-controle van de Shopify-back-up is mislukt")
    plaintext = target / "catalog.tar.gz"
    result = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "250000",
         "-md", "sha256", "-pass", "stdin", "-in", str(archive), "-out", str(plaintext)],
        input=password + "\n", text=True, capture_output=True,
    )
    if result.returncode:
        raise ValueError("Back-up kon niet worden geopend; controleer het wachtwoord")
    try:
        with tarfile.open(plaintext, "r:gz") as bundle:
            bundle.extractall(target, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("Back-upbestand is beschadigd of heeft een ongeldig formaat") from exc
    finally:
        plaintext.unlink(missing_ok=True)
    roots = [item for item in target.iterdir() if item.is_dir()]
    if len(roots) != 1 or not (roots[0] / "products.jsonl").is_file():
        raise ValueError("products.jsonl ontbreekt in de Shopify-back-up")
    return roots[0]


def _catalog_products(jsonl_path: Path) -> list[dict[str, Any]]:
    products: dict[str, dict[str, Any]] = {}
    children: list[dict[str, Any]] = []
    with jsonl_path.open(encoding="utf-8") as source:
        for line in source:
            item = json.loads(line)
            item_id = str(item.get("id") or "")
            if item_id.startswith("gid://shopify/Product/"):
                products[item_id] = {**item, "variants": [], "media": [], "metafields": []}
            elif item.get("__parentId"):
                children.append(item)
    for item in children:
        product = products.get(str(item.get("__parentId") or ""))
        if not product:
            continue
        item_id = str(item.get("id") or "")
        if item_id.startswith("gid://shopify/ProductVariant/"):
            product["variants"].append(item)
        elif item_id.startswith("gid://shopify/Metafield/"):
            product["metafields"].append(item)
        elif item.get("mediaContentType"):
            product["media"].append(item)
    return sorted(products.values(), key=lambda item: str(item.get("title") or "").casefold())


def list_products_in_shopify_backup(path: str | Path, password: str) -> list[dict[str, Any]]:
    """Return compact product summaries from an encrypted catalog backup."""
    with tempfile.TemporaryDirectory(prefix="shopify-backup-read-") as temporary:
        root = _extract_catalog_backup(path, password, Path(temporary))
        return [{
            "id": item["id"], "title": item.get("title") or "Zonder titel",
            "handle": item.get("handle") or "", "vendor": item.get("vendor") or "",
            "status": item.get("status") or "", "variant_count": len(item["variants"]),
            "skus": [str(v.get("sku") or "") for v in item["variants"] if v.get("sku")],
            "media_count": len(item["media"]),
        } for item in _catalog_products(root / "products.jsonl")]


def get_product_from_shopify_backup(
    path: str | Path, password: str, product_id: str,
) -> dict[str, Any]:
    """Return one complete product snapshot for the read-only backup viewer."""
    with tempfile.TemporaryDirectory(prefix="shopify-backup-product-") as temporary:
        root = _extract_catalog_backup(path, password, Path(temporary))
        product = next(
            (item for item in _catalog_products(root / "products.jsonl")
             if item["id"] == product_id), None,
        )
        if not product:
            raise ValueError("Geselecteerd product staat niet in deze back-up")
        return product


def _stage_backup_image(client: ShopifyClient, path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = client.graphql(
        """mutation($input:[StagedUploadInput!]!){stagedUploadsCreate(input:$input){
        stagedTargets{url resourceUrl parameters{name value}} userErrors{field message}}}""",
        {"input": [{"resource": "IMAGE", "filename": path.name,
                    "mimeType": mime_type, "httpMethod": "POST",
                    "fileSize": str(path.stat().st_size)}]},
    )["stagedUploadsCreate"]
    if payload.get("userErrors"):
        raise RuntimeError("Shopify media-upload: " + json.dumps(payload["userErrors"], ensure_ascii=False))
    target = payload["stagedTargets"][0]
    fields = {item["name"]: item["value"] for item in target["parameters"]}
    with path.open("rb") as media:
        response = requests.post(target["url"], data=fields,
                                 files={"file": (path.name, media, mime_type)}, timeout=180)
    response.raise_for_status()
    return str(target["resourceUrl"])


def restore_product_from_shopify_backup(
    path: str | Path, password: str, product_id: str,
    *, client: ShopifyClient | None = None,
) -> dict[str, Any]:
    """Restore one backed-up product as a new Shopify draft, excluding inventory."""
    client = client or ShopifyClient.from_settings()
    with tempfile.TemporaryDirectory(prefix="shopify-product-restore-") as temporary:
        root = _extract_catalog_backup(path, password, Path(temporary))
        product = next(
            (item for item in _catalog_products(root / "products.jsonl")
             if item["id"] == product_id), None,
        )
        if not product:
            raise ValueError("Geselecteerd product staat niet in deze back-up")
        media_index = {
            item["url"]: root / item["path"]
            for item in json.loads((root / "MEDIA.json").read_text(encoding="utf-8"))
        }
        files = []
        for media in product["media"]:
            image_url = str((media.get("image") or {}).get("url") or "")
            local = media_index.get(image_url)
            if media.get("mediaContentType") == "IMAGE" and local and local.is_file():
                files.append({"originalSource": _stage_backup_image(client, local),
                              "contentType": "IMAGE", "alt": media.get("alt") or ""})
        options = [{"name": option["name"], "position": option.get("position"),
                    "values": [{"name": value["name"]}
                               for value in option.get("optionValues") or []]}
                   for option in product.get("options") or []]
        variants = []
        for variant in product["variants"]:
            restored = {key: variant[key] for key in (
                "sku", "barcode", "price", "compareAtPrice", "taxable", "inventoryPolicy"
            ) if variant.get(key) is not None}
            restored["optionValues"] = [
                {"optionName": value["name"], "name": value["value"]}
                for value in variant.get("selectedOptions") or []
            ]
            inventory = variant.get("inventoryItem") or {}
            restored["inventoryItem"] = {
                key: inventory[key] for key in ("tracked", "requiresShipping")
                if inventory.get(key) is not None
            }
            variants.append(restored)
        input_data: dict[str, Any] = {
            "title": product.get("title") or "Hersteld product",
            "descriptionHtml": product.get("descriptionHtml") or "",
            "vendor": product.get("vendor") or "", "productType": product.get("productType") or "",
            "tags": product.get("tags") or [], "status": "DRAFT", "productOptions": options,
            "variants": variants, "files": files,
            "metafields": [{key: field[key] for key in ("namespace", "key", "type", "value")}
                           for field in product["metafields"]],
        }
        if product.get("seo"):
            input_data["seo"] = product["seo"]
        payload = client.graphql(
            """mutation($input:ProductSetInput!){productSet(synchronous:true,input:$input){
            product{id title handle status} userErrors{code field message}}}""",
            {"input": input_data},
        ).get("productSet") or {}
        if payload.get("userErrors"):
            raise RuntimeError("Shopify herstel: " + json.dumps(payload["userErrors"], ensure_ascii=False))
        restored = payload.get("product") or {}
        if not restored.get("id"):
            raise RuntimeError("Shopify gaf geen hersteld product terug")
        return {**restored, "variants": len(variants), "images": len(files)}
