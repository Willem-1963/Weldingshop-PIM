import pytest

from app.shopify.sync import _stock_protected_missing_items


class Client:
    def __init__(self, locations=None):
        self.locations = locations if locations is not None else [
            {'id': 'vp', 'name': 'Valkenpower'},
            {'id': 'ws', 'name': 'Weldingshop'},
        ]

    def shop_and_locations(self):
        return {'locations': {'nodes': self.locations}}

    def graphql(self, query, variables):
        levels = {
            'own-stock': [('vp', 0), ('ws', 3)],
            'supplier-stock': [('vp', 4), ('ws', 0)],
            'no-stock': [('vp', 0), ('ws', 0)],
            'negative-stock': [('ws', -1)],
        }
        return {'nodes': [
            {'id': item, 'inventoryLevels': {'nodes': [
                {'location': {'id': location},
                 'quantities': [{'name': 'available', 'quantity': quantity}]}
                for location, quantity in levels[item]
            ]}} for item in variables['ids']
        ]}


@pytest.mark.parametrize('all_locations,expected', [
    (False, {'own-stock'}),
    (True, {'own-stock', 'supplier-stock'}),
])
def test_missing_products_protected_by_actual_stock(all_locations, expected):
    assert _stock_protected_missing_items(
        Client(), ['own-stock', 'supplier-stock', 'no-stock', 'negative-stock'],
        protect_all_locations=all_locations,
    ) == expected


@pytest.mark.parametrize('locations', [[], [
    {'id': 'ws1', 'name': 'Weldingshop'},
    {'id': 'ws2', 'name': 'Weldingshop'},
]])
def test_unresolved_own_location_stops_cleanup(locations):
    with pytest.raises(ValueError, match='niet op Concept'):
        _stock_protected_missing_items(
            Client(locations), ['own-stock'], protect_all_locations=False,
        )


def test_empty_cleanup_needs_no_location_lookup():
    assert _stock_protected_missing_items(
        object(), [], protect_all_locations=False,
    ) == set()
