from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.suppliers.hub import (  # noqa: E402
    REGISTRY_PATH,
    _connect,
    supplier_database_path,
    utc_now,
)


SLUG = "rhodius-abrasives-gmbh"
WEBSITE_URL = "https://products.rhodius-abrasives.com/nl/"
CATALOGUE_PATH = (
    ROOT / "data/imports/rhodius-abrasives-gmbh/"
    "RHO_HQ_Katalog_2026-2027-nl-NL_low.pdf"
)
PAGE_FIELD = "Pag. (Catalogus 2026/2027)"


def main() -> dict[str, object]:
    pdf_bytes = CATALOGUE_PATH.read_bytes()
    reader = PdfReader(CATALOGUE_PATH)
    page_texts = [page.extract_text() or "" for page in reader.pages]

    found = 0
    missing: list[str] = []
    database = supplier_database_path(SLUG)
    with _connect(database) as connection:
        products = connection.execute(
            "SELECT sku,raw_data_json FROM products ORDER BY sku"
        ).fetchall()
        for product in products:
            sku = str(product["sku"] or "").strip()
            pattern = re.compile(rf"(?<!\d){re.escape(sku)}(?!\d)")
            page_number = next(
                (
                    index for index, text in enumerate(page_texts, start=1)
                    if pattern.search(text)
                ),
                None,
            )
            raw_data = json.loads(product["raw_data_json"] or "{}")
            if page_number is None:
                raw_data.pop(PAGE_FIELD, None)
                missing.append(sku)
            else:
                raw_data[PAGE_FIELD] = page_number
                found += 1
            connection.execute(
                "UPDATE products SET raw_data_json=?,updated_at=? WHERE sku=?",
                (json.dumps(raw_data, ensure_ascii=False), utc_now(), sku),
            )

    with _connect(REGISTRY_PATH) as connection:
        supplier = connection.execute(
            "SELECT request_options_json FROM suppliers WHERE slug=?", (SLUG,)
        ).fetchone()
        if not supplier:
            raise ValueError(f"Onbekende leverancier: {SLUG}")
        options = json.loads(supplier["request_options_json"] or "{}")
        options["catalogue_source"] = {
            "label": "RHODIUS catalogus 2026/2027 NL",
            "path": str(CATALOGUE_PATH),
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "pages": len(page_texts),
            "page_field": PAGE_FIELD,
            "pdf_page_equals_printed_page": True,
            "text_layer": True,
            "matched_products": found,
            "unmatched_products": len(missing),
            "match_method": "exact_sku_in_pdf_text",
            "registered_at": utc_now(),
        }
        options["enrichment_source_priority"] = [
            "supplier_excel",
            "catalogue_2026_2027_exact_page",
            "official_product_website_exact_sku_or_ean",
            "shopify_images_exact_sku",
        ]
        connection.execute(
            """
            UPDATE suppliers
            SET website_url=?,catalogue_url=?,website_match_policy=?,
                ai_research_allowed=1,request_options_json=?,updated_at=?
            WHERE slug=?
            """,
            (
                WEBSITE_URL,
                str(CATALOGUE_PATH),
                "exact_sku_or_ean",
                json.dumps(options, ensure_ascii=False),
                utc_now(),
                SLUG,
            ),
        )
    return {
        "catalogue": str(CATALOGUE_PATH),
        "pages": len(page_texts),
        "products_with_2026_2027_page": found,
        "products_not_in_catalogue": len(missing),
        "missing_skus": missing,
        "website": WEBSITE_URL,
    }


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
