#!/usr/bin/env python3
"""Verrijk uitsluitend de Certilas 309LSi-familie met SKU 32715 opnieuw."""

import json
import sqlite3
from datetime import datetime, timezone

from app.product_families import rebuild_product_families
from app.suppliers.hub import supplier_database_path, utc_now
from app.suppliers.website_enrichment import research_certilas_family
from scripts.apply_certilas_contextual_explanations import enrich


FAMILY_KEY = "certilas|gtaw|309lsi|staaf|1000-mm"


def main() -> dict:
    path = supplier_database_path("certilas")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.stem}-before-32715-repair-{stamp}.sqlite")
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    result = research_certilas_family(FAMILY_KEY, database_path=path)
    linked = 0
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """SELECT p.sku,p.html_description FROM products p
               JOIN product_family_variants v ON v.sku=p.sku
               WHERE v.family_key=? AND COALESCE(p.html_description,'')<>''""",
            (FAMILY_KEY,),
        ).fetchall()
        for sku, value in rows:
            updated, _ = enrich(value)
            if updated != value:
                connection.execute(
                    "UPDATE products SET html_description=?,updated_at=? WHERE sku=?",
                    (updated, utc_now(), sku),
                )
                linked += 1
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    rebuild_product_families("certilas", path)
    return {**result, "links_updated": linked, "backup": str(backup), "integrity": integrity}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
