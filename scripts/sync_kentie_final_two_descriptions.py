import sqlite3
import sys
sys.path.insert(0, "/root/weldingshop-pim")
sys.path.insert(0, "/root/weldingshop-pim/scripts")
from app.shopify.client import ShopifyClient
from kentie_match_batch_sync import graphql_retry, targeted_live

skus = ["1510225", "1512088"]
with sqlite3.connect("/root/weldingshop-pim/data/database/suppliers/kentie.sqlite") as c:
    descriptions = dict(c.execute(
        "SELECT sku,html_description FROM products WHERE sku IN (?,?)", skus
    ))
client = ShopifyClient.from_settings()
live = targeted_live(client, skus)
for sku in skus:
    result = graphql_retry(client, """mutation($input:ProductInput!){
      productUpdate(input:$input){product{id status} userErrors{field message}}}""",
      {"input": {"id": live[sku]["product"]["id"],
                  "descriptionHtml": descriptions[sku]}})["productUpdate"]
    if result.get("userErrors"):
        raise RuntimeError(result["userErrors"])
after = targeted_live(client, skus)
assert all("officieel kentie" not in after[s]["product"]["descriptionHtml"].casefold()
           for s in skus)
print("1510225, 1512088 bijgewerkt en gecontroleerd")
