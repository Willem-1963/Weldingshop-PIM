from app.core.database import SessionLocal
from app.models.image import ProductImage


class ImageService:
    @staticmethod
    def get_images_by_sku(sku: str):
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
    def count_images_by_sku(sku: str) -> int:
        session = SessionLocal()
        try:
            return (
                session.query(ProductImage)
                .filter(ProductImage.sku == sku)
                .count()
            )
        finally:
            session.close()

    @staticmethod
    def has_images(sku: str) -> bool:
        return ImageService.count_images_by_sku(sku) > 0

    @staticmethod
    def get_primary_image(sku: str):
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