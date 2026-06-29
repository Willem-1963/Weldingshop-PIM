from __future__ import annotations

import csv
import re
from pathlib import Path

from app.database.database import get_connection

OUTPUT_DIR = Path("data/output")


def make_handle(title: str, sku: str) -> str:
    text = f"{title or sku}".lower()
    text = text.replace('"', "")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:240] or sku.lower()


SHOPIFY_COLUMNS = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Product Category",
    "Type",
    "Tags",
    "Published",
    "Option1 Name",
    "Option1 Value",
    "Variant SKU",
    "Variant Grams",
    "Variant Inventory Tracker",
    "Variant Inventory Qty",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Variant Barcode",
    "Image Src",
    "Image Position",
    "SEO Title",
    "SEO Description",
    "Status",
]


def export_shopify_csv(limit: int = 100, output_name: str = "shopify_batch_001.csv") -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / output_name

    with get_connection() as conn:
        products = conn.execute(
            """
            SELECT * FROM products
            ORDER BY sku
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        with output_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=SHOPIFY_COLUMNS)
            writer.writeheader()

            for product in products:
                sku = product["sku"]
                title = product["title_nl"] or product["name"] or sku
                handle = make_handle(title, sku)
                images = conn.execute(
                    "SELECT image_url, position FROM images WHERE sku=? ORDER BY position",
                    (sku,),
                ).fetchall()

                base_row = {
                    "Handle": handle,
                    "Title": title,
                    "Body (HTML)": product["description_html"] or "",
                    "Vendor": "SP Tools",
                    "Product Category": "",
                    "Type": product["category"] or product["product_group"] or "",
                    "Tags": ", ".join(filter(None, ["SP Tools", product["category"], product["product_group"]])),
                    "Published": "FALSE",
                    "Option1 Name": "Title",
                    "Option1 Value": "Default Title",
                    "Variant SKU": sku,
                    "Variant Grams": int(product["weight_grams"] or 0),
                    "Variant Inventory Tracker": "shopify",
                    "Variant Inventory Qty": "0",
                    "Variant Inventory Policy": "continue",
                    "Variant Fulfillment Service": "manual",
                    "Variant Price": product["price"] or product["sale_price"] or "",
                    "Variant Barcode": product["ean"] or "",
                    "SEO Title": title[:70],
                    "SEO Description": f"{title} van SP Tools. Professioneel gereedschap voor werkplaats en industrie."[:320],
                    "Status": "draft",
                }

                if images:
                    for i, img in enumerate(images, start=1):
                        row = dict(base_row)
                        row["Image Src"] = img["image_url"]
                        row["Image Position"] = str(i)
                        if i > 1:
                            # Shopify verwacht bij extra afbeeldingen alleen Handle + Image velden
                            for key in SHOPIFY_COLUMNS:
                                if key not in ["Handle", "Image Src", "Image Position"]:
                                    row[key] = ""
                        writer.writerow(row)
                else:
                    row = dict(base_row)
                    row["Image Src"] = ""
                    row["Image Position"] = ""
                    writer.writerow(row)

    return output_path


if __name__ == "__main__":
    path = export_shopify_csv()
    print(f"Shopify CSV gemaakt: {path}")
