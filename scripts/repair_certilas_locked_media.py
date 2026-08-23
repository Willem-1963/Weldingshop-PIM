"""Backfill verified Certilas media through content locks. Dry-run by default."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/srv/weldingshop-pim")

from app.product_families import rebuild_product_families
from app.suppliers.hub import supplier_database_path, utc_now
from app.suppliers.website_enrichment import _merge_welding_position_icons


POSITION_CODES = {"PA", "PB", "PC", "PD", "PE", "PF", "PG"}


def run(*, apply: bool) -> dict[str, object]:
    path = supplier_database_path("certilas")
    changes: list[dict[str, object]] = []
    backup = ""
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        assets = {
            str(row["asset_key"]).rsplit("-", 1)[-1].upper(): row["shopify_cdn_url"]
            for row in connection.execute(
                "SELECT asset_key,shopify_cdn_url FROM managed_external_assets "
                "WHERE asset_key LIKE 'certilas-welding-position-%'"
            )
            if row["shopify_cdn_url"]
        }
        if set(assets) != POSITION_CODES:
            raise RuntimeError("Niet alle zeven centrale laspositie-iconen zijn beschikbaar.")
        rows = connection.execute(
            "SELECT sku,source_title,html_description,raw_data_json FROM products ORDER BY sku"
        ).fetchall()
        updates = []
        inserts = []
        for row in rows:
            raw = json.loads(row["raw_data_json"] or "{}")
            enrichment = raw.get("website_enrichment") or {}
            facts = enrichment.get("facts") or {}
            positions = list(dict.fromkeys(
                str(code).strip().upper()
                for code in facts.get("welding_positions") or []
                if str(code).strip().upper() in POSITION_CODES
            ))
            position_map = {code: assets[code] for code in positions}
            old_html = str(row["html_description"] or "")
            new_html = _merge_welding_position_icons(old_html, positions, position_map)
            old_map = facts.get("welding_position_images") or {}
            metadata_changed = bool(positions and old_map != position_map)
            if metadata_changed:
                facts["welding_position_images"] = position_map
                enrichment["facts"] = facts
                raw["website_enrichment"] = enrichment
            existing = {
                image_url for (image_url,) in connection.execute(
                    "SELECT image_url FROM product_images WHERE sku=?", (row["sku"],)
                )
            }
            image_urls = list(dict.fromkeys(
                str(url).strip() for url in enrichment.get("image_urls") or []
                if str(url).strip()
            ))
            missing_images = [url for url in image_urls if url not in existing]
            if new_html != old_html or metadata_changed:
                updates.append((new_html, json.dumps(raw, ensure_ascii=False), utc_now(), row["sku"]))
            for position, image_url in enumerate(image_urls, start=1):
                if image_url in missing_images:
                    inserts.append((row["sku"], image_url, position, row["source_title"] or row["sku"]))
            if new_html != old_html or metadata_changed or missing_images:
                changes.append({
                    "sku": row["sku"],
                    "icons_added": [code for code in positions if assets[code] in new_html and assets[code] not in old_html],
                    "images_added": missing_images,
                    "metadata_repaired": metadata_changed,
                })
        if apply and (updates or inserts):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = path.with_name(f"{path.stem}-before-locked-media-repair-{stamp}.sqlite")
            shutil.copy2(path, backup_path)
            backup = str(backup_path)
            connection.executemany(
                "UPDATE products SET html_description=?,raw_data_json=?,updated_at=? WHERE sku=?",
                updates,
            )
            connection.executemany(
                "INSERT OR IGNORE INTO product_images(sku,image_url,position,alt_text) VALUES(?,?,?,?)",
                inserts,
            )
    if apply and changes:
        rebuild_product_families("certilas", path)
    return {
        "mode": "apply" if apply else "audit",
        "products_changed": len(changes),
        "products_with_icons_added": sum(bool(row["icons_added"]) for row in changes),
        "icons_added": sum(len(row["icons_added"]) for row in changes),
        "products_with_images_added": sum(bool(row["images_added"]) for row in changes),
        "images_added": sum(len(row["images_added"]) for row in changes),
        "metadata_repaired": sum(bool(row["metadata_repaired"]) for row in changes),
        "backup": backup,
        "changes": changes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(apply=args.apply), ensure_ascii=False, indent=2))
