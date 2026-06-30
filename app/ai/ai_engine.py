from app.core.database import SessionLocal
from app.models.product import Product
from app.ai.providers.openai_provider import OpenAIProvider
from app.ai.generators.title_generator import TitleGenerator
from app.ai.generators.description_generator import DescriptionGenerator
from app.ai.generators.seo_generator import SEOGenerator


class AIEngine:
    def __init__(self, provider=None):
        self.provider = provider or OpenAIProvider()
        self.title_generator = TitleGenerator(self.provider)
        self.description_generator = DescriptionGenerator(self.provider)
        self.seo_generator = SEOGenerator(self.provider)

    def enrich_product(self, product: Product) -> Product:
        product.ai_title = self.title_generator.generate(product)
        product.html_description = self.description_generator.generate(product)
        product.ai_generated = True
        return product

    def enrich_batch(self, limit: int = 10) -> int:
        session = SessionLocal()
        products = (
            session.query(Product)
            .filter(Product.ai_generated == False)  # noqa: E712
            .order_by(Product.sku)
            .limit(limit)
            .all()
        )

        count = 0
        for product in products:
            self.enrich_product(product)
            count += 1

        session.commit()
        session.close()
        return count


def main():
    engine = AIEngine()
    count = engine.enrich_batch(limit=10)
    print(f"AI Engine: {count} producten verrijkt.")


if __name__ == "__main__":
    main()
