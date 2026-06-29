from sqlalchemy import Column, Integer, String, Text, ForeignKey
from app.core.database import Base


class ProductSpecification(Base):
    __tablename__ = "product_specifications"

    id = Column(Integer, primary_key=True)
    sku = Column(String, ForeignKey("products.sku"), index=True, nullable=False)

    name = Column(String, nullable=False)
    value = Column(Text, nullable=True)
