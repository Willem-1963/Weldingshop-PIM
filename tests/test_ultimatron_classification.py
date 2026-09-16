import json

import pytest
from app.suppliers import hub
from app.suppliers.ultimatron_classification import battery_classification


@pytest.mark.parametrize('source,expected', [('12.8V','12,8'),('25,6 V','25,6'),('38.4v','38,4'),('51.20 V','51,2')])
def test_nominal_voltage_uses_sheet_not_series_or_charging_voltage(source, expected):
    group, filters = battery_classification({'Nominal Voltage':source,'Description':'Series 12V, 24V, 36V, 48V. Charge 14.6V',
        'ultimatron_source': {'technical_specifications': {'Tension nominale':'12,8 V','Tension Nominale':'12V','Tension de charge':'14,6V'}}})
    assert group == 'Lithiumaccu’s'
    assert filters == [f'Nominale spanning: {expected} V']


def test_fallback_is_only_structured_nominal_voltage():
    assert battery_classification({'Description':'12V, 24V and 48V'})[1] == []
    assert battery_classification({'Nominal Voltage':'12V / 24V'})[1] == []
    assert battery_classification({'ultimatron_source':{'technical_specifications':{'Tension nominale':'12,8 V','Tension Nominale':'12V'}}})[1] == ['Nominale spanning: 12,8 V']


def test_refresh_and_import_keep_dutch_group_and_single_nominal_filter(monkeypatch,tmp_path):
    monkeypatch.setattr(hub,'SUPPLIER_DIR',tmp_path)
    monkeypatch.setattr(hub,'IMPORT_DIR',tmp_path/'imports')
    monkeypatch.setattr(hub,'REGISTRY_PATH',tmp_path/'registry.sqlite')
    monkeypatch.setattr(hub,'get_supplier',lambda slug:{'name':'Ultimatron','field_mapping':{},'request_options':{}})
    with hub._connect(hub.REGISTRY_PATH) as c:
        c.execute('CREATE TABLE suppliers(slug TEXT,last_run_at TEXT,last_run_status TEXT,last_run_message TEXT,updated_at TEXT)')
    record={'sku':'TEST','title':'Lithium Battery 12V, 24V, 36V, 48V', 'category':'Batterie au Lithium','Nominal Voltage':'12.8V'}
    analysis=hub.SourceAnalysis('json',1,list(record),[record],json.dumps([record]).encode(),[record])
    hub.import_records('ultimatron',analysis,{'sku':'sku','title':'title','category':'category'})
    path=hub.supplier_database_path('ultimatron')
    with hub._connect(path) as c:
        row=c.execute('SELECT product_group_name,filter_values_json FROM products').fetchone()
        assert row[0] == 'Lithiumaccu’s'
        assert json.loads(row[1]) == ['Nominale spanning: 12,8 V']
        c.execute("UPDATE products SET product_group_name='Batterie au Lithium',filter_values_json='[]'")
    hub.refresh_product_filters('ultimatron')
    with hub._connect(path) as c:
        row=c.execute('SELECT product_group_name,filter_values_json FROM products').fetchone()
        assert row[0] == 'Lithiumaccu’s'
        assert json.loads(row[1]) == ['Nominale spanning: 12,8 V']
