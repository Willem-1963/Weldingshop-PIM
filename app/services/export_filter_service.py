from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import exists, or_

from app.models.image import ProductImage
from app.models.product import Product


@dataclass
class ProductExportFilters:
    exclude_without_photo: bool = False
    exclude_missing_price: bool = False
    exclude_zero_price: bool = False
    exclude_missing_ean: bool = False
    exclude_missing_sku: bool = False
    min_price: float | None = None

    def has_filters(self) -> bool:
        return any(
            [
                self.exclude_without_photo,
                self.exclude_missing_price,
                self.exclude_zero_price,
                self.exclude_missing_ean,
                self.exclude_missing_sku,
                self.min_price is not None,
            ]
        )


class ExportFilterService:
    @staticmethod
    def apply_to_orm_query(query, filters: ProductExportFilters | None):
        if filters is None or not filters.has_filters():
            return query

        if filters.exclude_without_photo:
            query = query.filter(exists().where(ProductImage.sku == Product.sku))
        if filters.exclude_missing_price:
            query = query.filter(Product.price.isnot(None))
        if filters.exclude_zero_price:
            query = query.filter(or_(Product.price.is_(None), Product.price != 0))
        if filters.exclude_missing_ean:
            query = query.filter(Product.ean.isnot(None), Product.ean != "")
        if filters.exclude_missing_sku:
            query = query.filter(Product.sku.isnot(None), Product.sku != "")
        if filters.min_price is not None:
            query = query.filter(Product.price.isnot(None), Product.price > filters.min_price)

        return query

    @staticmethod
    def to_sql_where(
        filters: ProductExportFilters | None,
        product_table: str = "products",
        image_table: str = "images",
    ) -> tuple[list[str], list[float]]:
        if filters is None or not filters.has_filters():
            return [], []

        clauses: list[str] = []
        params: list[float] = []

        if filters.exclude_without_photo:
            clauses.append(
                f"EXISTS (SELECT 1 FROM {image_table} WHERE {image_table}.sku = {product_table}.sku)"
            )
        if filters.exclude_missing_price:
            clauses.append(f"{product_table}.price IS NOT NULL")
        if filters.exclude_zero_price:
            clauses.append(f"({product_table}.price IS NULL OR {product_table}.price != 0)")
        if filters.exclude_missing_ean:
            clauses.append(f"{product_table}.ean IS NOT NULL AND TRIM({product_table}.ean) != ''")
        if filters.exclude_missing_sku:
            clauses.append(f"{product_table}.sku IS NOT NULL AND TRIM({product_table}.sku) != ''")
        if filters.min_price is not None:
            clauses.append(f"{product_table}.price IS NOT NULL AND {product_table}.price > ?")
            params.append(filters.min_price)

        return clauses, params
