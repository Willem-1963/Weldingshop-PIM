from app.shopify.sync import _price_only_variant_rows

def test_certilas_case_cost_rounds_after_six_packs():
    p={
      "sku":"10022","ean":"8720682050347","_supplier_slug":"certilas",
      "sale_price":58.05,"cost_price":17.072,"kg_per_sales_unit":2.2,
      "_raw_data":{"KG Ceweld Unit":"2,2","KG Ceweld Bundle":"13,2"},
      "_certilas_order_unit":{"ean":"8720682050347","minimum_quantity":1,"quantity_step":1,"kg_per_order_unit":13.2,"minimum_order_kg":13.2,"local_sales_policy":True,"physical_pack_kg":2.2,"supplier_minimum_packs":6,"shop_packs_per_unit":6,"sales_mode":"supplier_minimum"}
    }
    rows=_price_only_variant_rows([p],{"10022":{"product":{"id":"p"},"variant":{"id":"v"}}})
    v=rows[0]["variants"][0]
    assert v["inventoryItem"]["cost"]=="102.43"
    assert v["inventoryItem"]["measurement"]["weight"]["value"]==13.2
