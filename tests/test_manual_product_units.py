import json
import sqlite3

from app.suppliers import hub


def test_manual_units_are_stored_as_durable_pim_overrides(monkeypatch, tmp_path):
    database = tmp_path / 'supplier.sqlite'
    with sqlite3.connect(database) as db:
        db.execute("""CREATE TABLE products(
            sku TEXT PRIMARY KEY,purchase_unit TEXT,sales_unit TEXT,
            purchase_units_per_sales_unit REAL,unit_calculation_mode TEXT,
            raw_data_json TEXT,updated_at TEXT)""")
        db.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?)",
                   ('A','stuk','stuk',1,'multiply','{}','old'))
    monkeypatch.setattr(hub, 'init_supplier_database', lambda slug: database)
    def getter(slug, sku):
        with sqlite3.connect(database) as db:
            db.row_factory = sqlite3.Row
            row = dict(db.execute('SELECT * FROM products WHERE sku=?',(sku,)).fetchone())
            row['raw_data'] = json.loads(row.pop('raw_data_json'))
            return row
    monkeypatch.setattr(hub, 'get_supplier_product', getter)
    saved = hub.save_manual_product_units(
        'supplier','A',purchase_unit='stuk',sales_unit='doos van 10',
        purchase_units_per_sales_unit='10',unit_calculation_mode='multiply')
    assert saved['purchase_units_per_sales_unit'] == 10
    with sqlite3.connect(database) as db:
        raw = json.loads(db.execute("SELECT raw_data_json FROM products WHERE sku='A'").fetchone()[0])
    assert raw['product_maker_overrides']['sales_unit'] == 'doos van 10'
    assert raw['product_maker_overrides']['unit_calculation_mode'] == 'multiply'
