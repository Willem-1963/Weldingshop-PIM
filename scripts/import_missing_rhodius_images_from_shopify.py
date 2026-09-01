from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.shopify.client import ShopifyClient
from app.shopify.sync import _shopify_products
from app.suppliers.hub import _connect, supplier_database_path


SLUG = "rhodius-abrasives-gmbh"


def media_images(nodes: list[dict]) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for node in nodes:
        url = str((node.get("image") or {}).get("url") or "").strip()
        if url and url not in {item["image_url"] for item in images}:
            images.append({"image_url": url, "alt_text": ""})
    return images


def main(*, apply: bool) -> dict[str, int]:
    client = ShopifyClient.from_settings()
    shopify_by_sku = _shopify_products(
        client, "Rhodius", supplier_slug=SLUG
    )
    database = supplier_database_path(SLUG)
    stats = {
        "missing_in_pim": 0,
        "exact_shopify_matches": 0,
        "safe_matches_with_images": 0,
        "images_added": 0,
        "skipped_multi_variant_without_variant_media": 0,
    }
    with _connect(database) as connection:
        missing = connection.execute(
            """
            SELECT p.sku,p.source_title
            FROM products p
            WHERE NOT EXISTS(
                SELECT 1 FROM product_images i WHERE i.sku=p.sku
            )
            ORDER BY p.sku
            """
        ).fetchall()
        stats["missing_in_pim"] = len(missing)
        for row in missing:
            match = shopify_by_sku.get(str(row["sku"]).strip().upper())
            if not match:
                continue
            stats["exact_shopify_matches"] += 1
            product = match["product"]
            variant = match["variant"]
            images = media_images((variant.get("media") or {}).get("nodes") or [])
            variant_count = len((product.get("variants") or {}).get("nodes") or [])
            if not images and variant_count == 1:
                images = media_images((product.get("media") or {}).get("nodes") or [])
            elif not images and variant_count > 1:
                stats["skipped_multi_variant_without_variant_media"] += 1
            if not images:
                continue
            stats["safe_matches_with_images"] += 1
            if not apply:
                continue
            for position, image in enumerate(images, start=1):
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO product_images(
                        sku,image_url,position,alt_text
                    ) VALUES(?,?,?,?)
                    """,
                    (
                        row["sku"], image["image_url"], position,
                        row["source_title"] or f"Rhodius {row['sku']}",
                    ),
                )
                stats["images_added"] += int(cursor.rowcount > 0)
        if not apply:
            connection.rollback()
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true",
        help="Sla veilige exacte Shopify-afbeeldingsmatches op in de PIM.",
    )
    arguments = parser.parse_args()
    print(main(apply=arguments.apply))
