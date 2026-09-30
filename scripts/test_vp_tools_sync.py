"""Run production sync on exactly ten existing VP tools, with before/after evidence."""
import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.shopify import sync
from app.shopify.client import ShopifyClient
from app.suppliers.hub import get_supplier

FIELDS = '''id title handle status descriptionHtml tags category{id name}
metafields(first:250){nodes{namespace key type value} pageInfo{hasNextPage}}
media(first:100){nodes{id status mediaErrors{code details message}
 ... on MediaImage{image{url width height}}}}
variants(first:100){nodes{id sku price barcode
 inventoryItem{id unitCost{amount} inventoryLevels(first:50){nodes{location{id} quantities(names:["available"]){name quantity}}}}
 metafields(first:20,namespace:"custom"){nodes{key value}}
 media(first:1){nodes{id ... on MediaImage{image{url}}}}}}'''


def save(folder, name, value):
    (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))


def snapshot(client, ids):
    products = []
    for pid in ids:
        result = client.graphql('query($id:ID!){product(id:$id){' + FIELDS + '}}', {'id': pid})['product']
        if not result or result['metafields']['pageInfo']['hasNextPage']:
            raise RuntimeError('Incomplete snapshot; refusing the test')
        products.append(result)
    return products


def protected(product):
    return {
        'tags': sorted(product['tags']), 'category': product.get('category'),
        'tree': {m['key']: (m['type'], m['value'])
                 for m in product['metafields']['nodes']
                 if m['namespace'] == 'custom' and m['key'] in sync.COLLECTION_CUSTOM_METAFIELD_KEYS},
        'recommendations': {m['namespace'] + '.' + m['key']: m['value']
                            for m in product['metafields']['nodes']
                            if m['namespace'] == 'shopify--discovery--product_recommendation'},
        'stock': {v['id']: v['inventoryItem']['inventoryLevels'] for v in product['variants']['nodes']},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder
    folder.mkdir(parents=True, exist_ok=True)
    client = ShopifyClient.from_settings()
    if not args.apply:
        collections = client.graphql('''query{collections(first:100,query:"title:*gereedschap*"){
          nodes{id title handle}}}''')['collections']['nodes']
        save(folder, 'collections.json', collections)
        candidates = [c for c in collections if 'gereedschap' in c['title'].casefold()]
        if not candidates:
            raise RuntimeError('No tools collection found')
        collection = sorted(candidates, key=lambda c: (c['handle'] != 'gereedschap-vp', c['title'].casefold() != 'gereedschappen', len(c['title'])))[0]
        all_source = {p['sku'].upper(): p for p in sync._source_products('valkenpower')}
        selected, ids, after = [], [], None
        while len(selected) < 10:
            connection = client.graphql('''query($id:ID!,$after:String){collection(id:$id){
              products(first:100,after:$after){pageInfo{hasNextPage endCursor} nodes{
                id title status variants(first:2){nodes{sku}} media(first:1){nodes{id}}
              }}}}''', {'id': collection['id'], 'after': after})['collection']['products']
            for remote in connection['nodes']:
                variants = remote['variants']['nodes']
                if remote['status'] != 'ACTIVE' or len(variants) != 1:
                    continue
                sku = str(variants[0]['sku'] or '').upper()
                p = copy.deepcopy(all_source.get(sku))
                if not p or not sku.startswith('VP-') or not sync._has_valid_price(p):
                    continue
                p['_raw_data'] = json.loads(p.get('raw_data_json') or '{}')
                p['_has_shopify_image'] = bool(remote['media']['nodes'])
                if sync._common_product_content_errors(p):
                    continue
                selected.append(sku)
                ids.append(remote['id'])
                if len(selected) == 10:
                    break
            if len(selected) == 10 or not connection['pageInfo']['hasNextPage']:
                break
            after = connection['pageInfo']['endCursor']
        if len(selected) != 10 or len(set(ids)) != 10:
            raise RuntimeError(f'Expected ten distinct eligible VP products, found {len(selected)}')
        before = snapshot(client, ids)
        save(folder, 'selection.json', {'collection': collection, 'skus': selected, 'ids': ids})
        save(folder, 'before.json', before)
        print(json.dumps({'collection': collection['title'], 'products': [{'sku': p['variants']['nodes'][0]['sku'], 'title': p['title']} for p in before]}, ensure_ascii=False), flush=True)
        return
    selection = json.loads((folder / 'selection.json').read_text())
    before = json.loads((folder / 'before.json').read_text())
    fresh = snapshot(client, selection['ids'])
    if any(protected(p) != protected(q) for p, q in zip(before, fresh)):
        raise RuntimeError('Protected values changed since preparation; refusing the run')
    wanted = set(selection['skus'])
    source = [p for p in sync._source_products('valkenpower') if p['sku'].upper() in wanted]
    assert len(source) == 10
    supplier = copy.deepcopy(get_supplier('valkenpower'))
    supplier.update(sync_new_product_policy='existing_only', missing_products_to_draft=0, delete_missing_products=0)
    supplier['request_options'].pop('supplier_priority', None)
    assert supplier['request_options'].get('inventory_source') == 'shopify'

    def read_selected(*unused, **kwargs):
        nodes = snapshot(client, selection['ids'])
        return {v['sku'].upper(): {'product': p, 'variant': v}
                for p in nodes for v in p['variants']['nodes']}

    def progress(percent, message):
        print(f'{datetime.now(timezone.utc).isoformat()} {percent}% {message}', flush=True)

    original_supplier = sync.get_supplier
    # Restrict every product lookup/mutation to the selected ten. Global
    # collection policies, missing-product actions and sync baselines are
    # intentionally excluded from this small production-path test.
    with patch.object(sync, '_source_products', lambda slug: copy.deepcopy(source)), \
         patch.object(sync, '_missing_products', lambda slug: []), \
         patch.object(sync, '_shopify_products', read_selected), \
         patch.object(sync, 'get_supplier', lambda slug: supplier if slug == 'valkenpower' else original_supplier(slug)), \
         patch.object(sync, '_apply_collection_inventory_policies', lambda *a: None), \
         patch.object(sync, '_draft_excluded_collections', lambda *a: set()), \
         patch.object(sync, '_collection_inventory_policy_skus', lambda *a: (set(), set(), set())), \
         patch.object(sync, '_save_sync_hashes', lambda *a: None):
        try:
            result = sync.sync_all_products('valkenpower', progress_callback=progress)
            save(folder, 'sync-result.json', result)
        except Exception as exc:
            save(folder, 'error.json', {'error': str(exc)})
            raise
        finally:
            after = snapshot(client, selection['ids'])
            save(folder, 'after.json', after)
            differences = [{'id': p['id'], 'title': p['title']}
                           for p, q in zip(before, after) if protected(p) != protected(q)]
            save(folder, 'protected-verification.json', {'checked': 10, 'differences': differences})
    if differences:
        raise RuntimeError(f'Protected fields changed: {differences}')
    print('SUCCESS: ten products synced; tree, recommendations and stock unchanged', flush=True)


if __name__ == '__main__':
    main()
