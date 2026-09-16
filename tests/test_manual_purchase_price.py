import json
import sqlite3

import pytest
from app.suppliers import discounts, hub


@pytest.fixture
def supplier_db(monkeypatch, tmp_path):
    monkeypatch.setattr(hub, 'SUPPLIER_DIR', tmp_path)
    monkeypatch.setattr(hub, 'IMPORT_DIR', tmp_path / 'imports')
    monkeypatch.setattr(hub, 'REGISTRY_PATH', tmp_path / 'registry.sqlite')
    monkeypatch.setattr(hub, 'get_supplier', lambda slug: {'name': 'Test', 'field_mapping': {}, 'request_options': {}})
    with hub._connect(hub.REGISTRY_PATH) as c:
        c.execute('CREATE TABLE suppliers(slug TEXT,last_run_at TEXT,last_run_status TEXT,last_run_message TEXT,updated_at TEXT)')
    path = hub.init_supplier_database('test')
    with hub._connect(path) as c:
        c.execute("""INSERT INTO products(sku,source_title,price,sale_price,cost_price,stock_quantity,raw_data_json,first_seen_at,last_seen_at,updated_at)
                  VALUES('A','Accu',500,600,NULL,8,'{"keep":true}','now','now','now')""")
    return path


def test_manual_cost_protected_from_discounts_and_source_import(supplier_db):
    assert discounts.save_manual_purchase_cost('test', 'A', '249,50') == 249.5
    discounts.save_discount_rule('test', name='20%', match_field='all', match_value='', discount_percent=20, basis_field='price')
    preview = discounts.preview_purchase_costs('test')
    assert preview[0]['Berekende inkoopprijs'] == 249.5
    assert preview[0]['Kortingsregel'] == 'Handmatig vastgelegd'
    assert discounts.apply_purchase_costs('test')['updated'] == 0
    record = {'sku': 'A', 'title': 'Accu uit bron', 'cost_price': 100, 'price': 500}
    analysis = hub.SourceAnalysis('json', 1, list(record), [record], json.dumps([record]).encode(), [record])
    hub.import_records('test', analysis, {'sku': 'sku', 'title': 'title', 'cost_price': 'cost_price', 'price': 'price'})
    with hub._connect(supplier_db) as c:
        p = c.execute('SELECT * FROM products').fetchone()
        assert p['cost_price'] == 249.5
        assert json.loads(p['raw_data_json'])['manual_purchase_price']['cost_price'] == 249.5
    discounts.release_manual_purchase_cost('test', 'A')
    assert discounts.apply_purchase_costs('test')['updated'] == 1
    with hub._connect(supplier_db) as c:
        assert c.execute('SELECT cost_price FROM products').fetchone()[0] == 400


def test_manual_cost_changes_only_price_and_keeps_zero(supplier_db):
    discounts.save_manual_purchase_cost('test', 'A', 0)
    with hub._connect(supplier_db) as c:
        p = c.execute('SELECT * FROM products').fetchone()
        assert (p['cost_price'], p['price'], p['sale_price'], p['stock_quantity'], p['content_locked']) == (0,500,600,8,0)
        assert json.loads(p['raw_data_json'])['keep']
    for value in (None, '', 'NaN', 'Infinity', -1):
        with pytest.raises(ValueError):
            discounts.save_manual_purchase_cost('test', 'A', value)
    with pytest.raises(ValueError):
        discounts.save_manual_purchase_cost('test', 'MISSING', 12)


def test_editor_saves_and_switches_articles(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from app.web import purchase_prices as page
    products = {
        'A': {'cost_price': None, 'sales_unit': 'stuk', 'raw_data': {}},
        'B': {'cost_price': 42, 'sales_unit': 'doos', 'raw_data': {}},
    }
    monkeypatch.setattr(page, 'discount_product_options', lambda slug: {'A': {'source_title': 'Accu'}, 'B': {'source_title': 'Doos'}})
    monkeypatch.setattr(page, 'get_supplier_product', lambda slug, sku: products[sku])
    saved = []
    def save(slug, sku, amount):
        saved.append((slug, sku, amount))
        products[sku]['cost_price'] = amount
        products[sku]['raw_data'] = {'manual_purchase_price': {'cost_price': amount}}
        return amount
    monkeypatch.setattr(page, 'save_manual_purchase_cost', save)
    app = AppTest.from_string("from app.web.purchase_prices import render_manual_purchase_price\nrender_manual_purchase_price('test')").run()
    assert not app.exception
    assert app.number_input[0].value is None
    app.number_input[0].set_value(249.5)
    app.button[0].click().run()
    assert not app.exception
    assert saved == [('test', 'A', 249.5)]
    assert '249,50' in app.success[0].value
    app.selectbox[0].select('B').run()
    assert app.number_input[0].value == 42
    assert 'doos' in app.number_input[0].label


@pytest.mark.parametrize('value,expected', [('222.25€',222.25),('€ 335,13',335.13),('1.234,56 EUR',1234.56),('EUR 1,234.56',1234.56),('op aanvraag',None),('NaN',None)])
def test_source_currency_prices(value, expected):
    assert hub.source_price_value(value) == expected


def test_dealer_price_import_and_targeted_repair(supplier_db, monkeypatch):
    field = 'Dealer Price(Excl.VAT)'
    supplier = {'name':'Test','request_options':{},'field_mapping':{'cost_price':field}}
    monkeypatch.setattr(hub,'get_supplier',lambda slug:supplier)
    monkeypatch.setattr(discounts,'get_supplier',lambda slug:supplier)
    record = {'sku':'A','title':'Accu','Dealer Price(Excl.VAT)':'222.25€'}
    analysis = hub.SourceAnalysis('json',1,list(record),[record],json.dumps([record]).encode(),[record])
    hub.import_records('test',analysis,{'sku':'sku','title':'title'})
    with hub._connect(supplier_db) as c:
        assert c.execute('SELECT cost_price FROM products').fetchone()[0] == 222.25
        c.execute('UPDATE products SET cost_price=NULL,sale_price=600,stock_quantity=8')
    assert discounts.apply_mapped_purchase_costs('test')['updated'] == 1
    with hub._connect(supplier_db) as c:
        assert tuple(c.execute('SELECT cost_price,sale_price,stock_quantity FROM products').fetchone()) == (222.25,600,8)
    discounts.save_manual_purchase_cost('test','A',100)
    assert discounts.apply_mapped_purchase_costs('test')['manual'] == 1
    assert hub.suggest_source_field_mapping([field])['cost_price'] == field


def test_mapped_price_screen_shows_source_without_manual_entry(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from app.web import purchase_prices as page
    monkeypatch.setattr(page,'discount_product_options',lambda slug:{'A':{'source_title':'Accu'}})
    monkeypatch.setattr(page,'get_supplier_product',lambda slug,sku:{'cost_price':222.25,'raw_data':{'Dealer Price(Excl.VAT)':'222.25€'}})
    app=AppTest.from_string("from app.web.purchase_prices import render_manual_purchase_price\nrender_manual_purchase_price('test','Dealer Price(Excl.VAT)')").run()
    assert not app.exception
    assert 'Dealer Price(Excl.VAT)' in app.info[0].value
    assert not app.number_input
    assert app.metric[0].value == '€ 222,25'
