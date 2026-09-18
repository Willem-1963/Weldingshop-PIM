import pytest
from app.shopify import rhodius_test_units as units


class Client:
    def __init__(self, components=None, required=False, errors=None):
        self.current = {'id': 'pack', 'requiresComponents': required,
                        'price': '114.48',
                        'productVariantComponents': {'nodes': components or []}}
        self.errors = errors or []
        self.mutations = []

    def graphql(self, query, variables):
        if query == units.RELATION_QUERY:
            return {'productVariant': self.current}
        self.mutations.append(variables)
        if not self.errors:
            self.current = {'id': 'pack', 'requiresComponents': True,
                            'price': variables['input'][0].get('priceInput', {}).get('price', '114.48'),
                            'productVariantComponents': {'nodes': [
                                {'quantity': 10, 'productVariant': {'id': 'piece'}}]}}
        return {'productVariantRelationshipBulkUpdate': {'userErrors': self.errors}}


def test_native_pack_contains_ten_pieces_and_retry_is_idempotent():
    client = Client()
    result = units.link_package_inventory(client, 'pack', 'piece', 10)
    assert result['requiresComponents']
    assert client.mutations[0]['input'][0]['productVariantRelationshipsToCreate'] == [{'id': 'piece', 'quantity': 10}]
    units.link_package_inventory(client, 'pack', 'piece', 10)
    assert len(client.mutations) == 1


def test_unrelated_components_are_not_replaced():
    client = Client([{'quantity': 10, 'productVariant': {'id': 'other'}}], True)
    with pytest.raises(ValueError, match='andere componenten'):
        units.link_package_inventory(client, 'pack', 'piece', 10)
    assert not client.mutations


def test_bundle_errors_are_not_reported_as_success():
    client = Client(errors=[{'message': 'not supported'}])
    with pytest.raises(RuntimeError, match='not supported'):
        units.link_package_inventory(client, 'pack', 'piece', 10)


def test_existing_bundle_keeps_pim_price_instead_of_rounded_piece_sum():
    client = Client([{'quantity': 10, 'productVariant': {'id': 'piece'}}], True)
    result = units.link_package_inventory(client, 'pack', 'piece', 10, package_price='114.51')
    assert result['price'] == '114.51'
    assert client.mutations[0]['input'][0]['priceInput'] == {'calculation': 'FIXED', 'price': '114.51'}
    units.link_package_inventory(client, 'pack', 'piece', 10, package_price='114.51')
    assert len(client.mutations) == 1


def test_single_piece_does_not_create_duplicate_unit(monkeypatch):
    monkeypatch.setattr(units, 'get_supplier_product', lambda *args: {
        'raw_data_json': '{"VE":1,"GTIN-code":"code","GTIN/verpakking":"code","Prijseenheid":"€/stuk"}'})
    saved = []
    monkeypatch.setattr(units, '_save_unit', lambda *args: saved.append(args))
    calls = []
    def upload(*args, **kwargs):
        calls.append(kwargs)
        return {'id': 'p', 'sku': 'TEST-1', 'unit_rule': {'unit': 'piece'}}
    result = units.upload_test_units('1', upload)
    assert len(calls) == len(saved) == len(result['units']) == 1
    assert result['online_policy'] == 'undecided'
