"""Build non-destructive updates for existing Shopify articles."""
from decimal import Decimal
from typing import Any


def changed_fields(source: dict[str, Any], match: dict[str, Any], sale_price: float | None,
                   description: str) -> tuple[dict, dict]:
    product = match['product']
    variant = match['variant']
    product_patch = {'id': product['id']}
    variant_patch = {'id': variant['id']}
    raw = source.get('_raw_data') or {}
    invoice_only = bool(raw.get('invoice_import'))
    if not invoice_only:
        title = str(source.get('ai_title') or source.get('source_title') or '').strip()[:255]
        if title and title != product.get('title'):
            product_patch['title'] = title
        if description.strip() and description != (product.get('descriptionHtml') or ''):
            product_patch['descriptionHtml'] = description
        barcode = str(source.get('ean') or '').strip()
        if barcode and barcode != (variant.get('barcode') or ''):
            variant_patch['barcode'] = barcode
        if sale_price is not None and Decimal(str(sale_price)) > 0:
            price = Decimal(str(sale_price)).quantize(Decimal('0.01'))
            if price != Decimal(str(variant.get('price') or 0)):
                variant_patch['price'] = str(price)
    cost = source.get('cost_price')
    if cost is not None and Decimal(str(cost)) > 0:
        cost = Decimal(str(cost)).quantize(Decimal('0.01'))
        current = ((variant.get('inventoryItem') or {}).get('unitCost') or {}).get('amount')
        if current is None or cost != Decimal(str(current)):
            variant_patch['inventoryItem'] = {'cost': str(cost)}
    return product_patch, variant_patch
