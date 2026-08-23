#!/usr/bin/env python3
"""Herstel letterlijk afgedrukte dictionaries onder Eigenschappen."""

import json
import sqlite3
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app.product_families import rebuild_product_families
from app.suppliers.hub import supplier_database_path, utc_now
from app.suppliers.website_enrichment import _section_html


def main() -> dict:
    path = supplier_database_path("certilas")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.stem}-before-properties-format-{stamp}.sqlite")
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    updated = 0
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT sku,html_description,raw_data_json FROM products WHERE html_description LIKE '%<p>{''%'"
        ).fetchall()
        for sku, description, raw_json in rows:
            raw = json.loads(raw_json or "{}")
            properties = (((raw.get("website_enrichment") or {}).get("facts") or {}).get("properties"))
            if not isinstance(properties, dict):
                continue
            soup = BeautifulSoup(description, "html.parser")
            heading = next((h for h in soup.find_all("h3") if h.get_text(" ", strip=True) == "Eigenschappen"), None)
            current = heading.find_next_sibling() if heading else None
            if not current:
                continue
            replacement = BeautifulSoup(_section_html(properties), "html.parser")
            current.replace_with(replacement)
            connection.execute(
                "UPDATE products SET html_description=?,updated_at=? WHERE sku=?",
                (str(soup), utc_now(), sku),
            )
            updated += 1
        remaining = connection.execute("SELECT COUNT(*) FROM products WHERE html_description LIKE '%<p>{''%'").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    rebuild_product_families("certilas", path)
    return {"updated": updated, "remaining": remaining, "backup": str(backup), "integrity": integrity}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
