import json

from app.product_maker_standalone import shopify


def test_category_suggestion_uses_ai_terms_before_dutch_fuzzy_search(monkeypatch):
    searches = []

    class Client:
        @classmethod
        def from_settings(cls):
            return cls()

        def graphql(self, query, variables):
            term = variables["search"]
            searches.append(term)
            nodes = [{
                "id": "gid://shopify/TaxonomyCategory/bi-25-10",
                "name": "Welding Helmets",
                "fullName": "Business & Industrial > Work Safety Protective Gear > Welding Helmets",
                "isLeaf": True,
            }] if term == "Welding Helmet Accessories" else []
            return {"taxonomy": {"categories": {"nodes": nodes}}}

    class Responses:
        def create(self, **kwargs):
            return type("Response", (), {
                "output_text": json.dumps(["Welding Helmet Accessories"]),
            })()

    class Provider:
        def __init__(self):
            self.client = type("AIClient", (), {
                "with_options": lambda self, **kwargs: type(
                    "ConfiguredClient", (), {"responses": Responses()},
                )(),
            })()

    monkeypatch.setattr(shopify, "ShopifyClient", Client)
    monkeypatch.setattr(shopify, "OpenAIProvider", Provider)

    result = shopify.suggest_categories(
        "Voorste glasplaat | optrel panoramaxx voorste glasplaat | optrel"
    )

    assert result[0]["name"] == "Welding Helmets"
    assert searches == ["Welding Helmet Accessories"]


def test_category_suggestion_falls_back_to_literal_search(monkeypatch):
    class Client:
        @classmethod
        def from_settings(cls):
            return cls()

        def graphql(self, query, variables):
            return {"taxonomy": {"categories": {"nodes": [{
                "id": "gid://shopify/TaxonomyCategory/ha-14-29",
                "name": "Welding Accessories",
            }]}}}

    class Provider:
        def __init__(self):
            raise RuntimeError("AI unavailable")

    monkeypatch.setattr(shopify, "ShopifyClient", Client)
    monkeypatch.setattr(shopify, "OpenAIProvider", Provider)

    assert shopify.suggest_categories("Welding Accessories")[0]["name"] == "Welding Accessories"
