"""Ultimatron battery facets come from structured nominal specifications."""
import re
from decimal import Decimal
from typing import Any


def battery_classification(raw: dict[str, Any]) -> tuple[str, list[str]]:
    voltage = raw.get("Nominal Voltage")
    if voltage is None or not str(voltage).strip():
        source = raw.get("ultimatron_source") or {}
        specifications = source.get("technical_specifications") or {}
        # The main technical table uses this spelling; WooCommerce may also
        # expose a rounded system class under "Tension Nominale".
        voltage = specifications.get("Tension nominale")
        if voltage is None:
            specifications = (raw.get("website_import") or {}).get("technical_specifications") or {}
            voltage = specifications.get("Nominale spanning")
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*V\s*", str(voltage or ""), re.IGNORECASE)
    filters = []
    if match:
        value = Decimal(match.group(1).replace(",", "."))
        if value > 0:
            formatted = format(value.normalize(), "f").replace(".", ",")
            filters.append(f"Nominale spanning: {formatted} V")
    return "Lithiumaccu’s", filters
