from app.core.database import SessionLocal
from app.models.product import Product
from app.services.export_filter_service import ExportFilterService, ProductExportFilters


class ProductRepository:
    @staticmethod
    def get_by_sku(sku: str):
        session = SessionLocal()
        try:
            return session.query(Product).filter(Product.sku == sku).first()
        finally:
            session.close()

    @staticmethod
    def search(query: str, limit: int = 25, export_filters: ProductExportFilters | None = None):
        session = SessionLocal()
        try:
            q = f"%{query}%"
            product_query = (
                session.query(Product)
                .filter(
                    (Product.sku.ilike(q)) |
                    (Product.source_title.ilike(q)) |
                    (Product.ai_title.ilike(q)) |
                    (Product.ean.ilike(q))
                )
            )
            product_query = ExportFilterService.apply_to_orm_query(product_query, export_filters)
            return (
                product_query
                .order_by(Product.sku)
                .limit(limit)
                .all()
            )
        finally:
            session.close()

    @staticmethod
    def list_missing_description(limit: int = 25):
        session = SessionLocal()
        try:
            return (
                session.query(Product)
                .filter(
                    (Product.source_description.is_(None)) |
                    (Product.source_description == "")
                )
                .order_by(Product.sku)
                .limit(limit)
                .all()
            )
        finally:
            session.close()
