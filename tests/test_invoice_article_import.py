import sqlite3
from unittest.mock import patch

from app.suppliers.invoice_import import import_invoice_article
from app.suppliers.hub import _is_invoice_managed_product


def test_invoice_managed_product_markers_are_durable():
    assert _is_invoice_managed_product('{"invoice_catalog":{"source":"linked_purchase_invoice"}}')
    assert _is_invoice_managed_product('{"invoice_import":{"source":"purchase_invoice"}}')
    assert _is_invoice_managed_product('{"source":"linked_purchase_invoice"}')
    assert not _is_invoice_managed_product('{"source":"supplier_file"}')
    assert not _is_invoice_managed_product('not-json')


def test_invoice_import_is_supplier_scoped_and_preserves_existing(tmp_path, monkeypatch):
    erp = tmp_path / 'erp.sqlite'
    with sqlite3.connect(erp) as db:
        db.executescript('''CREATE TABLE supplier_invoice_intake(id,relation_id,invoice_number,invoice_date,document_sha256,amount_status);
        CREATE TABLE supplier_invoice_intake_lines(intake_id,line_number,article_number,description,quantity,unit_price);
        CREATE TABLE supplier_invoice_line_pricing(intake_id,line_number,net_unit_price);
        INSERT INTO supplier_invoice_intake VALUES(1,'gas','TV1','2026-09-07','hash','match');
        INSERT INTO supplier_invoice_intake VALUES(2,'other','OTHER','2026-10-30','hash2','match');
        INSERT INTO supplier_invoice_intake_lines VALUES(1,1,'50600','KNIE Easyclip','3','15.60');
        INSERT INTO supplier_invoice_intake_lines VALUES(2,1,'50600','Wrong supplier','3','999');''')
    pim = tmp_path / 'pim.sqlite'
    with sqlite3.connect(pim) as db:
        db.execute('''CREATE TABLE products(sku,supplier_sku,vendor,source_title,cost_price,shopify_status,source_present,raw_data_json,first_seen_at,last_seen_at,updated_at)''')
    def connect(path):
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    monkeypatch.setenv('WELDINGSHOP_ERP_DATABASE_PATH', str(erp))
    supplier = {'slug':'gas','name':'Gasflessen.nl','request_options':{'erp_relation_id':'gas'}}
    with patch('app.suppliers.invoice_import.init_supplier_database', return_value=pim), patch('app.suppliers.invoice_import._connect', side_effect=connect):
        assert import_invoice_article(supplier, '50600')['created']
        with connect(pim) as db:
            row = dict(db.execute('select * from products').fetchone())
            assert row['cost_price'] == 15.60
            assert row['shopify_status'] == 'draft'
            db.execute("update products set cost_price=18,shopify_status='active'")
        assert not import_invoice_article(supplier, '50600')['created']
        with connect(pim) as db:
            row = db.execute('select * from products').fetchone()
            assert row['cost_price'] == 18
            assert row['shopify_status'] == 'active'
        assert import_invoice_article(supplier, 'missing') is None
    assert import_invoice_article({'request_options':{}}, '50600') is None
