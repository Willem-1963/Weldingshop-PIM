from __future__ import annotations

import html
import json
from typing import Any

from .base import SupplierQualityPolicy


class ValkenpowerQualityPolicy(SupplierQualityPolicy):
    def description(self, product: dict[str, Any], source: str) -> str:
        title = html.escape(str(
            product.get("ai_title") or product.get("source_title")
            or product.get("sku") or ""
        ).strip())
        source_html = source if "<" in source else f"<p>{html.escape(source)}</p>"
        return (
            f"<p><strong>{title}</strong></p>{source_html}"
            "<h2>Productinformatie</h2><p>Controleer vóór gebruik of dit artikel "
            "geschikt is voor de beoogde toepassing. Raadpleeg de productspecificaties "
            "en meegeleverde documentatie voor maatvoering, capaciteit, aansluiting, "
            "montage, onderhoud en veilig gebruik. Vergelijk het artikelnummer en de "
            "aansluiting met het gereedschap of onderdeel waarvoor het product bedoeld "
            "is. Neem bij twijfel over de juiste uitvoering contact met ons op.</p>"
        )

    def tags(self, product: dict[str, Any], tags: list[str]) -> list[str]:
        try:
            filters = json.loads(product.get("filter_values_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            filters = []
        values = [
            product.get("vendor"), product.get("product_group_name"),
            *filters,
            product.get("execution"), product.get("subcategory_3"),
            product.get("subcategory_4"), product.get("subcategory_5"),
        ]
        return [*tags, *(str(value).strip() for value in values if str(value or "").strip())]

    def validation_errors(
        self, product: dict[str, Any], description: str, tags: list[str],
    ) -> list[str]:
        evidence = (product.get("_raw_data") or {}).get(
            "valkenpower_category_evidence"
        ) or {}
        if (
            evidence.get("method") != "official_product_page_breadcrumb"
            or not evidence.get("hierarchy")
        ):
            return ["officiële Valkenpower-breadcrumb ontbreekt"]
        return []


POLICY = ValkenpowerQualityPolicy(isolate_incomplete_content=True)
