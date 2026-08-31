#!/usr/bin/env python3
"""Import Rhodius Canto portal images into a local, price-list-ready dataset."""

from __future__ import annotations

import argparse
import csv
import getpass
import io
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from PIL import Image, PngImagePlugin

PngImagePlugin.MAX_TEXT_CHUNK = 100 * 1024 * 1024
PngImagePlugin.MAX_TEXT_MEMORY = 200 * 1024 * 1024

BASE_URL = "https://rhodius-abrasives.canto.de"
PORTAL_PREFIX = "/v/customer"
ALBUMS = {
    "UI3TI": "Trennen | Cutting",
    "O2L3C": "Reinigen | Cleaning",
    "VF2TP": "Schruppschleifen | Grinding",
    "ITGO7": "Schleifen & Polieren | Grinding & Polishing",
    "L9UR0": "Hartmetallfräsen | Milling",
    "OM8TH": "Diamantwerkzeuge | Diamond Tools",
    "U2QGE": "Zubehör | Occupational Safety",
    "HH4J8": "Materialbilder | Materials",
    "RA086": "Verpackungsbilder | Packaging",
    "KTILL": "Anwendungsbilder | Application Images",
    "MV6NI": "Produktdaten | Product Data",
}
ASSET_FIELDS = [
    "asset_id", "article_number", "ean", "product_name", "description_de",
    "dimensions", "category", "materials", "machine", "media_type", "tags",
    "albums", "source_filename", "source_width", "source_height", "source_bytes",
    "source_md5", "image_file", "image_width", "image_height", "image_bytes",
    "download_status",
]
PRODUCT_FIELDS = [
    "supplier", "supplier_slug", "article_number", "ean", "product_name",
    "description_de", "dimensions", "category", "materials", "machine",
    "albums", "image_files", "image_count",
]


def first(data: dict[str, Any], key: str) -> str:
    values = data.get(key) or []
    return str(values[0]) if values else ""


def joined(data: dict[str, Any], key: str) -> str:
    return ", ".join(str(value) for value in (data.get(key) or []))


def web_preview_urls(data: dict[str, Any], size: int = 800) -> list[str]:
    candidates = data.get("previewURI320") or data.get("previewURI240") or []
    if not candidates:
        return []
    original = candidates[0]
    preferred = re.sub(r"\.(240|320)\.jpg(?=\?)", f".{size}.jpg", original)
    return list(dict.fromkeys([preferred, original]))


def safe_image_name(hit: dict[str, Any]) -> str:
    data = hit["data"]
    article = first(data, "meta_text_5")
    ean = first(data, "meta_text_6")
    stem = Path(hit["displayName"]).stem
    position_match = re.search(r"(?:^|_)p(\d+)(?:_|$)", stem, flags=re.IGNORECASE)
    position = f"p{position_match.group(1)}" if position_match else hit["path"][-8:]
    base = article or ean or re.sub(r"[^A-Za-z0-9_-]+", "_", stem)
    return f"{base}__{position}__{hit['path'][-8:]}.webp"


def login(session: requests.Session, username: str, password: str) -> None:
    response = session.post(
        BASE_URL + "/login/v/customer",
        data={"username": username, "password": password, "rememberme": "false"},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError("Canto login failed")


def fetch_album(session: requests.Session, album_id: str) -> tuple[int, list[dict[str, Any]]]:
    start = 0
    found = 0
    hits: list[dict[str, Any]] = []
    while True:
        response = session.get(
            BASE_URL + f"/rest{PORTAL_PREFIX}/search/album/{album_id}",
            params={
                "aggsEnabled": "false",
                "sortBy": "created",
                "sortDirection": "false",
                "size": 100,
                "start": start,
                "type": "image",
                "operator": "and",
            },
            timeout=120,
        )
        response.raise_for_status()
        result = response.json()["hits"]
        found = int(result.get("found") or 0)
        page_hits = result.get("hit") or []
        hits.extend(page_hits)
        start += len(page_hits)
        if not page_hits or start >= found:
            break
    return found, hits


def merge_assets(album_hits: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for album_id, hits in album_hits.items():
        for hit in hits:
            asset_id = hit["path"]
            if asset_id not in merged:
                merged[asset_id] = {"hit": hit, "album_ids": []}
            merged[asset_id]["album_ids"].append(album_id)
    return list(merged.values())


def download_asset(asset: dict[str, Any], images_dir: Path, quality: int) -> dict[str, Any]:
    hit = asset["hit"]
    data = hit["data"]
    filename = safe_image_name(hit)
    destination = images_dir / filename
    status = "existing"
    if not destination.exists() or destination.stat().st_size == 0:
        urls = web_preview_urls(data)
        if not urls:
            raise RuntimeError(f"No preview URL for {hit['displayName']}")
        response = None
        for url in urls:
            candidate = requests.get(url, timeout=120)
            if candidate.ok:
                response = candidate
                break
        if response is None:
            candidate.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        temporary = destination.with_suffix(".tmp")
        image.save(temporary, "WEBP", quality=quality, method=6)
        temporary.replace(destination)
        status = "downloaded"
    with Image.open(destination) as image:
        width, height = image.size
    return {
        "asset_id": hit["path"],
        "article_number": first(data, "meta_text_5"),
        "ean": first(data, "meta_text_6"),
        "product_name": first(data, "meta_text_0"),
        "description_de": first(data, "meta_text_4"),
        "dimensions": first(data, "meta_text_8"),
        "category": joined(data, "meta_multichoice_3"),
        "materials": joined(data, "meta_multichoice_4"),
        "machine": joined(data, "meta_multichoice_8"),
        "media_type": joined(data, "meta_multichoice_2"),
        "tags": joined(data, "tag"),
        "albums": " | ".join(ALBUMS[album_id] for album_id in asset["album_ids"]),
        "source_filename": hit["displayName"],
        "source_width": int(first(data, "width") or 0),
        "source_height": int(first(data, "height") or 0),
        "source_bytes": int(first(data, "size") or 0),
        "source_md5": first(data, "md5checksum"),
        "image_file": f"images/{filename}",
        "image_width": width,
        "image_height": height,
        "image_bytes": destination.stat().st_size,
        "download_status": status,
    }


def product_rows(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    products: dict[str, dict[str, Any]] = {}
    for asset in assets:
        article = asset["article_number"]
        if not article:
            continue
        product = products.setdefault(article, {
            "supplier": "Rhodius Abrasives GmbH",
            "supplier_slug": "rhodius",
            "article_number": article,
            "ean": asset["ean"],
            "product_name": asset["product_name"],
            "description_de": asset["description_de"],
            "dimensions": asset["dimensions"],
            "category": asset["category"],
            "materials": asset["materials"],
            "machine": asset["machine"],
            "albums": [],
            "image_files": [],
        })
        for key in ("ean", "product_name", "description_de", "dimensions", "category", "materials", "machine"):
            if not product[key] and asset[key]:
                product[key] = asset[key]
        product["albums"].extend(part for part in asset["albums"].split(" | ") if part)
        product["image_files"].append(asset["image_file"])
    rows = []
    for product in products.values():
        product["albums"] = " | ".join(dict.fromkeys(product["albums"]))
        product["image_files"] = " | ".join(dict.fromkeys(product["image_files"]))
        product["image_count"] = len(product["image_files"].split(" | "))
        rows.append(product)
    return sorted(rows, key=lambda row: row["article_number"])


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("output/rhodius_all_albums"))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--quality", type=int, default=85)
    args = parser.parse_args()

    username = getpass.getpass("Canto username: ")
    password = getpass.getpass("Canto password: ")
    session = requests.Session()
    login(session, username, password)

    args.output.mkdir(parents=True, exist_ok=True)
    images_dir = args.output / "images"
    images_dir.mkdir(exist_ok=True)
    album_hits: dict[str, list[dict[str, Any]]] = {}
    album_counts: dict[str, int] = {}
    for album_id, album_name in ALBUMS.items():
        found, hits = fetch_album(session, album_id)
        album_hits[album_id] = hits
        album_counts[album_name] = found
        print(f"Metadata: {album_name}: {len(hits)}/{found}", flush=True)

    merged = merge_assets(album_hits)
    assets: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(download_asset, asset, images_dir, args.quality): asset for asset in merged}
        for completed, future in enumerate(as_completed(futures), start=1):
            try:
                row = future.result()
                with lock:
                    assets.append(row)
            except Exception as exc:
                hit = futures[future]["hit"]
                failures.append({"asset_id": hit["path"], "source_filename": hit["displayName"], "error": str(exc)})
            if completed % 100 == 0 or completed == len(futures):
                print(f"Afbeeldingen: {completed}/{len(futures)}; fouten: {len(failures)}", flush=True)

    assets.sort(key=lambda row: (row["article_number"], row["source_filename"]))
    products = product_rows(assets)
    (args.output / "assets.json").write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "products.json").write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(args.output / "assets.csv", assets, ASSET_FIELDS)
    write_csv(args.output / "products.csv", products, PRODUCT_FIELDS)
    summary = {
        "albums": album_counts,
        "album_asset_occurrences": sum(album_counts.values()),
        "unique_assets": len(merged),
        "downloaded_assets": len(assets),
        "failed_assets": len(failures),
        "products_with_article_number": len(products),
        "assets_without_article_number": sum(not row["article_number"] for row in assets),
        "products_with_ean": sum(bool(row["ean"]) for row in products),
        "duplicate_album_occurrences": sum(album_counts.values()) - len(merged),
        "source_total_bytes": sum(row["source_bytes"] for row in assets),
        "web_total_bytes": sum(row["image_bytes"] for row in assets),
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
