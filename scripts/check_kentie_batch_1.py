import json
import sys

sys.path.insert(0, "/srv/ai-product-factory")
from app.shopify.client import ShopifyClient

skus = [
    "1000046", "1003024", "1050055", "1050061", "114351",
    "114359-KHL", "114376-KZ", "114383-KE", "114392-KE", "114395-KE",
]
query = " OR ".join(f"sku:{sku}" for sku in skus)
data = ShopifyClient.from_settings().graphql(
    """query($query:String!){products(first:20,query:$query){nodes{
    id status descriptionHtml tags media(first:10){nodes{... on MediaImage{image{url}}}}
    variants(first:20){nodes{sku price}}}}}""",
    {"query": query},
)
print(json.dumps(data, ensure_ascii=False))
