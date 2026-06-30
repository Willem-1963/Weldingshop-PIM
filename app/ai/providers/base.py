from abc import ABC, abstractmethod


class AIProvider(ABC):
    """
    Basisinterface voor iedere AI-provider.
    Alle AI-providers moeten dezelfde functies aanbieden.
    """

    @abstractmethod
    def generate_title(self, product) -> str:
        """Genereer een nette producttitel."""
        raise NotImplementedError

    @abstractmethod
    def generate_description(self, product) -> str:
        """Genereer een HTML-productbeschrijving."""
        raise NotImplementedError

    @abstractmethod
    def generate_seo(self, product) -> dict:
        """Genereer SEO-title en meta-description."""
        raise NotImplementedError
