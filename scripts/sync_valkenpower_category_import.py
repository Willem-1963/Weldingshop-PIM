"""Lock the approved Excel import and synchronize ONLY its six metafields.

Run prepare, then apply, then verify using the same report directory.
No product, variant, price, inventory or publication mutation is used.
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.shopify.client import ShopifyClient
from app.suppliers.category_locks import CATEGORY_FIELDS, install_category_locks, locked_metafields

IMPORT = ROOT / 'data/import_reports/valkenpower-categories-20260914T092649066833Z'
DB = ROOT / 'data/database/suppliers/valkenpower.sqlite'
SELECTION = ' '.join(f'{key}: metafield(namespace:"custom",key:"{key}") {{value type compareDigest}}' for key in CATEGORY_FIELDS)
QUERY = '''query($after:String){ productVariants(first:50,after:$after,query:"sku:VP-*") {
    pageInfo {hasNextPage endCursor} nodes {id sku price product {id title status
    ''' + SELECTION + '''}}}}'''
READ = 'query($ids:[ID!]!){nodes(ids:$ids){... on Product{id '+SELECTION+'}}}'
SET = '''mutation($metafields:[MetafieldsSetInput!]!){metafieldsSet(metafields:$metafields){
    metafields{owner{... on Product{id}} key value} userErrors{field message code}}}'''
DELETE = '''mutation($metafields:[MetafieldIdentifierInput!]!){metafieldsDelete(metafields:$metafields){
    deletedMetafields{ownerId namespace key} userErrors{field message}}}'''


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def prepare(folder):
    changes = json.loads((IMPORT / 'changes.json').read_text())
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    if not (folder / 'pim-before-lock.sqlite').exists():
        with sqlite3.connect(folder / 'pim-before-lock.sqlite') as backup:
            conn.backup(backup)
    records = []
    for change in changes:
        row = conn.execute('SELECT raw_data_json FROM products WHERE sku=?', (change['sku'],)).fetchone()
        provenance = json.loads(row[0]).get('category_file_import') or {}
        provenance.update(values=change['after'], sheet='Filters')
        provenance.setdefault('source_file', str(IMPORT))
        records.append({'sku': change['sku'], 'provenance': provenance})
    with conn:
        install_category_locks(conn, records)
    desired = {}
    for record in records:
        row = dict(conn.execute('SELECT * FROM products WHERE sku=?', (record['sku'],)).fetchone())
        assert all(row[k] == v for k, v in record['provenance']['values'].items())
        desired[row['sku']] = locked_metafields(row)
    conn.close()
    print(f'Locked and verified {len(desired)} PIM products', flush=True)
    client = ShopifyClient.from_settings()
    defs = client.graphql('''query {metafieldDefinitions(first:250,ownerType:PRODUCT,namespace:"custom"){
        nodes{key type{name}}}}''')['metafieldDefinitions']['nodes']
    defs = {d['key']: d['type']['name'] for d in defs}
    for key in CATEGORY_FIELDS:
        assert defs.get(key) == 'single_line_text_field', (key, defs.get(key))
    dump(folder / 'definitions.json', {k: defs[k] for k in CATEGORY_FIELDS})
    matches = defaultdict(dict)
    after = None
    pages = 0
    while True:
        data = client.graphql(QUERY, {'after': after})['productVariants']
        for variant in data['nodes']:
            if variant['sku'] in desired:
                matches[variant['sku']][variant['product']['id']] = variant
        pages += 1
        if pages % 10 == 0:
            print(f'Shopify lookup: {pages} pages, {len(matches)} SKUs', flush=True)
        if not data['pageInfo']['hasNextPage']:
            break
        after = data['pageInfo']['endCursor']
    targets, issues = {}, []
    for sku, values in desired.items():
        candidates = matches.get(sku, {})
        if len(candidates) != 1:
            issues.append({'sku': sku, 'reason': 'missing' if not candidates else 'duplicate', 'ids': list(candidates)})
            continue
        variant = next(iter(candidates.values()))
        pid = variant['product']['id']
        if pid in targets:
            assert targets[pid]['values'] == values, f'Conflicting categories for variants in {pid}'
            targets[pid]['skus'].append(sku)
        else:
            targets[pid] = {'skus': [sku], 'values': values, 'before': variant['product']}
    dump(folder / 'plan.json', targets)
    dump(folder / 'issues.json', issues)
    print(json.dumps({'products': len(targets), 'issues': len(issues), 'issue_counts': {
        kind: sum(x['reason'] == kind for x in issues) for kind in ['missing', 'duplicate']}}), flush=True)


def apply(folder):
    client = ShopifyClient.from_settings()
    plan = json.loads((folder / 'plan.json').read_text())
    # Read fresh values in batches, then issue only whitelisted metafield mutations.
    ids = list(plan)
    counts = {'set': 0, 'deleted': 0, 'products': 0}
    for offset in range(0, len(ids), 20):
        nodes = client.graphql(READ, {'ids': ids[offset:offset+20]})['nodes']
        sets, deletes = [], []
        assert all(nodes), 'Product disappeared since prepare'
        for node in nodes:
            for key, value in plan[node['id']]['values'].items():
                current = node.get(key)
                identifier = {'ownerId': node['id'], 'namespace': 'custom', 'key': key}
                if value and (not current or current['value'] != value):
                    sets.append({**identifier, 'type': 'single_line_text_field', 'value': value,
                                 'compareDigest': current['compareDigest'] if current else None})
                elif not value and current:
                    deletes.append(identifier)
        for rows, query, name in [(sets, SET, 'metafieldsSet'), (deletes, DELETE, 'metafieldsDelete')]:
            for start in range(0, len(rows), 25):
                batch = rows[start:start+25]
                assert all(r['namespace'] == 'custom' and r['key'] in CATEGORY_FIELDS for r in batch)
                result = client.graphql(query, {'metafields': batch})[name]
                with (folder / 'operations.jsonl').open('a') as log:
                    log.write(json.dumps({'operation': name, 'input': batch, 'result': result}, ensure_ascii=False)+'\n')
                if result['userErrors']:
                    raise RuntimeError(result['userErrors'])
        counts['set'] += len(sets)
        counts['deleted'] += len(deletes)
        counts['products'] += len(nodes)
        dump(folder / 'progress.json', counts)
        if offset % 200 == 0:
            print(json.dumps(counts), flush=True)
    dump(folder / 'applied.json', counts)
    print(json.dumps(counts), flush=True)


def verify(folder):
    client = ShopifyClient.from_settings()
    plan = json.loads((folder / 'plan.json').read_text())
    ids = list(plan)
    differences = []
    for offset in range(0, len(ids), 20):
        nodes = client.graphql(READ, {'ids': ids[offset:offset+20]})['nodes']
        assert all(nodes)
        for node in nodes:
            for key, value in plan[node['id']]['values'].items():
                actual = (node.get(key) or {}).get('value', '')
                if actual != value:
                    differences.append({'id': node['id'], 'key': key, 'expected': value, 'actual': actual})
        if offset % 400 == 0:
            print(f'Verified {offset+len(nodes)}/{len(ids)} products', flush=True)
    # Verify durable PIM values and the normal full-sync payload as well.
    from app.shopify.sync import _input
    from app.suppliers.hub import get_supplier
    supplier = get_supplier('valkenpower')
    pim_verified = 0
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute('SELECT p.*,l.values_json FROM products p '
                                'JOIN category_import_locks l ON l.sku=p.sku'):
            product = dict(row)
            assert all(product[k] == v for k, v in json.loads(row['values_json']).items()), row['sku']
            product.update(_supplier_slug='valkenpower', images=[],
                _raw_data=json.loads(product['raw_data_json']),
                _shopify_metafield_mapping=supplier.get('shopify_metafield_mapping') or {})
            expected = locked_metafields(product)
            actual = {m['key']: m['value'] for m in _input(product, None)['metafields'] if m['namespace'] == 'custom'}
            assert expected is not None and all(actual.get(k, '') == v for k, v in expected.items()), row['sku']
            pim_verified += 1
    dump(folder / 'verification.json', {'products': len(ids), 'fields': len(ids)*6,
        'differences': differences, 'pim_locks_and_normal_sync_verified': pim_verified})
    print(json.dumps({'verified_products': len(ids), 'differences': len(differences)}), flush=True)
    assert not differences


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'apply', 'verify'])
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    args.report.mkdir(parents=True, exist_ok=True)
    globals()[args.action](args.report)
