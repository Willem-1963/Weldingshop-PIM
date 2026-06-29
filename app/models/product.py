from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, Boolean
from app.core.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    sku = Column(String, unique=True, index=True, nullable=False)
    ean = Column(String, nullable=True)

    supplier = Column(String, default="SP Tools")
    brand = Column(String, default="SP Tools")

    source_title = Column(String, nullable=True)
    ai_title = Column(String, nullable=True)

    source_description = Column(Text, nullable=True)
    html_description = Column(Text, nullable=True)

    price = Column(Float, nullable=True)
    cost_price = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)

    product_type = Column(String, nullable=True)
    category = Column(String, nullable=True)

    shopify_handle = Column(String, nullable=True)
    shopify_status = Column(String, default="draft")
    inventory_policy = Column(String, default="continue")

    ai_generated = Column(Boolean, default=False)
    shopify_exported = Column(Boolean, default=False)

    content_hash = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
