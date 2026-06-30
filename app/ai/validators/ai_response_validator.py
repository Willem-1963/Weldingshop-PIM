import json
from typing import Any, Dict, Tuple

REQUIRED_FIELDS = [
    "title",
    "short_description",
    "html_description",
    "seo_title",
    "meta_description",
    "keywords",
    "shopify_tags",
]


class AIResponseValidator:
    """Valideert AI-output voordat deze naar de database mag."""

    @staticmethod
    def parse_json(raw_response: str) -> Tuple[bool, Dict[str, Any] | None, str | None]:
        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            return False, None, f"Ongeldige JSON: {exc}"

        return True, data, None

    @staticmethod
    def validate(data: Dict[str, Any]) -> Tuple[bool, list[str]]:
        errors: list[str] = []

        for field in REQUIRED_FIELDS:
            if field not in data:
                errors.append(f"Veld ontbreekt: {field}")

        if "keywords" in data and not isinstance(data["keywords"], list):
            errors.append("keywords moet een lijst zijn")

        if "shopify_tags" in data and not isinstance(data["shopify_tags"], list):
            errors.append("shopify_tags moet een lijst zijn")

        for text_field in ["title", "short_description", "html_description", "seo_title", "meta_description"]:
            if text_field in data and not isinstance(data[text_field], str):
                errors.append(f"{text_field} moet tekst zijn")

        if data.get("html_description") and "<" not in data.get("html_description", ""):
            errors.append("html_description lijkt geen HTML te bevatten")

        return len(errors) == 0, errors

    @staticmethod
    def parse_and_validate(raw_response: str) -> Tuple[bool, Dict[str, Any] | None, list[str]]:
        ok, data, error = AIResponseValidator.parse_json(raw_response)
        if not ok:
            return False, None, [error or "Onbekende JSON-fout"]

        valid, errors = AIResponseValidator.validate(data or {})
        return valid, data, errors
