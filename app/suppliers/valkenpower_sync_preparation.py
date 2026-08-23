from __future__ import annotations

import json
from typing import Any

from app.suppliers.hub import _connect, init_supplier_database, utc_now
from app.suppliers.quality import quality_policy_for
from app.suppliers.tags import generate_relevant_tags


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        identity = cleaned.casefold()
        if cleaned and identity not in seen:
            seen.add(identity)
            result.append(cleaned[:255])
    return result


def prepare_valkenpower_products_for_sync() -> dict[str, int]:
    """Persist verified descriptions and tags before the Shopify preflight."""
    path = init_supplier_database("valkenpower")
    policy = quality_policy_for("Valkenpower", supplier_slug="valkenpower")
    counts = {
        "checked": 0,
        "prepared": 0,
        "ready": 0,
        "awaiting_official_breadcrumb": 0,
    }
    with _connect(path) as connection:
        rows = connection.execute(
            """SELECT p.*,e.status AS breadcrumb_status,
                      e.hierarchy_json AS official_hierarchy_json,
                      e.source_url AS official_source_url,
                      e.official_product_code,e.matched_by,e.verified_at
                 FROM products p
                 LEFT JOIN official_category_evidence e
                   ON e.supplier_sku=p.supplier_sku
                WHERE p.source_present=1"""
        ).fetchall()
        for raw_row in rows:
            counts["checked"] += 1
            row = dict(raw_row)
            if row.get("breadcrumb_status") != "confirmed":
                counts["awaiting_official_breadcrumb"] += 1
                continue
            try:
                hierarchy = json.loads(row.get("official_hierarchy_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                hierarchy = []
            tags = _unique([
                *generate_relevant_tags(row),
                *hierarchy,
                *policy.tags(row, []),
            ])
            description = str(row.get("html_description") or "").strip()
            if not description:
                description = policy.description(
                    row, str(row.get("source_description") or "")
                )
            if policy.tags_are_complete(tags) and policy.description_is_complete(
                description
            ):
                counts["ready"] += 1
            try:
                product_raw = json.loads(row.get("raw_data_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                product_raw = {}
            product_raw["valkenpower_category_evidence"] = {
                "method": "official_product_page_breadcrumb",
                "source_url": str(row.get("official_source_url") or ""),
                "hierarchy": hierarchy,
                "official_product_code": str(
                    row.get("official_product_code") or ""
                ),
                "matched_by": str(row.get("matched_by") or ""),
                "verified_at": str(row.get("verified_at") or ""),
                "policy": "valkenpower-official-breadcrumb-v1",
            }
            try:
                current_tags = _unique(json.loads(row.get("ai_tags_json") or "[]"))
            except (json.JSONDecodeError, TypeError):
                current_tags = []
            serialized_raw = json.dumps(product_raw, ensure_ascii=False)
            if (
                serialized_raw != str(row.get("raw_data_json") or "{}")
                or description != str(row.get("html_description") or "").strip()
                or [tag.casefold() for tag in tags]
                != [tag.casefold() for tag in current_tags]
            ):
                connection.execute(
                    """UPDATE products SET html_description=?,ai_tags_json=?,
                              raw_data_json=?,updated_at=?
                         WHERE sku=?""",
                    (
                        description,
                        json.dumps(tags, ensure_ascii=False),
                        serialized_raw,
                        utc_now(),
                        row["sku"],
                    ),
                )
                counts["prepared"] += 1
    return counts
