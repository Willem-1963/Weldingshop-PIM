from __future__ import annotations

import json
import re
from typing import Any

from app.suppliers.hub import _connect, init_supplier_database, utc_now


KEYWORD_TAGS = {
    "accu": "Accugereedschap",
    "bit": "Bits",
    "boren": "Boren",
    "boor": "Boren",
    "dop": "Doppen",
    "doppen": "Doppen",
    "gereedschapswagen": "Gereedschapswagens",
    "hamer": "Hamers",
    "inbus": "Inbus",
    "krik": "Krikken",
    "lucht": "Luchtgereedschap",
    "ratel": "Ratels",
    "schroevendraaier": "Schroevendraaiers",
    "slijper": "Slijpen",
    "tang": "Tangen",
    "toolkit": "Gereedschapsets",
    "werkplaats": "Werkplaatsinrichting",
}


def generate_relevant_tags(product: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for value in (
        product.get("brand"),
        product.get("vendor"),
        product.get("category"),
        product.get("product_type"),
        product.get("product_group_name"),
    ):
        cleaned = str(value or "").strip()
        if cleaned and not cleaned.isdigit():
            tags.append(cleaned)
    category_path = str(product.get("category_full") or "")
    tags.extend(
        part.strip()
        for part in category_path.split(">")
        if part.strip() and not part.strip().isdigit()
    )
    product_group = str(product.get("product_group_name") or "")
    tags.extend(
        part.strip()
        for part in re.split(r"[/|>]", product_group)
        if part.strip() and not part.strip().isdigit()
    )
    supplier_names = {
        str(product.get(field) or "").strip().casefold()
        for field in ("brand", "vendor")
    }
    if "certilas" in supplier_names:
        tags.append("Lasbenodigdheden")
    searchable = " ".join(
        str(product.get(field) or "")
        for field in ("source_title", "source_description", "category_full")
    ).casefold()
    words = set(re.findall(r"[a-z0-9]+", searchable))
    for keyword, tag in KEYWORD_TAGS.items():
        if keyword in words:
            tags.append(tag)
    return list(dict.fromkeys(tag[:255] for tag in tags if tag.strip()))[:50]


def refresh_product_tags(slug: str) -> dict[str, int]:
    path = init_supplier_database(slug)
    with _connect(path) as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()
        }
        if "ai_tags_json" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN ai_tags_json TEXT NOT NULL DEFAULT '[]'"
            )
        rows = conn.execute(
            """
            SELECT sku,brand,vendor,source_title,source_description,
                product_type,category,category_full,product_group_name
            FROM products WHERE source_present=1
            """
        ).fetchall()
        for row in rows:
            tags = generate_relevant_tags(dict(row))
            conn.execute(
                "UPDATE products SET ai_tags_json=?,updated_at=? WHERE sku=?",
                (json.dumps(tags, ensure_ascii=False), utc_now(), row["sku"]),
            )
    return {"updated": len(rows)}
