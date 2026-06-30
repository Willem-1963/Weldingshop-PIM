from app.core.database import SessionLocal
from app.models.image import ProductImage


class ImageRepository:
    @staticmethod
    def get_by_sku(sku: str):
        session = SessionLocal()
        try:
            return (
                session.query(ProductImage)
                .filter(ProductImage.sku == sku)
                .order_by(ProductImage.position)
                .all()
            )
        finally:
            session.close()

    @staticmethod
    def count_by_sku(sku: str) -> int:
        session = SessionLocal()
        try:
            return session.query(ProductImage).filter(ProductImage.sku == sku).count()
        finally:
            session.close()

    @staticmethod
    def get_primary(sku: str):
        session = SessionLocal()
        try:
            return (
                session.query(ProductImage)
                .filter(ProductImage.sku == sku)
                .order_by(ProductImage.position)
                .first()
            )
        finally:
            session.close()