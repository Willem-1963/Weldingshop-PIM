from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.core.database import SessionLocal
from app.models.product import Product
from app.models.image import ProductImage
from app.models.specification import ProductSpecification


@dataclass
class ProductSnapshot:
    product: Product
    images: list[ProductImage]
    specifications: list[ProductSpecification]


class ProductService:
    """Service layer for product lookup and read-only product views."""

    def get_by_sku(self, sku: str) -> ProductSnapshot | None:
        session = SessionLocal()
        try:
            product = session.query(Product).filter(Product.sku == sku).first()
            if not product:
                return None

            images = (
                session.query(ProductImage)
                .filter(ProductImage.sku == product.sku)
                .order_by(ProductImage.position)
                .all()
            )
            specifications = (
                session.query(ProductSpecification)
                .filter(ProductSpecification.sku == product.sku)
                .order_by(ProductSpecification.name)
                .all()
            )

            session.expunge(product)
            for image in images:
                session.expunge(image)
            for specification in specifications:
                session.expunge(specification)

            return ProductSnapshot(product=product, images=images, specifications=specifications)
        finally:
            session.close()

    def search(self, query: str, limit: int = 20) -> list[Product]:
        session = SessionLocal()
        try:
            like = f"%{query}%"
            products = (
                session.query(Product)
                .filter(
                    (Product.sku.ilike(like))
                    | (Product.ean.ilike(like))
                    | (Product.source_title.ilike(like))
                    | (Product.ai_title.ilike(like))
                )
                .order_by(Product.sku)
                .limit(limit)
                .all()
            )
            for product in products:
                session.expunge(product)
            return products
        finally:
            session.close()
