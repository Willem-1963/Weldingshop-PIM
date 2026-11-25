from __future__ import annotations

import json
import re
from typing import Any

from app.suppliers.hub import _connect, init_supplier_database, utc_now
from app.suppliers.category_locks import install_category_locks
from app.suppliers.quality import quality_policy_for
from app.suppliers.tags import generate_relevant_tags


PUTKRIK_TAXONOMY = {
    "product_group_name": "Werkplaatsuitrusting",
    "filter_values_json": ["Brugkrikken en putkrikken"],
    "execution": "Putkrikken",
    "subcategory_3": "",
    "subcategory_4": "",
    "subcategory_5": "",
    "productsoort": "Brugkrikken en putkrikken",
    "keep_active_at_zero": True,
    "collection_tags_json": [
        "cat-gereedschap",
        "cat-werkplaatsuitrusting",
        "cat-hefbruggen",
        "filter-productsoort-brugkrikken-en-putkrikken",
        "ws-tax-hefbruggen",
    ],
}

KNEE_PROTECTION_TAXONOMY = {
    "product_group_name": "PBM",
    "filter_values_json": ["Voetbescherming"],
    "execution": "Kniebescherming en kniestukken",
    "subcategory_3": "",
    "subcategory_4": "",
    "subcategory_5": "",
    "productsoort": "Kniebescherming",
    "collection_tags_json": [
        "cat-pbm",
        "cat-pbm-voetbescherming",
        "cat-pbm-voetbescherming-kniebescherming",
        "ws-tax-been-en-voetbescherming",
    ],
}

NEW_TAXONOMY_TAG_PREFIXES = ("cat-", "filter-", "ws-tax-")


def _is_putkrik(product: dict[str, Any]) -> bool:
    sku = str(product.get("sku") or "").strip().upper()
    title = str(product.get("source_title") or "").strip().casefold()
    return bool(re.fullmatch(r"VP-PK[A-Z0-9]+", sku)) and "putkrik" in title


def _explicit_taxonomy_rule(
    product: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    title = str(product.get("source_title") or "").strip().casefold()
    if _is_putkrik(product):
        return "valkenpower-putkrik-title-and-sku-rule-v1", PUTKRIK_TAXONOMY
    if re.search(r"\b(kniebeschermer|kniebeschermers|kniestuk|kniestukken|kneepad|kneepads)\b", title):
        return "valkenpower-knee-protection-title-rule-v1", KNEE_PROTECTION_TAXONOMY
    return None


def _collection_tags(values: dict[str, Any]) -> list[str]:
    source = values.get("collection_tags_json") or []
    if isinstance(source, list):
        return [str(tag).strip() for tag in source if str(tag).strip()]
    try:
        parsed = json.loads(str(source))
    except (json.JSONDecodeError, TypeError):
        parsed = []
    return [str(tag).strip() for tag in parsed if str(tag).strip()]


def _approved_hierarchy_templates(connection) -> dict[str, dict[str, Any]]:
    """Return only exact official hierarchies with one unanimous new route."""
    candidates: dict[str, dict[str, str]] = {}
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='category_import_locks'"
    ).fetchone():
        return {}
    rows = connection.execute(
        """SELECT e.hierarchy_json,l.values_json,l.sku
             FROM official_category_evidence e
             JOIN products p ON p.supplier_sku=e.supplier_sku
             JOIN category_import_locks l ON l.sku=p.sku
            WHERE e.status='confirmed'"""
    ).fetchall()
    for raw_row in rows:
        row = dict(raw_row)
        try:
            values = json.loads(row.get("values_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not any(
            tag.casefold().startswith(NEW_TAXONOMY_TAG_PREFIXES)
            for tag in _collection_tags(values)
        ):
            continue
        hierarchy = str(row.get("hierarchy_json") or "")
        canonical = json.dumps(values, ensure_ascii=False, sort_keys=True)
        candidates.setdefault(hierarchy, {})[canonical] = str(row.get("sku") or "")
    return {
        hierarchy: {
            "values": json.loads(next(iter(routes))),
            "template_sku": next(iter(routes.values())),
        }
        for hierarchy, routes in candidates.items()
        if hierarchy and len(routes) == 1
    }


def _ensure_review_queue(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS valkenpower_category_review_queue(
               sku TEXT PRIMARY KEY,source_title TEXT NOT NULL,
               hierarchy_json TEXT NOT NULL DEFAULT '[]',reason TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'pending',first_seen_at TEXT NOT NULL,
               last_seen_at TEXT NOT NULL)"""
    )


def _queue_category_review(
    connection, product: dict[str, Any], hierarchy_json: str, reason: str,
) -> None:
    now = utc_now()
    connection.execute(
        """INSERT INTO valkenpower_category_review_queue(
               sku,source_title,hierarchy_json,reason,status,first_seen_at,last_seen_at
           ) VALUES(?,?,?,?,'pending',?,?)
           ON CONFLICT(sku) DO UPDATE SET
               source_title=excluded.source_title,
               hierarchy_json=excluded.hierarchy_json,
               reason=excluded.reason,
               status='pending',last_seen_at=excluded.last_seen_at""",
        (
            product["sku"], str(product.get("source_title") or ""),
            hierarchy_json or "[]", reason, now, now,
        ),
    )


def _install_automatic_category_locks(connection) -> dict[str, int]:
    existing_locks = {
        str(row[0]).upper()
        for row in connection.execute(
            "SELECT sku FROM category_import_locks"
        ).fetchall()
    } if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='category_import_locks'"
    ).fetchone() else set()
    _ensure_review_queue(connection)
    templates = _approved_hierarchy_templates(connection)
    records = []
    rule_locks = 0
    inherited_locks = 0
    review_pending = 0
    for row in connection.execute(
        """SELECT p.sku,p.source_title,e.status AS breadcrumb_status,
                  e.hierarchy_json
             FROM products p
             LEFT JOIN official_category_evidence e
               ON e.supplier_sku=p.supplier_sku
            WHERE p.source_present=1"""
    ).fetchall():
        product = dict(row)
        sku = product["sku"].upper()
        if sku in existing_locks:
            connection.execute(
                "DELETE FROM valkenpower_category_review_queue WHERE sku=?", (sku,)
            )
            continue
        explicit = _explicit_taxonomy_rule(product)
        hierarchy_json = str(product.get("hierarchy_json") or "")
        if explicit:
            method, values = explicit
            rule_locks += 1
            provenance = {
                "method": method,
                "source": "supplier_feed",
                "matched_title": product["source_title"],
                "values": values,
            }
        elif product.get("breadcrumb_status") == "confirmed" and hierarchy_json in templates:
            template = templates[hierarchy_json]
            inherited_locks += 1
            provenance = {
                "method": "valkenpower-exact-official-hierarchy-template-v1",
                "source": "official_product_page_breadcrumb",
                "official_hierarchy": json.loads(hierarchy_json),
                "template_sku": template["template_sku"],
                "values": template["values"],
            }
        else:
            reason = (
                "official_breadcrumb_missing"
                if product.get("breadcrumb_status") != "confirmed"
                else "no_unanimous_approved_new_taxonomy_template"
            )
            _queue_category_review(connection, product, hierarchy_json, reason)
            review_pending += 1
            continue
        records.append({
            "sku": product["sku"],
            "provenance": provenance,
        })
    if records:
        install_category_locks(connection, records)
        connection.executemany(
            "DELETE FROM valkenpower_category_review_queue WHERE sku=?",
            [(record["sku"],) for record in records],
        )
    connection.execute(
        """DELETE FROM valkenpower_category_review_queue
              WHERE sku NOT IN (SELECT sku FROM products WHERE source_present=1)"""
    )
    return {
        "installed": len(records),
        "explicit_rules": rule_locks,
        "inherited_exact_hierarchy": inherited_locks,
        "review_pending": review_pending,
    }


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
        "automatic_category_locks": 0,
        "automatic_rule_locks": 0,
        "automatic_inherited_locks": 0,
        "category_review_pending": 0,
    }
    with _connect(path) as connection:
        automatic = _install_automatic_category_locks(connection)
        counts["automatic_category_locks"] = automatic["installed"]
        counts["automatic_rule_locks"] = automatic["explicit_rules"]
        counts["automatic_inherited_locks"] = automatic[
            "inherited_exact_hierarchy"
        ]
        counts["category_review_pending"] = automatic["review_pending"]
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
