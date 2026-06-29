from sqlalchemy import Column, Integer, String, ForeignKey
from app.core.database import Base


class ProductImage(Base):
    __tablename__ = "product_images"

    id = Column(Integer, primary_key=True)
    sku = Column(String, ForeignKey("products.sku"), index=True, nullable=False)

    url = Column(String, nullable=False)
    position = Column(Integer, default=1)
    alt_text = Column(String, nullable=True)
