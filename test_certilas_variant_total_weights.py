from app.suppliers.certilas_order_units import sales_unit_packaging_label, order_notice


def test_multi_pack_label_preserves_package_and_adds_total():
 rule={'local_sales_policy':True,'shop_packs_per_unit':4,'physical_pack_kg':5}
 value=sales_unit_packaging_label('D200 – kunststof spoel 5 kg',rule)
 assert value=='D200 – kunststof spoel 4 × 5 kg (20 kg totaal)'
 assert sales_unit_packaging_label(value,rule)==value
 assert sales_unit_packaging_label('D200 – kunststof spoel 20 kg',rule)==value


def test_decimal_multi_pack_label_uses_dutch_decimal():
 rule={'local_sales_policy':True,'shop_packs_per_unit':2,'physical_pack_kg':4.2}
 assert sales_unit_packaging_label('D200 – kunststof spoel 4,2 kg',rule)=='D200 – kunststof spoel 2 × 4,2 kg (8,4 kg totaal)'


def test_repeating_pack_weight_is_marked_approximate():
 rule={'local_sales_policy':True,'shop_packs_per_unit':6,'physical_pack_kg':8/6}
 assert sales_unit_packaging_label('Can – 2 kg',rule)=='Can – 6 × ca. 1,33 kg (8 kg totaal)'
 assert sales_unit_packaging_label('Doos · 4 × 2 kg (8 kg totaal)',rule)=='Doos · 6 × ca. 1,33 kg (8 kg totaal)'
 assert sales_unit_packaging_label('Doos · · 6 × ca. 1,33 kg (8 kg totaal)',rule)=='Doos · 6 × ca. 1,33 kg (8 kg totaal)'

def test_repeating_pack_notice_is_marked_approximate():
 rule={'ean':'x','minimum_quantity':1,'quantity_step':1,'kg_per_order_unit':8,'minimum_order_kg':8,'local_sales_policy':True,'shop_packs_per_unit':6,'physical_pack_kg':8/6}
 p={'sku':'A','ean':'x','_supplier_slug':'certilas','_certilas_order_unit':rule}
 assert '6 pakken van ca. 1,33 kg (8 kg totaal)' in order_notice(p)

def test_single_pack_label_stays_physical():
 rule={'local_sales_policy':True,'shop_packs_per_unit':1,'physical_pack_kg':0.8}
 assert sales_unit_packaging_label('D100 – kleine spoel 0,8 kg',rule)=='D100 – kleine spoel 0,8 kg'
