import json
import sys
sys.path.insert(0, "/srv/weldingshop-pim")
from app.shopify.client import ShopifyClient

client = ShopifyClient.from_settings()
result = client.graphql("""mutation($input:ProductSetInput!){
productSet(synchronous:true,input:$input){
product{id variants(first:10){nodes{id sku}}} userErrors{field message code}}}""", {
    "input": {
        "id": "gid://shopify/Product/10349875331401",
        "productOptions": [{
            "name": "Title", "position": 1,
            "values": [{"name": "Default Title"}],
        }],
        "variants": [{
            "id": "gid://shopify/ProductVariant/55074336833865",
            "sku": "4211052",
            "optionValues": [{"optionName": "Title", "name": "Default Title"}],
        }],
    },
})["productSet"]
if result.get("userErrors"):
    raise RuntimeError(result["userErrors"])
print(json.dumps(result, ensure_ascii=False, indent=2))
