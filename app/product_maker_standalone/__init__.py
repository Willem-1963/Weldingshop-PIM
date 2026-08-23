"""Volledig zelfstandige, bewijs-gestuurde Shopify-productmaker voor PIM."""

from .page import show_product_maker
from .service import ProductMakerService

__all__ = ["ProductMakerService", "show_product_maker"]
