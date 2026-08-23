from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SupplierQualityPolicy:
    description_minimum: int = 300
    tag_minimum: int = 3
    image_minimum: int = 1
    isolate_missing_image: bool = False
    isolate_incomplete_content: bool = False

    def description(self, product: dict[str, Any], source: str) -> str:
        return source

    def tags(self, product: dict[str, Any], tags: list[str]) -> list[str]:
        return tags

    def description_is_complete(self, description: str) -> bool:
        value = str(description or "").strip()
        return len(value) >= self.description_minimum and "<" in value

    def tags_are_complete(self, tags: list[str]) -> bool:
        # Shopify treats tags that only differ in casing as the same tag.
        # Validate against that same representation so the preflight check
        # cannot pass with values such as ``manometer`` and ``Manometer``.
        unique_tags = {
            str(tag).strip().casefold()
            for tag in tags
            if str(tag).strip()
        }
        return len(unique_tags) >= self.tag_minimum

    def validation_errors(
        self, product: dict[str, Any], description: str, tags: list[str],
    ) -> list[str]:
        """Supplier-specific contracts; the generic synchronizer stays neutral."""
        return []

    def fallback_image(self, product: dict[str, Any]) -> dict[str, str] | None:
        return None


DEFAULT_POLICY = SupplierQualityPolicy()
