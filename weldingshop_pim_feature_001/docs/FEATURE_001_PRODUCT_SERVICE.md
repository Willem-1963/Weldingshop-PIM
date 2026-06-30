# Feature 001 - Product Service

## Doel
De Product Service vormt de business layer tussen database en applicatielogica.

Modules die productdata nodig hebben gebruiken voortaan:

```python
from app.services.product_service import ProductService

product = ProductService.get_by_sku("SP81470")
```

## Waarom
Dit voorkomt dubbele databasequeries in:

- AI Engine
- Shopify Engine
- Product Viewer
- Rapportages
- GUI
- Importers

## Beschikbare functies

- `get_by_sku(sku)`
- `get_details_by_sku(sku)`
- `search(query, limit)`
- `list_missing_description(limit)`
- `list_missing_images(limit)`
- `count_all()`
- `mark_ai_generated(sku, ai_title, html_description)`
