import csv
import html
import re
from pathlib import Path
from typing import Any

from app.database.database import get_connection

OUTPUT_DIR = Path("data/output")

SHOPIFY_CATEGORY_BY_GROUP = {
    "airtools": "Hardware > Tools",
    "handtools": "Hardware > Tools",
    "powertools": "Hardware > Tools",
    "speciality products": "Hardware > Tools",
}

SHOPIFY_CATEGORY_BY_CATEGORY = {
    "accessories": "Hardware > Tool Accessories",
    "bit sockets": "Hardware > Tool Accessories",
    "extension bars & accessories": "Hardware > Tool Accessories",
    "impact sockets ind. & acc.": "Hardware > Tool Accessories",
    "impact sockets indv. & accessories": "Hardware > Tool Accessories",
    "sockets": "Hardware > Tools",
    "spanners": "Hardware > Tools",
    "screwdrivers": "Hardware > Tools",
}

TITLE_KEEP_UPPER = {
    "ac",
    "dc",
    "eva",
    "hss",
    "led",
    "l&r",
    "o-ring",
    "ptfe",
    "sku",
    "sp",
    "tbv",
    "t.b.v.",
}


def make_handle(title: str, sku: str) -> str:
    text = f"{title or sku}".lower()
    text = text.replace('"', "")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:240] or sku.lower()


def format_price(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def _title_word(word: str) -> str:
    if not word:
        return word

    lower = word.lower()
    stripped = lower.strip(".,;:()[]")
    if stripped in {"tbv", "t.b.v."}:
        return "t.b.v."
    if stripped == "o-ring":
        return "O-ring"
    if stripped in TITLE_KEEP_UPPER:
        return word.upper()
    if re.search(r"\d", word):
        return word
    if "/" in word or "&" in word or "-" in word:
        return word
    return lower


def format_title(title: str | None, sku: str) -> str:
    text = (title or sku or "").strip()
    if not text:
        return sku

    if text == text.upper():
        words = [_title_word(word) for word in text.split()]
        text = " ".join(words)
        if text:
            text = text[0].upper() + text[1:]

    text = re.sub(r"\s+", " ", text).strip()
    return text or sku


def map_shopify_category(product: Any) -> str:
    category = (product["category"] or "").strip()
    category_full = (product["category_full"] or "").strip()
    product_group = (product["product_group"] or "").strip()

    for value in (category, category_full.split(">")[-1].strip() if category_full else ""):
        mapped = SHOPIFY_CATEGORY_BY_CATEGORY.get(value.lower())
        if mapped:
            return mapped

    return SHOPIFY_CATEGORY_BY_GROUP.get(product_group.lower(), "Hardware > Tools")


def format_variant_grams(value: Any) -> str:
    if value is None or value == "":
        return "0"
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return "0"

    if weight <= 0:
        return "0"
    if weight < 50:
        weight *= 1000
    return str(int(round(weight)))


def build_body_html(product: Any, title: str) -> str:
    existing_html = (product["description_html"] or "").strip()
    if existing_html:
        return existing_html

    details = []
    if product["sku"]:
        details.append(f"<li>SKU: {html.escape(product['sku'])}</li>")
    if product["product_group"]:
        details.append(f"<li>Productgroep: {html.escape(product['product_group'])}</li>")
    if product["category"]:
        details.append(f"<li>Categorie: {html.escape(product['category'])}</li>")
    if product["ean"]:
        details.append(f"<li>EAN: {html.escape(product['ean'])}</li>")

    body = [
        f"<h2>{html.escape(title)}</h2>",
        "<p>Professioneel SP Tools product voor werkplaats, montage en onderhoud.</p>",
    ]
    if details:
        body.append("<h3>Productdetails</h3>")
        body.append("<ul>")
        body.extend(details)
        body.append("</ul>")
    return "\n".join(body)


def truncate_words(text: str, max_length: int = 155) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_length:
        return text

    truncated = text[: max_length + 1].rsplit(" ", 1)[0].rstrip(" .,;:-")
    return f"{truncated}." if truncated else text[:max_length].rstrip()


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
                title = format_title(product["title_nl"] or product["name"], sku)
                handle = make_handle(title, sku)
                images = conn.execute(
                    "SELECT image_url, position FROM images WHERE sku=? ORDER BY position",
                    (sku,),
                ).fetchall()

                price = product["price"] if product["price"] is not None else product["sale_price"]
                seo_description = truncate_words(
                    f"{title} van SP Tools. Professioneel gereedschap voor werkplaats, montage en onderhoud."
                )

                base_row = {
                    "Handle": handle,
                    "Title": title,
                    "Body (HTML)": build_body_html(product, title),
                    "Vendor": "SP Tools",
                    "Product Category": map_shopify_category(product),
                    "Type": product["category"] or product["product_group"] or "",
                    "Tags": ", ".join(filter(None, ["SP Tools", product["category"], product["product_group"]])),
                    "Published": "FALSE",
                    "Option1 Name": "Title",
                    "Option1 Value": "Default Title",
                    "Variant SKU": sku,
                    "Variant Grams": format_variant_grams(product["weight_grams"]),
                    "Variant Inventory Tracker": "shopify",
                    "Variant Inventory Qty": "0",
                    "Variant Inventory Policy": "continue",
                    "Variant Fulfillment Service": "manual",
                    "Variant Price": format_price(price),
                    "Variant Barcode": product["ean"] or "",
                    "SEO Title": title[:70],
                    "SEO Description": seo_description,
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