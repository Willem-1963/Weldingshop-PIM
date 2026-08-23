from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from bs4 import BeautifulSoup, NavigableString

from app.suppliers.hub import supplier_database_path, utc_now


LIST_HEADINGS = {"Eigenschappen", "Toepassingen", "Goedkeuringen"}


def clean_html(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for heading in soup.find_all("h3"):
        if heading.get_text(" ", strip=True) not in LIST_HEADINGS:
            continue
        paragraph = heading.find_next_sibling("p")
        if paragraph is None or ".," not in paragraph.get_text():
            continue
        items = [
            item.strip().rstrip(",")
            for item in re.split(r"\.\s*,\s*", paragraph.get_text(" ", strip=True))
            if item.strip().rstrip(",")
        ]
        listing = soup.new_tag("ul")
        for item in items:
            li = soup.new_tag("li")
            li.string = item if item.endswith((".", ":", ";")) else item + "."
            listing.append(li)
        paragraph.replace_with(listing)
    for node in list(soup.find_all(string=lambda text: text and ".," in text)):
        node.replace_with(NavigableString(re.sub(r"\.\s*,\s*", ". ", str(node))))
    return str(soup)


def main() -> None:
    path = supplier_database_path("certilas")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(f"{path.stem}-before-list-cleanup-{stamp}.sqlite")
    with sqlite3.connect(path) as source, sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    updated = 0
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT sku,html_description FROM products WHERE html_description LIKE '%.,%'"
        ).fetchall()
        for sku, description in rows:
            cleaned = clean_html(description)
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
