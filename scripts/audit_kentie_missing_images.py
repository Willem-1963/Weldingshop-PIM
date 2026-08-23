from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.suppliers.on_demand_import import (
    _official_product_search_url,
    _page_images_for_article,
)


HOSTS = {"kentie.shop"}
WEBSITE_URL = "https://www.kentie.shop/nl"


def resolve(sku: str) -> dict:
    url = _official_product_search_url(WEBSITE_URL, sku, HOSTS)
    images = _page_images_for_article(url, sku, HOSTS) if url else []
    return {"source_url": url, "images": images}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("output")
    parser.add_argument("--sample-size", type=int, default=120)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.database) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(
            """SELECT p.sku,p.source_title,p.product_group_name,p.category
                 FROM products p
                WHERE p.source_present=1
                  AND NOT EXISTS(
                      SELECT 1 FROM product_images i WHERE i.sku=p.sku
                  )
                ORDER BY p.sku"""
        )]
    sample_size = min(max(args.sample_size, 1), len(rows))
    indexes = sorted({
        round(index * (len(rows) - 1) / max(sample_size - 1, 1))
        for index in range(sample_size)
    })
    sample = [rows[index] for index in indexes]
    checked = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as pool:
        futures = {pool.submit(resolve, row["sku"]): row for row in sample}
        for future in as_completed(futures):
            row = futures[future]
            try:
                checked[row["sku"]] = future.result()
            except Exception as exc:
                checked[row["sku"]] = {"source_url": "", "images": [], "error": str(exc)}
    export_rows = []
    for row in rows:
        result = checked.get(row["sku"])
        export_rows.append({
            **row,
            "sample_checked": "yes" if result is not None else "no",
            "sample_result": (
                "photo_available_on_kentie_shop" if result and result.get("images")
                else "no_photo_on_exact_product_page" if result and result.get("source_url")
                else "exact_product_page_not_found" if result is not None
                else "not_sampled"
            ),
            "source_url": (result or {}).get("source_url") or "",
            "found_image_urls": " | ".join((result or {}).get("images") or []),
            "error": (result or {}).get("error") or "",
        })
    csv_path = output / "kentie-products-without-pim-photo.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=export_rows[0].keys())
        writer.writeheader()
        writer.writerows(export_rows)
    summary = {
        "products_without_pim_photo": len(rows),
        "sample_checked": len(sample),
        "photo_available_on_shop": sum(bool(item.get("images")) for item in checked.values()),
        "exact_page_without_photo": sum(bool(item.get("source_url")) and not item.get("images") for item in checked.values()),
        "exact_page_not_found": sum(not item.get("source_url") for item in checked.values()),
        "list": str(csv_path),
    }
    (output / "kentie-missing-image-sample-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
