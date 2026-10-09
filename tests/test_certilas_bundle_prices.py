import sqlite3
import pytest
from app.shopify.sync import _selling_price, _certilas_bundle_metafields, _price_only_variant_rows
from app.suppliers.certilas_order_units import load_order_units, verified_order_unit
from scripts.collect_certilas_order_units import parse_order_units

def product(order_kg=3, unit_kg=3, price=100, bundle=18):
    return {'sku':'10001','ean':'8720663400000','_supplier_slug':'certilas',
            'sale_price':price,'cost_price':50,'kg_per_sales_unit':unit_kg,
            '_raw_data':{'KG Ceweld Unit':str(unit_kg),'KG Ceweld Bundle':str(bundle)},
            '_certilas_order_unit':{'ean':'8720663400000','minimum_quantity':1,
                'quantity_step':1,'kg_per_order_unit':order_kg,'minimum_order_kg':order_kg,
                'source_url':'https://portaal.certilas.nl/example','checked_at':'2026-10-09'}}

def test_bundle_weight_is_not_minimum_order():
    p=product()
    assert _selling_price(p)==100
    fields={f['key']:f['value'] for f in _certilas_bundle_metafields(p)}
    assert fields['verplichte_bundel']=='false'
    assert 'verpakkingen_per_bundel' not in fields
    assert fields['minimale_afname']=='Minimale bestelhoeveelheid: 3 kg. De getoonde prijs geldt voor 3 kg. Aantal 1 = 3 kg.'

def test_fractional_package_ratio_uses_actual_order_weight():
    p=product(order_kg=13.2,unit_kg=2,price=31.04,bundle=13.2)
    assert _selling_price(p)==204.86
    fields={f['key']:f['value'] for f in _certilas_bundle_metafields(p)}
    assert '13,2 kg' in fields['minimale_afname']

def test_minimum_can_exceed_single_portal_order_unit():
    p=product(order_kg=6,unit_kg=3)
    p['_certilas_order_unit'].update(minimum_quantity=2,kg_per_order_unit=3)
    assert _selling_price(p)==200

@pytest.mark.parametrize('field,value',[('minimum_order_kg',0),('kg_per_order_unit',float('nan')),('quantity_step',0),('ean','wrong')])
def test_invalid_or_mismatched_portal_data_blocks_publication(field,value):
    p=product();p['_certilas_order_unit'][field]=value
    with pytest.raises(ValueError):_selling_price(p)

def test_missing_verified_minimum_is_never_guessed_from_bundle():
    p=product();p.pop('_certilas_order_unit')
    with pytest.raises(ValueError):_selling_price(p)

def test_other_suppliers_are_unchanged():
    p=product();p['_supplier_slug']='tecweld';p.pop('_certilas_order_unit')
    assert _selling_price(p)==100

def test_price_cost_shipping_weight_share_same_sale_unit():
    p=product(order_kg=13.2,unit_kg=2,price=31.04)
    rows=_price_only_variant_rows([p],{'10001':{'product':{'id':'p'},'variant':{'id':'v'}}})
    v=rows[0]['variants'][0]
    assert v['price']=='204.86'
    assert v['inventoryItem']['cost']=='330.00'
    assert v['inventoryItem']['measurement']['weight']=={'value':13.2,'unit':'KILOGRAMS'}

def test_portal_parser_uses_order_weight_not_kg_per_package_column():
    html='''<table><tr><td>Vacuum 2 8720663400000</td><td><input name="txtCount_10001" min="1" step="1" onchange="changeWeightAmount(this, '13.2000');"></td></tr></table>'''
    row=parse_order_units(html,'https://portaal.certilas.nl/example')[0]
    assert row['minimum_order_kg']==13.2
    assert row['sku']=='10001'

def test_separate_database_table_survives_replacement_of_import_rows():
    c=sqlite3.connect(':memory:')
    assert load_order_units(c)=={}
    c.execute('create table certilas_order_units(sku text, rule_json text)')
    c.execute('insert into certilas_order_units values (?,?)',('10001','{"minimum_order_kg":3}'))
    assert load_order_units(c)['10001']['minimum_order_kg']==3

def test_unknown_minimum_does_not_stop_other_price_updates():
    from app.suppliers.certilas_order_units import checked_order_products
    good=product();bad=product();bad['sku']='unknown';bad.pop('_certilas_order_unit')
    checked,skipped=checked_order_products([good,bad],preserve_families=False)
    assert checked==[good]
    assert skipped==['unknown']

def test_full_product_update_never_omits_an_unverified_sibling_variant():
    from app.suppliers.certilas_order_units import checked_order_products
    good=product();bad=product();bad['sku']='unknown';bad.pop('_certilas_order_unit')
    for p in [good,bad]:p['_family_variant_options']={'family_key':'family'}
    checked,skipped=checked_order_products([good,bad],preserve_families=True)
    assert checked==[]
    assert set(skipped)=={'10001','unknown'}


def test_shop_pack_override_wins_without_changing_supplier_minimum():
    from app.suppliers.certilas_order_units import with_pack_overrides, order_notice
    p=product(order_kg=12,unit_kg=2,price=92.72)
    portal={'10001':p['_certilas_order_unit']}
    overrides={'10001':{'ean':p['ean'],'kg_per_pack':2,'reason':'Verkoop uit winkelvoorraad per pak','updated_at':'2026-10-09'}}
    p['_certilas_order_unit']=with_pack_overrides(portal,overrides)['10001']
    assert _selling_price(p)==92.72
    assert portal['10001']['minimum_order_kg']==12
    assert p['_certilas_order_unit']['portal_minimum_order_kg']==12
    assert 'Aantal 1 = één pak van 2 kg' in order_notice(p)


def test_shop_pack_override_survives_a_new_portal_import():
    import json
    c=sqlite3.connect(':memory:')
    c.execute('create table certilas_order_units(sku text primary key, rule_json text)')
    c.execute('create table certilas_pack_overrides(sku text primary key, rule_json text)')
    c.execute('insert into certilas_pack_overrides values (?,?)',('10001',json.dumps({'ean':'8720663400000','kg_per_pack':2,'reason':'Winkelpak','updated_at':'2026-10-09'})))
    c.execute('insert into certilas_order_units values (?,?)',('10001',json.dumps({'ean':'8720663400000','minimum_order_kg':18})))
    assert load_order_units(c)['10001']['minimum_order_kg']==2
    assert load_order_units(c)['10001']['portal_minimum_order_kg']==18


def test_shop_pack_override_cannot_silently_follow_a_different_article_ean():
    from app.suppliers.certilas_order_units import with_pack_overrides
    with pytest.raises(ValueError,match='afwijkend EAN'):
        with_pack_overrides({'10001':{'ean':'new-ean'}},{'10001':{'ean':'old-ean','kg_per_pack':2,'reason':'Winkelpak','updated_at':'2026-10-09'}})
