from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from app.suppliers.on_demand_import import (
    _official_product_search_url,
    _page_images_for_article,
)


HOSTS = {"kentie.shop"}
WEBSITE_URL = "https://www.kentie.shop/nl"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve(sku: str) -> dict:
    url = _official_product_search_url(WEBSITE_URL, sku, HOSTS)
    images = _page_images_for_article(url, sku, HOSTS) if url else []
    return {"sku": sku, "source_url": url, "images": images}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("output")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(args.database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT p.sku,p.source_title,p.raw_data_json
                 FROM products p LEFT JOIN product_images i ON i.sku=p.sku
                WHERE p.source_present=1 GROUP BY p.sku HAVING COUNT(i.id)=0
                ORDER BY p.sku"""
        ).fetchall()
    targets = []
    for row in rows:
        raw = json.loads(row["raw_data_json"] or "{}")
        audit = ((raw.get("website_import") or {}).get("image_audit") or {})
        if audit.get("status") == "rejected_non_product_page":
            targets.append({"sku": row["sku"], "title": row["source_title"] or ""})

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as pool:
        futures = {pool.submit(resolve, item["sku"]): item for item in targets}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results[item["sku"]] = future.result()
            except Exception as exc:
                results[item["sku"]] = {
                    "sku": item["sku"], "source_url": "", "images": [],
                    "error": str(exc),
                }

    timestamp = now()
    with sqlite3.connect(args.database) as connection:
        connection.row_factory = sqlite3.Row
        for item in targets:
            result = results[item["sku"]]
            row = connection.execute(
                "SELECT raw_data_json FROM products WHERE sku=?", (item["sku"],)
            ).fetchone()
            raw = json.loads(row["raw_data_json"] or "{}")
            website_import = dict(raw.get("website_import") or {})
            website_import.update({
                "source_url": result.get("source_url") or "",
                "matched_by": "supplier_article_number",
                "matched_value": item["sku"],
                "verification": "official_product_page" if result.get("source_url") else "not_found",
                "researched_at": timestamp,
                "image_urls": result.get("images") or [],
                "image_audit": {
                    "status": (
                        "verified_product_images" if result.get("images")
                        else "verified_no_product_image" if result.get("source_url")
                        else "exact_product_not_found"
                    ),
                    "audited_at": timestamp,
                },
            })
            raw["website_import"] = website_import
            connection.execute(
                "UPDATE products SET raw_data_json=?,updated_at=? WHERE sku=?",
                (json.dumps(raw, ensure_ascii=False), timestamp, item["sku"]),
            )
            for position, image_url in enumerate(result.get("images") or [], 1):
                connection.execute(
                    """INSERT OR IGNORE INTO product_images(
                           sku,image_url,position,alt_text) VALUES(?,?,?,?)""",
                    (item["sku"], image_url, position, item["title"]),
                )

    report = [{
        **item,
        "source_url": results[item["sku"]].get("source_url") or "",
        "image_count": len(results[item["sku"]].get("images") or []),
        "image_urls": " | ".join(results[item["sku"]].get("images") or []),
        "error": results[item["sku"]].get("error") or "",
    } for item in targets]
    csv_path = output / "kentie-rejected-image-repair.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=report[0].keys())
        writer.writeheader()
        writer.writerows(report)
    summary = {
        "targeted": len(report),
        "exact_product_pages": sum(bool(row["source_url"]) for row in report),
        "products_with_images": sum(row["image_count"] > 0 for row in report),
        "images": sum(row["image_count"] for row in report),
        "no_image_on_exact_page": sum(bool(row["source_url"]) and row["image_count"] == 0 for row in report),
        "not_found": sum(not row["source_url"] for row in report),
        "report": str(csv_path),
    }
    (output / "kentie-rejected-image-repair.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
