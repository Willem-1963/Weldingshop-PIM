import json
import sys
sys.path.insert(0, "/root/weldingshop-pim")
from app.shopify.client import ShopifyClient

client = ShopifyClient.from_settings()
data = client.graphql("""query($query:String!){products(first:20,query:$query){nodes{
id title handle status vendor descriptionHtml variants(first:100){nodes{id sku price}}
}}}""", {"query": "handle:snelkoppeling-gas-38l-ar-model-lt OR handle:snelkoppeling-gas-3-8-l-ar-model-lt-4211058 OR sku:4211058"})
print(json.dumps(data, ensure_ascii=False, indent=2))
