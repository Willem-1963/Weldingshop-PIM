import json
import sqlite3
import pytest
from app.suppliers import hub


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / 'supplier.sqlite'
    fields = '''sku source_title ai_title source_description ean price sale_price stock_quantity
        available purchase_unit sales_unit purchase_units_per_sales_unit unit_calculation_mode
        gross_purchase_price_per_kg purchase_discount_percent net_purchase_price_per_kg
        kg_per_purchase_unit kg_per_sales_unit cost_price category product_group_name execution
        filter_values_json source_present source_updated_at raw_data_json'''.split()
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE products (' + ','.join(f'{f} TEXT' for f in fields) + ')')
        for sku, raw in [
            ('own', {'GTIN-code': '4011890101810', 'GTIN/verpakking': '4011890101841'}),
            ('neighbour', {'catalogue_enrichment': {'variants': [{'GTIN-code': '4011890101810'}]}}),
            ('website', {'website_import': {'technical_specifications': {'gtin_code_verpakkingseenheid': '4011890116494'}}}),
        ]:
            conn.execute('INSERT INTO products(sku,source_title,ean,raw_data_json) VALUES(?,?,?,?)',
                         (sku, sku, 'other', json.dumps(raw)))
    monkeypatch.setattr(hub, 'init_supplier_database', lambda slug: path)
    return path


@pytest.mark.parametrize('search', [hub.list_products, hub.search_supplier_products])
def test_both_barcodes_find_own_article_without_catalogue_neighbours(database, search):
    for code in ['4011890101810', '4011890101841']:
        assert [r['sku'] for r in search('rhodius-abrasives-gmbh', query=code)] == ['own']
    assert [r['sku'] for r in search('rhodius-abrasives-gmbh', query='4011890116494')] == ['website']
    assert not search('other-supplier', query='4011890101810')
