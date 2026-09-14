import json
import sqlite3

import pytest

from app.shopify import sync
from app.suppliers import hub


class StockClient:
    def graphql(self, query, variables):
        return {'nodes': [
            {'id': item, 'inventoryLevels': {'nodes': [
                {'location': {'id': 'ws'}, 'quantities': [
                    {'name': 'available', 'quantity': 5 if item == 'in-stock' else 0}
                ]},
                {'location': {'id': 'supplier'}, 'quantities': [
                    {'name': 'available', 'quantity': 99}
                ]},
            ]}} for item in variables['ids']
        ]}


def test_shopify_availability_overrides_feed_and_uses_selected_location():
    products = [
        {'sku': 'A', 'available': False},
        {'sku': 'B', 'available': True, '_keep_active_when_out_of_stock': True},
        {'sku': 'NEW', 'available': True},
    ]
    existing = {
        sku: {'variant': {'inventoryItem': {'id': item}}}
        for sku, item in [('A', 'in-stock'), ('B', 'empty')]
    }
    sync._apply_shopify_stock_availability(StockClient(), products, existing, 'ws')
    assert [p['available'] for p in products] == [True, False, False]
    assert not any(p['_keep_active_when_out_of_stock'] for p in products)


def test_inventory_source_round_trip_preserves_other_settings(tmp_path, monkeypatch):
    path = tmp_path / 'registry.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE suppliers (slug TEXT, available_stock_quantity INTEGER, shopify_location_id TEXT, request_options_json TEXT, updated_at TEXT)')
        db.execute("INSERT INTO suppliers VALUES ('test', 1, 'ws', ?, '')", (json.dumps({'draft_only_when_no_location_stock': True}),))
    monkeypatch.setattr(hub, 'REGISTRY_PATH', path)
    def supplier(slug):
        with sqlite3.connect(path) as db:
            options = json.loads(db.execute('SELECT request_options_json FROM suppliers').fetchone()[0])
        return {'shopify_location_id': 'ws', 'request_options': options}
    monkeypatch.setattr(hub, 'get_supplier', supplier)
    monkeypatch.setattr(hub, 'init_supplier_database', lambda slug: pytest.fail('Shopify mode must not rewrite imported availability'))
    hub.save_inventory_mapping('test', 1, inventory_source='shopify')
    assert supplier('test')['request_options'] == {
        'draft_only_when_no_location_stock': True, 'inventory_source': 'shopify',
    }
    hub.save_inventory_mapping('test', 1, inventory_source='sync', apply_existing=False)
    assert supplier('test')['request_options']['inventory_source'] == 'sync'
    with pytest.raises(ValueError):
        hub.save_inventory_mapping('test', 1, inventory_source='invalid')
