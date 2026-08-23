#!/usr/bin/env python3
import json
import sqlite3
from datetime import datetime, timezone

from app.product_families import rebuild_product_families
from app.suppliers.certilas_surcharges import apply_certilas_alloy_surcharge_bundles
from app.suppliers.hub import supplier_database_path, utc_now


def main() -> dict:
    path = supplier_database_path("certilas")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.stem}-before-zz-surcharge-bundles-{stamp}.sqlite")
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        result = apply_certilas_alloy_surcharge_bundles(connection, utc_now())
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    families = rebuild_product_families("certilas", path)
    return {**result,"families":len(families),"backup":str(backup),"integrity":integrity}


if __name__ == "__main__": print(json.dumps(main(),ensure_ascii=False))
