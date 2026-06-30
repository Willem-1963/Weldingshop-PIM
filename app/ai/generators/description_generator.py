class DescriptionGenerator:
    def __init__(self, provider):
        self.provider = provider

    def generate(self, product) -> str:
        return self.provider.generate_description(product)
