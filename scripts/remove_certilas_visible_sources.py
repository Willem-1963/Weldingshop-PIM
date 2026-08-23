from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app.suppliers.hub import supplier_database_path, utc_now


def remove_visible_source(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for paragraph in list(soup.find_all("p")):
        text = paragraph.get_text(" ", strip=True)
        links = [str(link.get("href") or "") for link in paragraph.find_all("a")]
        if text.casefold().startswith("bron:") and any(
            "certilas.com" in link.casefold() for link in links
        ):
            paragraph.decompose()
    return str(soup)


def main() -> None:
    path = supplier_database_path("certilas")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(f"{path.stem}-before-source-cleanup-{stamp}.sqlite")
    with sqlite3.connect(path) as source, sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    updated = 0
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """SELECT sku,html_description FROM products
               WHERE html_description LIKE '%Bron:%certilas.com%'"""
        ).fetchall()
        for sku, description in rows:
            cleaned = remove_visible_source(description)
            if cleaned != description:
                connection.execute(
                    "UPDATE products SET html_description=?,updated_at=? WHERE sku=?",
                    (cleaned, utc_now(), sku),
                )
                updated += 1
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    print({"updated": updated, "backup": str(backup_path), "integrity": integrity})


if __name__ == "__main__":
    main()
