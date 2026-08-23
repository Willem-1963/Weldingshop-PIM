#!/usr/bin/env python3
"""Herstel ontbrekende tekst/foto's in alle opgeslagen Certilas-families."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.product_families import rebuild_product_families
from app.suppliers.hub import supplier_database_path, utc_now
from app.suppliers.website_enrichment import research_certilas_family
from scripts.apply_certilas_contextual_explanations import enrich


def incomplete_families(path: Path) -> list[dict]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            """SELECT f.family_key,f.title,COUNT(*) variants,
                      SUM(COALESCE(p.html_description,'')<>'') text_count,
                      SUM(EXISTS(SELECT 1 FROM product_images i WHERE i.sku=p.sku)) image_count
               FROM product_families f
               JOIN product_family_variants v ON v.family_key=f.family_key
               JOIN products p ON p.sku=v.sku
               GROUP BY f.family_key,f.title
               HAVING text_count<variants OR image_count<variants
               ORDER BY f.title"""
        ).fetchall()]


def propagate_proven_family_content(path: Path, family_key: str) -> dict:
    """Vul uitsluitend gaten vanuit een zustervariant in exact dezelfde familie."""
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        variants = connection.execute(
            """SELECT p.sku,p.html_description FROM products p
               JOIN product_family_variants v ON v.sku=p.sku
               WHERE v.family_key=? ORDER BY v.position""", (family_key,)
        ).fetchall()
        descriptions = [row["html_description"] for row in variants if row["html_description"]]
        source_description = max(descriptions, key=len) if descriptions else ""
        images = connection.execute(
            """SELECT DISTINCT i.image_url,i.alt_text FROM product_images i
               JOIN product_family_variants v ON v.sku=i.sku
               WHERE v.family_key=? ORDER BY i.position,i.id""", (family_key,)
        ).fetchall()
        filled_text = filled_images = linked = 0
        for row in variants:
            current = row["html_description"] or ""
            if not current and source_description:
                current = source_description
                filled_text += 1
            if current:
                updated, _ = enrich(current)
                if updated != (row["html_description"] or ""):
                    connection.execute(
                        "UPDATE products SET html_description=?,updated_at=? WHERE sku=?",
                        (updated, utc_now(), row["sku"]),
                    )
                    linked += 1
            has_images = connection.execute(
                "SELECT 1 FROM product_images WHERE sku=? LIMIT 1", (row["sku"],)
            ).fetchone()
            if not has_images and images:
                for position, image in enumerate(images, start=1):
                    connection.execute(
                        """INSERT OR IGNORE INTO product_images(sku,image_url,position,alt_text)
                           VALUES(?,?,?,?)""",
                        (row["sku"], image["image_url"], position, image["alt_text"] or ""),
                    )
                filled_images += 1
        return {"filled_text": filled_text, "filled_images": filled_images, "linked": linked}


def main() -> dict:
    path = supplier_database_path("certilas")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.stem}-before-incomplete-family-repair-{stamp}.sqlite")
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    targets = incomplete_families(path)
    results = []
    for number, family in enumerate(targets, start=1):
        item = {"family_key": family["family_key"], "title": family["title"]}
        print(json.dumps({"progress": f"{number}/{len(targets)}", "title": family["title"]}, ensure_ascii=False), flush=True)
        try:
            item["research"] = research_certilas_family(family["family_key"], database_path=path)
        except Exception as exc:
            item["research_error"] = str(exc)
        item["propagated"] = propagate_proven_family_content(path, family["family_key"])
        results.append(item)
    rebuild_product_families("certilas", path)
    remaining = incomplete_families(path)
    with sqlite3.connect(path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    report = path.parent / f"certilas-incomplete-family-repair-{stamp}.json"
    payload = {
        "targeted": len(targets), "remaining": len(remaining),
        "remaining_families": remaining, "results": results,
        "backup": str(backup), "integrity": integrity,
    }
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: payload[key] for key in ("targeted", "remaining", "backup", "integrity")} | {"report": str(report)}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False), flush=True)
