"""Controleer en herstel Shopify-CDN-iconen in Certilas-laspositietabellen."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from app.suppliers.hub import supplier_database_path, utc_now


POSITION_CODES = {"PA", "PB", "PC", "PD", "PE", "PF", "PG"}


def position_images(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute(
        """SELECT asset_key,shopify_cdn_url FROM managed_external_assets
           WHERE asset_key LIKE 'certilas-welding-position-%'
             AND COALESCE(shopify_cdn_url,'')<>''"""
    ).fetchall()
    return {
        str(asset_key).rsplit("-", 1)[-1].upper(): str(url)
        for asset_key, url in rows
    }


def enrich_html(value: str, images: dict[str, str]) -> tuple[str, list[str]]:
    soup = BeautifulSoup(value or "", "html.parser")
    added: list[str] = []
    for heading in soup.find_all(["h2", "h3", "h4"]):
        if "laspositie" not in heading.get_text(" ", strip=True).casefold():
            continue
        table = heading.find_next_sibling("table")
        if not table:
            continue
        for cell in table.find_all("td"):
            code = cell.get_text(" ", strip=True).upper()
            if code not in POSITION_CODES or code not in images:
                continue
            current = cell.find("img")
            if current and str(current.get("src") or "") == images[code]:
                continue
            cell.clear()
            icon = soup.new_tag(
                "img", src=images[code], alt=f"Laspositie {code}",
                width="64", height="64", loading="lazy",
            )
            cell.append(icon)
            cell.append(soup.new_tag("br"))
            label = soup.new_tag("strong"); label.string = code
            cell.append(label)
            added.append(code)
    return str(soup), added


def run(*, apply: bool = False) -> dict[str, object]:
    path = supplier_database_path("certilas")
    changed: list[dict[str, object]] = []
    with sqlite3.connect(path) as connection:
        images = position_images(connection)
        if set(images) != POSITION_CODES:
            missing = sorted(POSITION_CODES - set(images))
            raise RuntimeError("Shopify-CDN-iconen ontbreken voor: " + ", ".join(missing))
        rows = connection.execute(
            """SELECT sku,html_description,content_locked FROM products
               WHERE COALESCE(html_description,'')<>'' ORDER BY sku"""
        ).fetchall()
        updates: list[tuple[str, str]] = []
        for sku, description, locked in rows:
            enriched, added = enrich_html(description, images)
            if not added:
                continue
            changed.append({"sku": sku, "icons_added": added, "content_locked": bool(locked)})
            updates.append((enriched, sku))
        backup = ""
        if apply and updates:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = path.with_name(f"{path.stem}-before-position-icons-{stamp}.sqlite")
            shutil.copy2(path, backup_path)
            backup = str(backup_path)
            now = utc_now()
            connection.executemany(
                "UPDATE products SET html_description=?,updated_at=? WHERE sku=?",
                [(description, now, sku) for description, sku in updates],
            )
    return {
        "mode": "apply" if apply else "audit",
        "products_checked": len(rows),
        "products_changed": len(changed),
        "icons_added": sum(len(item["icons_added"]) for item in changed),
        "changed": changed,
        "backup": backup,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(apply=args.apply), ensure_ascii=False, indent=2))
