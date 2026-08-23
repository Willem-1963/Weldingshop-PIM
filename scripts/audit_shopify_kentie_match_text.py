import json
import sys
sys.path.insert(0, "/srv/ai-product-factory")
from app.shopify.client import ShopifyClient
from app.shopify.sync import _shopify_products

live = _shopify_products(ShopifyClient.from_settings(), "Kentie", "kentie")
products = {}
for match in live.values():
    product = match["product"]
    if "match" in str(product.get("descriptionHtml") or "").casefold():
        products[product["id"]] = {
            "id": product["id"], "title": product["title"],
            "status": product["status"],
            "skus": sorted({
                str(v.get("sku") or "")
                for v in product["variants"]["nodes"] if v.get("sku")
            }),
        }
print(json.dumps({"count": len(products), "products": list(products.values())},
                 ensure_ascii=False, indent=2))
