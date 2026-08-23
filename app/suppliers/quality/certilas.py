from __future__ import annotations

import json
from typing import Any

from app.suppliers.hub import (
    CERTILAS_ALLOWED_MATERIALS,
    CERTILAS_ALLOWED_PROCESSES,
)

from .base import SupplierQualityPolicy


class CertilasQualityPolicy(SupplierQualityPolicy):
    FALLBACK_IMAGES = {
        "TIG": "https://cdn.shopify.com/s/files/1/0086/2581/5612/files/certilas-illustration-alloy-rods.png?v=1785660894",
        "MIG": "https://cdn.shopify.com/s/files/1/0086/2581/5612/files/certilas-illustration-steel-spool.png?v=1785660899",
    }

    def fallback_image(self, product: dict[str, Any]) -> dict[str, str] | None:
        try:
            raw = json.loads(product.get("raw_data_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            raw = {}
        group = str(raw.get("Description product group") or "").upper()
        process = "TIG" if group.startswith("GTAW") else (
            "MIG" if group.startswith("GMAW") else ""
        )
        if not process:
            return None
        return {
            "image_url": self.FALLBACK_IMAGES[process],
            "alt_text": f"Algemene Certilas {process}-productafbeelding",
        }

    def validation_errors(
        self, product: dict[str, Any], description: str, tags: list[str],
    ) -> list[str]:
        errors = []
        try:
            values = json.loads(product.get("filter_values_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            values = []
        prefixes = ["Lasproces: ", "Materiaal: "]
        if len(values) != 2 or any(
            not isinstance(value, str) or not value.startswith(prefix)
            for value, prefix in zip(values, prefixes)
        ):
            errors.append("filtercontract vereist exact Lasproces en Materiaal")
        else:
            process = values[0].split(":", 1)[1].strip()
            material = values[1].split(":", 1)[1].strip()
            if process not in CERTILAS_ALLOWED_PROCESSES:
                errors.append(f"ongeldig vast lasproces: {process}")
            if material not in CERTILAS_ALLOWED_MATERIALS:
                errors.append(f"ongeldig vast materiaal: {material}")
        try:
            enrichment = (product.get("_raw_data") or {}).get(
                "website_enrichment"
            ) or {}
            position_images = (
                (enrichment.get("facts") or {}).get("welding_position_images")
                or {}
            )
        except AttributeError:
            position_images = {}
        missing = [
            code for code in position_images
            if f"certilas-laspositie-{str(code).casefold()}.png" not in description
        ]
        if missing:
            errors.append("laspositie-iconen ontbreken: " + ", ".join(missing))
        return errors


POLICY = CertilasQualityPolicy(description_minimum=300, tag_minimum=3)
