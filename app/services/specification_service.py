from app.core.database import SessionLocal
from app.models.specification import ProductSpecification


class SpecificationService:
    @staticmethod
    def get_specifications_by_sku(sku: str):
        session = SessionLocal()
        try:
            return (
                session.query(ProductSpecification)
                .filter(ProductSpecification.sku == sku)
                .order_by(ProductSpecification.name)
                .all()
            )
        finally:
            session.close()

    @staticmethod
    def count_specifications_by_sku(sku: str) -> int:
        session = SessionLocal()
        try:
            return (
                session.query(ProductSpecification)
                .filter(ProductSpecification.sku == sku)
                .count()
            )
        finally:
            session.close()

    @staticmethod
    def has_specifications(sku: str) -> bool:
        return SpecificationService.count_specifications_by_sku(sku) > 0
