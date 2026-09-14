from app.shopify.sync import _draft_excluded_collections, _without_excluded_products


def test_all_pages_drafted_and_existing_drafts_preserved():
    class Client:
        def __init__(self): self.writes = []
        def graphql(self, query, variables):
            if 'productUpdate' in query:
                self.writes.append(variables['input']['id'])
                return {'productUpdate': {'product': {'status': 'DRAFT'}, 'userErrors': []}}
            after = variables['after']
            return {'collection': {'products': {
                'nodes': [{'id': 'second', 'status': 'DRAFT'}] if after else [{'id': 'first', 'status': 'ACTIVE'}],
                'pageInfo': {'hasNextPage': not bool(after), 'endCursor': 'page2'},
            }}}
    client = Client()
    supplier = {'request_options': {'continue_selling_collection_rules': [{'collection_id': 'gas', 'exclude': True}]}}
    assert _draft_excluded_collections(client, supplier) == {'first', 'second'}
    assert client.writes == ['first']


def test_excluded_product_siblings_cannot_be_reactivated():
    source = [{'sku': 'A'}, {'sku': 'B'}, {'sku': 'C'}]
    existing = {'A': {'product': {'id': 'gas'}}, 'B': {'product': {'id': 'gas'}}}
    assert _without_excluded_products(source, existing, {'gas'}, {'A'}) == [{'sku': 'C'}]
