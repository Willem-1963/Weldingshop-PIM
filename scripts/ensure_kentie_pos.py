from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path('/root/weldingshop-pim')
sys.path.insert(0, str(PROJECT))

from app.shopify.client import ShopifyClient

OUTPUT = PROJECT / 'data/output/shopify'
VENDOR_QUERY = 'vendor:Kentie'

PUBLICATIONS_QUERY = '''query { publications(first:100) { nodes { id name } } }'''
PRODUCTS_QUERY = '''query($after:String,$query:String!){
  products(first:250,after:$after,query:$query,sortKey:ID){
    pageInfo{hasNextPage endCursor}
    nodes{
      id title handle status vendor
      variants(first:100){nodes{sku}}
      resourcePublicationsV2(first:100){
        nodes{isPublished publication{id name}}
      }
    }
  }
}'''
PUBLISH_MUTATION = '''mutation($id:ID!,$input:[PublicationInput!]!){
  publishablePublish(id:$id,input:$input){userErrors{field message}}
}'''


def scan(client: ShopifyClient) -> tuple[str, list[dict]]:
    publications = client.graphql(PUBLICATIONS_QUERY, {})['publications']['nodes']
    pos = next((row for row in publications if row['name'] == 'Point of Sale'), None)
    if not pos:
        raise RuntimeError('Point of Sale-publicatie ontbreekt')
    products: list[dict] = []
    after = None
    while True:
        connection = client.graphql(
            PRODUCTS_QUERY, {'after': after, 'query': VENDOR_QUERY}
        )['products']
        products.extend(connection['nodes'])
        if not connection['pageInfo']['hasNextPage']:
            break
        after = connection['pageInfo']['endCursor']
    return pos['id'], products


def on_pos(product: dict, publication_id: str) -> bool:
    return any(
        row.get('isPublished')
        and (row.get('publication') or {}).get('id') == publication_id
        for row in (product.get('resourcePublicationsV2') or {}).get('nodes') or []
    )


def run(apply: bool) -> dict:
    client = ShopifyClient.from_settings()
    publication_id, before = scan(client)
    missing = [
        product for product in before
        if product.get('status') == 'ACTIVE' and not on_pos(product, publication_id)
    ]
    nonactive_on_pos = [
        product for product in before
        if product.get('status') != 'ACTIVE' and on_pos(product, publication_id)
    ]
    if nonactive_on_pos:
        raise RuntimeError(
            f'Veiligheidsstop: {len(nonactive_on_pos)} niet-actieve Kentie-producten staan op POS'
        )
    applied: list[str] = []
    if apply:
        for product in missing:
            result = client.graphql(PUBLISH_MUTATION, {
                'id': product['id'],
                'input': [{'publicationId': publication_id}],
            })['publishablePublish']
            if result.get('userErrors'):
                raise RuntimeError(
                    f"{product['id']}: {json.dumps(result['userErrors'], ensure_ascii=False)}"
                )
            applied.append(product['id'])
    _, after = scan(client)
    remaining = [
        product for product in after
        if product.get('status') == 'ACTIVE' and not on_pos(product, publication_id)
    ]
    result = {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'mode': 'apply' if apply else 'dry-run',
        'vendor': 'Kentie',
        'publication_id': publication_id,
        'total': len(after),
        'active': sum(product.get('status') == 'ACTIVE' for product in after),
        'missing_before': len(missing),
        'published': len(applied),
        'remaining': len(remaining),
        'nonactive_on_pos': len(nonactive_on_pos),
        'missing_skus': [
            variant.get('sku')
            for product in missing
            for variant in (product.get('variants') or {}).get('nodes') or []
        ],
        'status': 'green' if not remaining else 'blocked',
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = OUTPUT / f"kentie-pos-guard-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    result['report'] = str(report)
    if remaining:
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.apply), ensure_ascii=False, indent=2))
