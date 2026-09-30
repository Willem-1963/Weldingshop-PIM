"""Repair incomplete Rhodius HTML from verified catalogue and variant facts."""
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.suppliers.hub import supplier_database_path, utc_now
from app.suppliers.rhodius_catalogue_enrichment import _render_product_html
from app.shopify.sync import _source_products, _description_meets_quality, _effective_description

# Manually checked against RHODIUS 2026/2027 NL, printed pages below.
SERIES = {
    'FT26': (33, 'Deze doorslijpschijf is bedoeld voor de prijsbewuste vakman. '
             'De schijf is ijzer- en zwavelvrij en combineert een lange standtijd '
             'met comfortabel slijpgedrag en gemakkelijk doorslijpen.'),
    'FTK26': (33, 'Deze doorslijpschijf is bedoeld voor de prijsbewuste vakman. '
              'De schijf is ijzer- en zwavelvrij en combineert een lange standtijd '
              'met comfortabel slijpgedrag en gemakkelijk doorslijpen.'),
    'LSZ F3': (114, 'Lamellenslijpschijf met zirkonium en een katoenen rug. '
               'Deze serie biedt een goed verspanend vermogen en een redelijke standtijd.'),
    'LSZ F3 PACK': (114, 'Lamellenslijpschijf met zirkonium en een katoenen rug. '
                    'Deze serie biedt een goed verspanend vermogen en een redelijke standtijd.'),
    'FS': (136, 'Lamellenslijper met een as van 6 mm en aluminiumoxide als korrelsoort. '
           'De grote kunstharskern zorgt voor een lange levensduur. Het flexibele '
           'X-weefsel past zich aan het werkstuk aan en biedt een goede verspaning.'),
    'SPBR': (148, 'Rol schuurlinnen voor handmatig gebruik, met een kunststof kern '
             'om eenvoudig af te rollen. Het flexibele katoenen J-flex-weefsel past '
             'zich aan het werkstuk aan. De rol is dicht bestrooid met aluminiumoxide '
             'en heeft een kunstharsbinding. Scheur de gewenste lengte met de hand af.'),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    args.report.mkdir(parents=True, exist_ok=True)
    database = supplier_database_path('rhodius-abrasives-gmbh')
    products = _source_products('rhodius-abrasives-gmbh')
    changes = []
    for product in products:
        if _description_meets_quality(product, _effective_description(product)):
            continue
        if product.get('content_locked'):
            raise ValueError(f"Locked content needs review: {product['sku']}")
        raw = json.loads(product['raw_data_json'])
        group = dict(raw.get('catalogue_enrichment') or {})
        group.setdefault('title', raw.get('Productnaam') or product['source_title'])
        series = SERIES.get(raw.get('Productnaam'))
        if series and not group.get('description'):
            group['description'] = series[1]
        product['raw_data'] = raw
        new_html = _render_product_html(group, product)
        assert _description_meets_quality(product, new_html), product['sku']
        raw['description_repair'] = {
            'method': 'verified_catalogue_series_and_exact_variant_facts',
            'catalogue_text_page': series[0] if series else None,
            'updated_at': utc_now(),
        }
        changes.append({'sku': product['sku'], 'title': product['source_title'],
                        'before': product['html_description'], 'after': new_html,
                        'raw_before': product['raw_data_json'],
                        'raw_after': json.dumps(raw, ensure_ascii=False),
                        'catalogue_text_page': series[0] if series else ''})
    (args.report / 'changes.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2))
    with (args.report / 'products.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=['sku','title','catalogue_text_page'])
        writer.writeheader()
        writer.writerows({k:r[k] for k in writer.fieldnames} for r in changes)
    if args.apply:
        with sqlite3.connect(database) as conn:
            backup = args.report / 'before.sqlite3'
            if backup.exists():
                raise RuntimeError('Backup already exists; use a fresh report directory')
            with sqlite3.connect(backup) as out:
                conn.backup(out)
            for r in changes:
                result = conn.execute('''UPDATE products SET html_description=?,raw_data_json=?,updated_at=?
                    WHERE sku=? AND html_description IS ? AND raw_data_json=? AND content_locked=0''',
                    (r['after'],r['raw_after'],utc_now(),r['sku'],r['before'],r['raw_before']))
                assert result.rowcount == 1, f"Concurrent change: {r['sku']}"
        remaining = [r['sku'] for r in _source_products('rhodius-abrasives-gmbh')
                     if not _description_meets_quality(r, _effective_description(r))]
        assert not remaining, remaining
    print(json.dumps({'changed':len(changes),'apply':args.apply,'catalogue_series':sum(bool(r['catalogue_text_page']) for r in changes)}))


if __name__ == '__main__':
    main()
