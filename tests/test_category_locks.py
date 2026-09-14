import json
import sqlite3

from app.suppliers.category_locks import CATEGORY_FIELDS, install_category_locks
from app.shopify.sync import _input


def test_import_and_enrichment_cannot_replace_locked_fields_or_empty_levels():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA recursive_triggers=ON')
    conn.execute('CREATE TABLE products (sku TEXT PRIMARY KEY, raw_data_json TEXT, price REAL, '
                 + ','.join(f'{field} TEXT' for field in CATEGORY_FIELDS.values()) + ')')
    conn.execute("INSERT INTO products(sku,raw_data_json,price) VALUES ('VP-A','{}',10),('VP-B','{}',10)")
    values = dict(zip(CATEGORY_FIELDS.values(), ['Werkplaats', 'Handgereedschap', '["Sleutelsets"]', '', '', '']))
    install_category_locks(conn, [{'sku': 'VP-A', 'provenance': {'values': values}}])
    # A source import replaces raw data; an enrichment writer fills empty levels.
    conn.execute("UPDATE products SET product_group_name='Fout',filter_values_json='[]',"
                 "execution='Afgeleid',subcategory_3='Ongewenst',raw_data_json='{\"stock\":5}',price=20")
    saved = dict(conn.execute("SELECT * FROM products WHERE sku='VP-A'").fetchone())
    assert all(saved[field] == value for field, value in values.items())
    assert saved['price'] == 20
    assert json.loads(saved['raw_data_json'])['stock'] == 5
    assert json.loads(saved['raw_data_json'])['category_file_import']['locked'] is True
    assert conn.execute("SELECT product_group_name FROM products WHERE sku='VP-B'").fetchone()[0] == 'Fout'
    # The supplier feed's INSERT ... ON CONFLICT UPDATE path is protected too.
    conn.execute("INSERT INTO products(sku,raw_data_json,product_group_name,subcategory_4) "
                 "VALUES ('VP-A','{}','Nieuw','Afgeleid') ON CONFLICT(sku) DO UPDATE SET "
                 "raw_data_json=excluded.raw_data_json,product_group_name=excluded.product_group_name,"
                 "subcategory_4=excluded.subcategory_4")
    saved = dict(conn.execute("SELECT * FROM products WHERE sku='VP-A'").fetchone())
    assert all(saved[field] == value for field, value in values.items())


def test_normal_shopify_sync_uses_approved_values_over_conflicting_mappings():
    values = dict(zip(CATEGORY_FIELDS.values(), ['Werkplaats', 'Handgereedschap', '["Sleutelsets"]', '', '', '']))
    product = {'sku': 'VP-A', '_supplier_slug': 'valkenpower', 'sale_price': 20, 'images': [],
               'product_group_name': 'Afgeleid', 'subcategory_3': 'Fout',
               '_raw_data': {'category_file_import': {'locked': True, 'values': values}, 'wrong': 'Fout'},
               '_shopify_metafield_mapping': {
                   key: {'owner': 'product', 'namespace': 'custom', 'key': key,
                         'type': 'single_line_text_field', 'source_field': 'wrong'}
                   for key in ['productgroep', 'subcategorie_3', 'ander_veld']}}
    fields = {item['key']: item['value'] for item in _input(product, None)['metafields']}
    assert fields['productgroep'] == 'Werkplaats'
    assert fields['uitvoering'] == 'Handgereedschap'
    assert fields['filter'] == 'Sleutelsets'
    assert 'subcategorie_3' not in fields
    assert fields['ander_veld'] == 'Fout'
