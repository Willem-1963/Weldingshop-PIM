from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import or_

from app.core.database import SessionLocal
from app.models.product import Product
from app.models.image import ProductImage
from app.models.specification import ProductSpecification


@dataclass
class ProductDetails:
    product: Product
    images: list[ProductImage]
    specifications: list[ProductSpecification]


class ProductService:
    """
    Centrale servicelaag voor productdata.

    Andere modules zoals AI, Shopify, rapportages en straks de GUI gebruiken deze
    service in plaats van rechtstreeks databasequeries te schrijven.
    """

    @staticmethod
    def get_by_sku(sku: str) -> Product | None:
        with SessionLocal() as session:
            return session.query(Product).filter(Product.sku == sku).first()

    @staticmethod
    def get_details_by_sku(sku: str) -> ProductDetails | None:
        with SessionLocal() as session:
            product = session.query(Product).filter(Product.sku == sku).first()
            if product is None:
                return None

            session.expunge(product)

            images = (
                session.query(ProductImage)
                .filter(ProductImage.sku == sku)
                .order_by(ProductImage.position)
                .all()
            )
            for image in images:
                session.expunge(image)

            specifications = (
                session.query(ProductSpecification)
                .filter(ProductSpecification.sku == sku)
                .order_by(ProductSpecification.name)
                .all()
            )
            for specification in specifications:
                session.expunge(specification)

            return ProductDetails(product=product, images=images, specifications=specifications)

    @staticmethod
    def search(query: str, limit: int = 25) -> list[Product]:
        query = query.strip()
        if not query:
            return []

        pattern = f"%{query}%"

        with SessionLocal() as session:
            products = (
                session.query(Product)
                .filter(
                    or_(
                        Product.sku.ilike(pattern),
                        Product.ean.ilike(pattern),
                        Product.source_title.ilike(pattern),
                        Product.ai_title.ilike(pattern),
                        Product.category.ilike(pattern),
                        Product.product_type.ilike(pattern),
                    )
                )
                .order_by(Product.sku)
                .limit(limit)
                .all()
            )
            for product in products:
                session.expunge(product)
            return products

    @staticmethod
    def list_missing_description(limit: int = 100) -> list[Product]:
        with SessionLocal() as session:
            products = (
                session.query(Product)
                .filter(or_(Product.source_description.is_(None), Product.source_description == ""))
                .order_by(Product.sku)
                .limit(limit)
                .all()
            )
            for product in products:
                session.expunge(product)
            return products

    @staticmethod
    def list_missing_images(limit: int = 100) -> list[Product]:
        with SessionLocal() as session:
            subquery = session.query(ProductImage.sku).distinct()
            products = (
                session.query(Product)
                .filter(~Product.sku.in_(subquery))
                .order_by(Product.sku)
                .limit(limit)
                .all()
            )
            for product in products:
                session.expunge(product)
            return products

    @staticmethod
    def count_all() -> int:
        with SessionLocal() as session:
            return session.query(Product).count()

    @staticmethod
    def mark_ai_generated(sku: str, ai_title: str | None = None, html_description: str | None = None) -> bool:
        with SessionLocal() as session:
            product = session.query(Product).filter(Product.sku == sku).first()
            if product is None:
                return False

            if ai_title:
                product.ai_title = ai_title
            if html_description:
                product.html_description = html_description

            product.ai_generated = True
            session.commit()
            return True
