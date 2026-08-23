from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.suppliers.hub import REGISTRY_PATH, _connect, get_supplier, utc_now


PROFILE_VERSION = 1


def default_enrichment_profile(slug: str) -> dict[str, Any]:
    tecweld = slug.casefold() == "tecweld"
    return {
        "version": PROFILE_VERSION,
        "status": "active",
        "exact_sku_required": True,
        "official_sources_only": True,
        "content": {
            "full_description": True,
            "feature_bullets": True,
            "technical_specifications": True,
            "feature_icons": tecweld,
            "translate_icon_labels": tecweld,
            "render_icons_in_product_html": tecweld,
            "include_icons_in_shopify": tecweld,
            "accessories": tecweld,
            "product_images": True,
            "documents": tecweld,
            "preserve_document_image_positions": tecweld,
            "include_documents_in_shopify": tecweld,
        },
        "translation": {
            "enabled": tecweld,
            "target_language": "nl-NL",
            "allow_summary": False,
            "preserve_paragraphs": True,
            "preserve_bullets": True,
            "preserve_technical_values": True,
            "minimum_length_percent": 55,
            "primary_model": "gpt-5.6-terra" if tecweld else "",
            "quality_fallback_model": "gpt-5.6-sol" if tecweld else "",
            "fallback_only_after_quality_failure": tecweld,
        },
        "overwrite": {
            "text": "if_more_complete",
            "images": "merge_verified",
            "manual_content": "never",
            "prices": "never",
            "stock": "never",
        },
        "execution": {
            "selected_product": True,
            "bulk_enrichment": True,
            "source_import": False,
            "scheduled_sync": False,
        },
        "quality": {
            "require_exact_official_page": True,
            "require_dutch_text": tecweld,
            "require_complete_translation": tecweld,
            "reject_related_product_images": True,
        },
    }


def get_enrichment_profile(slug: str) -> dict[str, Any]:
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    stored = (supplier.get("request_options") or {}).get("enrichment_profile")
    profile = default_enrichment_profile(slug)
    if isinstance(stored, dict):
        profile.update(deepcopy(stored))
        for section in ("content", "translation", "overwrite", "execution", "quality"):
            profile[section] = {
                **default_enrichment_profile(slug).get(section, {}),
                **deepcopy(stored.get(section) or {}),
            }
    return profile


def save_enrichment_profile(slug: str, profile: dict[str, Any]) -> dict[str, Any]:
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    profile = deepcopy(profile)
    profile["version"] = int(profile.get("version") or PROFILE_VERSION) + 1
    profile["status"] = "active"
    profile["updated_at"] = utc_now()
    options = dict(supplier.get("request_options") or {})
    history = list(options.get("enrichment_profile_history") or [])
    previous = options.get("enrichment_profile")
    if isinstance(previous, dict):
        history.append(previous)
    options["enrichment_profile_history"] = history[-10:]
    options["enrichment_profile"] = profile
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            "UPDATE suppliers SET request_options_json=?,updated_at=? WHERE slug=?",
            (json.dumps(options, ensure_ascii=False), utc_now(), slug),
        )
    return profile


def reset_enrichment_profile(slug: str) -> dict[str, Any]:
    return save_enrichment_profile(slug, default_enrichment_profile(slug))


def enrichment_enabled(slug: str, context: str) -> bool:
    if context not in {
        "selected_product", "bulk_enrichment", "source_import", "scheduled_sync"
    }:
        raise ValueError(f"Onbekende verrijkingscontext: {context}")
    return bool((get_enrichment_profile(slug).get("execution") or {}).get(context))


def run_profile_enrichment(
    slug: str, context: str, skus: list[str] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Run the active profile for a concrete execution chain."""
    if not enrichment_enabled(slug, context):
        return {"enabled": False, "processed": 0, "failed": 0, "details": []}
    from app.suppliers.hub import init_supplier_database
    from app.suppliers.on_demand_import import import_official_website_product

    if skus is None:
        with _connect(init_supplier_database(slug)) as connection:
            skus = [str(row[0]) for row in connection.execute(
                "SELECT sku FROM products WHERE source_present=1 ORDER BY sku"
            ).fetchall()]
    unique_skus = list(dict.fromkeys(str(sku).strip() for sku in skus if str(sku).strip()))
    details = []
    for index, sku in enumerate(unique_skus, 1):
        if progress_callback:
            progress_callback(index, len(unique_skus), f"{sku} verrijken volgens tab 8")
        try:
            result = import_official_website_product(
                slug, sku, execution_context=context
            )
            details.append({"sku": sku, "status": "completed", "result": result})
        except Exception as exc:
            details.append({"sku": sku, "status": "failed", "error": str(exc)})
    return {
        "enabled": True,
        "processed": sum(item["status"] == "completed" for item in details),
        "failed": sum(item["status"] == "failed" for item in details),
        "details": details,
    }
