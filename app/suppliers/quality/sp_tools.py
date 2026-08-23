from __future__ import annotations

import html
import json
from typing import Any

from .base import SupplierQualityPolicy


class SpToolsQualityPolicy(SupplierQualityPolicy):
    """Compacte kwaliteitsregels voor losse tools, bits en accessoires."""

    def __init__(self) -> None:
        super().__init__(
            description_minimum=60,
            tag_minimum=2,
            isolate_missing_image=True,
        )

    @staticmethod
    def _categories(product: dict[str, Any]) -> list[str]:
        try:
            filters = json.loads(product.get("filter_values_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            filters = []
        return list(dict.fromkeys(
            str(value).strip() for value in [
                product.get("product_group_name"), product.get("category"),
                product.get("category_full"), *filters,
            ] if str(value or "").strip()
        ))

    def description(self, product: dict[str, Any], source: str) -> str:
        title = html.escape(str(
            product.get("ai_title") or product.get("source_title")
            or product.get("sku") or ""
        ).strip())
        source_html = (
            source if "<" in source and ">" in source
            else (f"<p>{html.escape(source)}</p>" if source else "")
        )
        categories = "".join(
            f"<li>{html.escape(value)}</li>" for value in self._categories(product)
        )
        sku = html.escape(str(product.get("sku") or "").strip())
        return (
            f"<h2>{title}</h2>{source_html}<h3>Productinformatie</h3><ul>"
            f"<li><strong>Merk:</strong> SP Tools</li>{categories}"
            f"<li><strong>Artikelnummer:</strong> {sku}</li></ul>"
        )

    def tags(self, product: dict[str, Any], tags: list[str]) -> list[str]:
        return [*tags, "SP Tools", *self._categories(product)]


POLICY = SpToolsQualityPolicy()
