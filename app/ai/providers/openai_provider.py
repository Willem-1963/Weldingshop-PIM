from __future__ import annotations

import os
from openai import OpenAI

import app.core.config  # laadt config/settings.env
from app.ai.providers.base import AIProvider


class OpenAIProvider(AIProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY ontbreekt in config/settings.env")

        self.client = OpenAI(api_key=self.api_key)

    def generate_json(self, prompt: str) -> str:
        response = self.client.responses.create(
            model=self.model,
            input=prompt,
            text={"format": {"type": "json_object"}},
        )
        return response.output_text

    def generate_title(self, product) -> str:
        title = getattr(product, "source_title", None) or getattr(product, "sku", "")
        brand = getattr(product, "brand", None) or getattr(product, "supplier", None) or ""
        return f"{brand} {title}".strip()

    def generate_description(self, product) -> str:
        title = getattr(product, "source_title", None) or getattr(product, "sku", "")
        return f"<h2>{title}</h2>\n<p>Productinformatie wordt gegenereerd door AI.</p>"

    def generate_seo(self, product) -> dict:
        title = getattr(product, "source_title", None) or getattr(product, "sku", "")
        return {
            "seo_title": title[:70],
            "meta_description": title[:155],
        }
