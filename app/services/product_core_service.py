from app.core.database import SessionLocal
from app.models.product import Product
from app.models.image import ProductImage
from app.models.specification import ProductSpecification
from app.product_core.product_record import ProductRecord


class ProductCoreService:
    """
    Central aggregate service for product-related data.

    GUI, AI, Shopify and reports should use this service instead of directly
    combining Product, ProductImage and ProductSpecification queries.
    """

    @staticmethod
    def get_product_record(sku: str) -> ProductRecord | None:
        session = SessionLocal()
        try:
            product = session.query(Product).filter(Product.sku == sku).first()
            if product is None:
                return None

            images = (
                session.query(ProductImage)
                .filter(ProductImage.sku == sku)
                .order_by(ProductImage.position)
                .all()
            )

            specifications = (
                session.query(ProductSpecification)
                .filter(ProductSpecification.sku == sku)
                .order_by(ProductSpecification.name)
                .all()
            )

            return ProductRecord(
                sku=product.sku,
                ean=product.ean,
                supplier=product.supplier,
                brand=product.brand,
                source_title=product.source_title,
                ai_title=product.ai_title,
                source_description=product.source_description,
                html_description=product.html_description,
                price=product.price,
                weight=product.weight,
                product_type=product.product_type,
                category=product.category,
                shopify_status=product.shopify_status,
                inventory_policy=product.inventory_policy,
                ai_generated=bool(product.ai_generated),
                shopify_exported=bool(product.shopify_exported),
                images=list(images),
                specifications=list(specifications),
            )
        finally:
            session.close()

    @staticmethod
    def search_records(query: str, limit: int = 25) -> list[ProductRecord]:
        session = SessionLocal()
        try:
            products = (
                session.query(Product)
                .filter(
                    (Product.sku.ilike(f"%{query}%"))
                    | (Product.source_title.ilike(f"%{query}%"))
                    | (Product.ai_title.ilike(f"%{query}%"))
                    | (Product.ean.ilike(f"%{query}%"))
                )
                .order_by(Product.sku)
                .limit(limit)
                .all()
            )
            skus = [p.sku for p in products]
        finally:
            session.close()

        records = []
        for sku in skus:
            record = ProductCoreService.get_product_record(sku)
            if record:
                records.append(record)
        return records

    @staticmethod
    def get_missing_content_records(limit: int = 25) -> list[ProductRecord]:
        session = SessionLocal()
        try:
            products = (
                session.query(Product)
                .filter(
                    ((Product.source_description.is_(None)) | (Product.source_description == ""))
                    & ((Product.html_description.is_(None)) | (Product.html_description == ""))
                )
                .order_by(Product.sku)
                .limit(limit)
                .all()
            )
            skus = [p.sku for p in products]
        finally:
            session.close()

        return [r for sku in skus if (r := ProductCoreService.get_product_record(sku))]

    @staticmethod
    def get_shopify_ready_records(limit: int = 25) -> list[ProductRecord]:
        session = SessionLocal()
        try:
            products = session.query(Product).order_by(Product.sku).all()
            skus = [p.sku for p in products]
        finally:
            session.close()

        ready = []
        for sku in skus:
            record = ProductCoreService.get_product_record(sku)
            if record and record.is_shopify_ready:
                ready.append(record)
            if len(ready) >= limit:
                break
        return ready
