import os
from app.ai.providers.base import AIProvider


class OpenAIProvider(AIProvider):
    """
    OpenAI-provider voor Weldingshop PIM.
    Deze versie is nog veilig: hij roept nog geen externe API aan.
    De echte API-koppeling voegen we toe zodra de promptstructuur vaststaat.
    """

    def __init__(self, api_key: str | None = None, model: str = "gpt-4.1-mini"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model

    def generate_title(self, product) -> str:
        title = getattr(product, "source_title", None) or getattr(product, "sku", "")
        brand = getattr(product, "brand", None) or "SP Tools"
        return f"{brand} {title}".strip()

    def generate_description(self, product) -> str:
        title = getattr(product, "source_title", None) or getattr(product, "sku", "")
        description = getattr(product, "source_description", None) or ""
        category = getattr(product, "category", None) or getattr(product, "product_type", None) or ""

        html = [
            f"<h2>{title}</h2>",
            "<p>Professioneel product van SP Tools, geschikt voor gebruik in werkplaats, garage en industrie.</p>",
        ]

        if description:
            html.append("<h3>Productomschrijving</h3>")
            html.append(f"<p>{description}</p>")

        if category:
            html.append("<h3>Categorie</h3>")
            html.append(f"<p>{category}</p>")

        html.append("<h3>Kenmerken</h3>")
        html.append("<ul>")
        html.append("<li>Professionele kwaliteit</li>")
        html.append("<li>Geschikt voor intensief gebruik</li>")
        html.append("<li>Onderdeel van het SP Tools assortiment</li>")
        html.append("</ul>")

        return "\n".join(html)

    def generate_seo(self, product) -> dict:
        title = getattr(product, "source_title", None) or getattr(product, "sku", "")
        sku = getattr(product, "sku", "")
        seo_title = f"SP Tools {title}"[:70]
        meta_description = (
            f"Bestel {title} van SP Tools bij Weldingshop. Professionele kwaliteit, "
            f"artikelnummer {sku}."
        )[:155]
        return {
            "seo_title": seo_title,
            "meta_description": meta_description,
        }
