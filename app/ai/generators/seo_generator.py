class SEOGenerator:
    def __init__(self, provider):
        self.provider = provider

    def generate(self, product) -> dict:
        return self.provider.generate_seo(product)
