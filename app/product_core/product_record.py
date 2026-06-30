from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProductRecord:
    """
    Read-only product aggregate used by GUI, AI, reports and Shopify.

    This class combines the product row with related images and specifications,
    so higher-level modules no longer need to query separate tables directly.
    """

    sku: str
    ean: str | None
    supplier: str | None
    brand: str | None
    source_title: str | None
    ai_title: str | None
    source_description: str | None
    html_description: str | None
    price: float | None
    weight: float | None
    product_type: str | None
    category: str | None
    shopify_status: str | None
    inventory_policy: str | None
    ai_generated: bool
    shopify_exported: bool
    images: list[Any] = field(default_factory=list)
    specifications: list[Any] = field(default_factory=list)

    @property
    def display_title(self) -> str:
        return self.ai_title or self.source_title or self.sku

    @property
    def primary_image(self):
        return self.images[0] if self.images else None

    @property
    def image_count(self) -> int:
        return len(self.images)

    @property
    def specification_count(self) -> int:
        return len(self.specifications)

    @property
    def has_description(self) -> bool:
        return bool(self.html_description or self.source_description)

    @property
    def has_images(self) -> bool:
        return self.image_count > 0

    @property
    def is_shopify_ready(self) -> bool:
        return bool(self.display_title and self.price is not None and self.has_description and self.has_images)

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "ean": self.ean,
            "supplier": self.supplier,
            "brand": self.brand,
            "title": self.display_title,
            "source_title": self.source_title,
            "ai_title": self.ai_title,
            "price": self.price,
            "weight": self.weight,
            "product_type": self.product_type,
            "category": self.category,
            "shopify_status": self.shopify_status,
            "inventory_policy": self.inventory_policy,
            "ai_generated": self.ai_generated,
            "shopify_exported": self.shopify_exported,
            "image_count": self.image_count,
            "specification_count": self.specification_count,
            "shopify_ready": self.is_shopify_ready,
        }
