from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable

from app.ai.providers.openai_provider import OpenAIProvider
from app.suppliers.hub import (
    DEFAULT_MAPPING,
    REGISTRY_PATH,
    _connect,
    _normalized_field_name,
    utc_now,
)


FIELD_MEANINGS = {
    "sku": "unieke leveranciersartikelcode of SKU",
    "ean": "EAN, GTIN of barcode",
    "title": "Nederlandse korte producttitel",
    "description": "Nederlandse productomschrijving, eventueel HTML",
    "price": "algemene of bruto prijs, niet automatisch inkoopprijs",
    "sale_price": "verkoopprijs voor de klant",
    "cost_price": "netto inkoopprijs of kostprijs",
    "weight": "gewicht in gram",
    "weight_kg": "gewicht in kilogram",
    "primary_image": "URL van de primaire productafbeelding",
    "stock": "voorraad, beschikbaarheid of voorraadindicator",
    "product_type": "producttype of artikelgroep",
    "category": "categorie",
    "category_full": "volledig categoriepad",
    "updated_at": "wijzigingsdatum of tijdstip",
    "product_group_name": "productgroepnaam",
    "execution": "uitvoering of variantkenmerk",
    "filter": "filters, kenmerken of attributen",
    "purchase_unit": "inkoopeenheid of prijseenheid",
    "sales_unit": "verkoopeenheid",
    "purchase_units_per_sales_unit": "aantal inkoopeenheden per verkoopeenheid",
    "unit_calculation_mode": "rekenwijze multiply of divide",
    "gross_purchase_price_per_kg": "bruto inkoopprijs per kilogram",
    "purchase_discount_percent": "inkoopkorting in procenten",
    "net_purchase_price_per_kg": "netto inkoopprijs per kilogram",
    "kg_per_purchase_unit": "kilogram per inkoopeenheid",
    "kg_per_sales_unit": "kilogram per verkoopeenheid of bundel",
}

HIGH_RISK_TARGETS = {
    "sku", "ean", "price", "sale_price", "cost_price", "stock",
    "gross_purchase_price_per_kg", "purchase_discount_percent",
    "net_purchase_price_per_kg",
}


def init_field_learning() -> None:
    with _connect(REGISTRY_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS field_mapping_learning (
                source_field_normalized TEXT NOT NULL,
                target_field TEXT NOT NULL,
                source_field_example TEXT,
                decision TEXT NOT NULL,
                confirmations INTEGER NOT NULL DEFAULT 0,
                rejections INTEGER NOT NULL DEFAULT 0,
                last_supplier_slug TEXT,
                last_confidence REAL,
                last_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(source_field_normalized,target_field)
            );
            """
        )


def learned_source_field_mapping(
    fields: Iterable[Any],
    claimed_sources: set[str] | None = None,
    claimed_targets: set[str] | None = None,
) -> dict[str, str]:
    init_field_learning()
    claimed_sources = claimed_sources or set()
    claimed_targets = claimed_targets or set()
    fields_by_normalized = {
        _normalized_field_name(field): str(field) for field in fields
    }
    suggestions: dict[str, str] = {}
    with _connect(REGISTRY_PATH) as conn:
        rows = conn.execute(
            """
            SELECT * FROM field_mapping_learning
            WHERE decision='confirmed' AND confirmations>rejections
            ORDER BY confirmations DESC,updated_at DESC
            """
        ).fetchall()
    for row in rows:
        source = fields_by_normalized.get(row["source_field_normalized"])
        target = row["target_field"]
        if (
            not source
            or source in claimed_sources
            or target in claimed_targets
            or target in suggestions
        ):
            continue
        suggestions[target] = source
        claimed_sources.add(source)
        claimed_targets.add(target)
    return suggestions


def record_field_mapping_decisions(
    slug: str,
    proposals: list[dict[str, Any]],
    accepted_pairs: set[tuple[str, str]],
) -> None:
    init_field_learning()
    now = utc_now()
    with _connect(REGISTRY_PATH) as conn:
        for proposal in proposals:
            source = str(proposal.get("source_field") or "").strip()
            target = str(proposal.get("target_field") or "").strip()
            if not source or target not in DEFAULT_MAPPING:
                continue
            accepted = (target, source) in accepted_pairs
            conn.execute(
                """
                INSERT INTO field_mapping_learning(
                    source_field_normalized,target_field,source_field_example,
                    decision,confirmations,rejections,last_supplier_slug,
                    last_confidence,last_reason,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_field_normalized,target_field) DO UPDATE SET
                    source_field_example=excluded.source_field_example,
                    decision=excluded.decision,
                    confirmations=confirmations+excluded.confirmations,
                    rejections=rejections+excluded.rejections,
                    last_supplier_slug=excluded.last_supplier_slug,
                    last_confidence=excluded.last_confidence,
                    last_reason=excluded.last_reason,
                    updated_at=excluded.updated_at
                """,
                (
                    _normalized_field_name(source), target, source,
                    "confirmed" if accepted else "rejected",
                    1 if accepted else 0, 0 if accepted else 1, slug,
                    float(proposal.get("confidence") or 0),
                    str(proposal.get("reason") or "")[:1000], now, now,
                ),
            )


def _rejected_pairs() -> set[tuple[str, str]]:
    init_field_learning()
    with _connect(REGISTRY_PATH) as conn:
        rows = conn.execute(
            """
            SELECT source_field_normalized,target_field
            FROM field_mapping_learning
            WHERE decision='rejected' AND rejections>=confirmations
            """
        ).fetchall()
    return {
        (row["source_field_normalized"], row["target_field"])
        for row in rows
    }


def _value_profile(field: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        record.get(field) for record in records[:25]
        if record.get(field) not in (None, "")
    ]
    samples = [str(value).replace("\n", " ")[:160] for value in values[:5]]
    types = Counter(type(value).__name__ for value in values)
    return {
        "field": field,
        "non_empty": len(values),
        "types": dict(types),
        "samples": samples,
    }


def _semantically_compatible(source: str, target: str) -> bool:
    name = _normalized_field_name(source)
    guarded_families = [
        (("title", "naam", "name"), {"title"}),
        (("description", "omschrijving"), {"description"}),
        (("ean", "gtin", "barcode"), {"ean"}),
        (("image", "photo", "foto", "picture"), {"primary_image"}),
        (("stock", "voorraad", "available"), {"stock"}),
        (
            ("price", "prijs", "cost", "korting", "discount"),
            {
                "price", "sale_price", "cost_price",
                "gross_purchase_price_per_kg",
                "purchase_discount_percent",
                "net_purchase_price_per_kg",
            },
        ),
        (
            ("sku", "article", "artikel", "item", "model", "reference"),
            {"sku"},
        ),
        (
            ("category", "categorie", "group", "groep"),
            {"category", "category_full", "product_type", "product_group_name"},
        ),
    ]
    for markers, allowed in guarded_families:
        if any(marker in name for marker in markers):
            return target in allowed
    if any(marker in name for marker in ("weight", "gewicht")):
        # Zonder expliciete eenheid kan AI gram en kilogram niet veilig
        # onderscheiden. Bekende leveranciersaliases worden eerder afgehandeld.
        return (
            target == "weight_kg"
            if ("kg" in name or "kilogram" in name)
            else target == "weight" and ("gram" in name or name.endswith("gr"))
        )
    if any(marker in name for marker in ("height", "width", "length")):
        # Hiervoor bestaan nog geen passende standaarddoelvelden.
        return False
    return True


def ai_field_mapping_proposals(
    slug: str,
    fields: list[str],
    records: list[dict[str, Any]],
    known_mapping: dict[str, str],
) -> list[dict[str, Any]]:
    claimed_sources = set(known_mapping.values())
    claimed_targets = set(known_mapping)
    unknown_fields = [field for field in fields if field not in claimed_sources]
    available_targets = [
        target for target in FIELD_MEANINGS if target not in claimed_targets
    ]
    if not unknown_fields or not available_targets:
        return []

    prompt = {
        "response_format": "Geef uitsluitend één geldig JSON-object terug.",
        "task": (
            "Stel uitsluitend veilige bronveld-naar-PIM-koppelingen voor. "
            "Gebruik veldnaam, voorbeeldwaarden en datatype. Verzin geen "
            "doelvelden. Koppel ieder bronveld en doelveld maximaal één keer."
        ),
        "supplier": slug,
        "allowed_targets": {
            target: FIELD_MEANINGS[target] for target in available_targets
        },
        "high_risk_rule": (
            "Voor SKU, EAN, prijzen, kostprijs en voorraad alleen voorstellen "
            "bij confidence >= 0.90 en ondubbelzinnige betekenis."
        ),
        "other_rule": "Voor andere velden alleen confidence >= 0.75.",
        "semantic_rule": (
            "Taalvarianten zoals titleEN/descriptionDE mogen nooit als "
            "producttype of categorie worden gebruikt. Maten zonder passend "
            "doelveld en gewichten zonder zekere eenheid niet voorstellen."
        ),
        "source_profiles": [
            _value_profile(field, records) for field in unknown_fields
        ],
        "output": {
            "proposals": [{
                "source_field": "exacte bronveldnaam",
                "target_field": "exact toegestaan doelveld",
                "confidence": "getal tussen 0 en 1",
                "reason": "korte Nederlandse uitleg",
            }]
        },
    }
    raw = OpenAIProvider().generate_json(
        json.dumps(prompt, ensure_ascii=False)
    )
    payload = json.loads(raw)
    rejected = _rejected_pairs()
    proposals: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    used_targets: set[str] = set()
    for item in payload.get("proposals") or []:
        source = str(item.get("source_field") or "")
        target = str(item.get("target_field") or "")
        try:
            confidence = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            continue
        threshold = 0.90 if target in HIGH_RISK_TARGETS else 0.75
        if (
            source not in unknown_fields
            or target not in available_targets
            or confidence < threshold
            or not _semantically_compatible(source, target)
            or source in used_sources
            or target in used_targets
            or (_normalized_field_name(source), target) in rejected
        ):
            continue
        used_sources.add(source)
        used_targets.add(target)
        proposals.append({
            "source_field": source,
            "target_field": target,
            "confidence": round(confidence, 2),
            "reason": str(item.get("reason") or "")[:1000],
            "origin": "AI",
        })
    return proposals
