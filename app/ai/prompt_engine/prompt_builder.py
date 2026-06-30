import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List

PROMPT_VERSION = "0.4.0"

SYSTEM_PROMPT = """
Je bent productschrijver voor Weldingshop.nl.
Schrijf professioneel Nederlands voor technische producten.
Gebruik geen overdreven marketingtaal, geen emoji en geen verzonnen specificaties.
Gebruik alleen informatie die in de productdata staat.
Geef altijd geldige JSON terug volgens het opgegeven schema.
""".strip()

OUTPUT_SCHEMA = {
    "title": "Korte Nederlandse producttitel",
    "short_description": "Korte commerciële samenvatting van maximaal 280 tekens",
    "html_description": "HTML beschrijving met h2, p, h3 en ul/li",
    "seo_title": "SEO titel van maximaal 70 tekens",
    "meta_description": "Meta description van maximaal 155 tekens",
    "keywords": ["zoekwoord1", "zoekwoord2"],
    "shopify_tags": ["tag1", "tag2"],
}


class PromptBuilder:
    """Bouwt consistente AI-prompts voor productverrijking."""

    @staticmethod
    def _safe(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def product_to_dict(product_record: Any) -> Dict[str, Any]:
        if is_dataclass(product_record):
            data = asdict(product_record)
        elif isinstance(product_record, dict):
            data = product_record
        else:
            data = product_record.__dict__

        return {
            "sku": PromptBuilder._safe(data.get("sku")),
            "ean": PromptBuilder._safe(data.get("ean")),
            "supplier": PromptBuilder._safe(data.get("supplier")),
            "brand": PromptBuilder._safe(data.get("brand")),
            "source_title": PromptBuilder._safe(data.get("source_title")),
            "ai_title": PromptBuilder._safe(data.get("ai_title")),
            "source_description": PromptBuilder._safe(data.get("source_description")),
            "html_description": PromptBuilder._safe(data.get("html_description")),
            "price": data.get("price"),
            "weight": data.get("weight"),
            "product_type": PromptBuilder._safe(data.get("product_type")),
            "category": PromptBuilder._safe(data.get("category")),
            "image_count": data.get("image_count", 0),
            "specification_count": data.get("specification_count", 0),
        }

    @staticmethod
    def build_product_prompt(product_record: Any) -> str:
        product_data = PromptBuilder.product_to_dict(product_record)

        payload = {
            "prompt_version": PROMPT_VERSION,
            "task": "Genereer verrijkte Nederlandse productcontent voor Shopify.",
            "style_rules": [
                "Schrijf zakelijk, duidelijk en professioneel Nederlands.",
                "Gebruik geen claims die niet uit de brondata blijken.",
                "Gebruik geen woorden als ultiem, revolutionair of ongeëvenaard.",
                "Gebruik HTML met h2, p, h3 en ul/li.",
                "Maak de tekst geschikt voor Weldingshop.nl.",
                "Noem SP Tools alleen als merk/leverancier wanneer dit relevant is.",
            ],
            "product": product_data,
            "required_output_schema": OUTPUT_SCHEMA,
        }

        return SYSTEM_PROMPT + "\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)
