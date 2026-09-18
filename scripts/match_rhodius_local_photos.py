"""Match supplier photos by both GTINs; audit Shopify fallback by exact SKU.

Default is a read-only plan. --apply changes only the Rhodius PIM image list.
Original photos and existing secondary images are retained. Shopify is read-only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageCms, ImageOps
from app.suppliers.hub import supplier_database_path

SLUG = 'rhodius-abrasives-gmbh'
EXTENSIONS = {'.tif', '.tiff', '.jpg', '.jpeg', '.png', '.webp'}


def gtin(value):
    text = str(value or '').strip()
    if text.endswith('.0'):
        text = text[:-2]
    return text.zfill(14) if re.fullmatch(r'\d{8,14}', text) else ''


def rank(path):
    position = re.search(r'_p(\d+)(?:_|$)', path.stem, re.I)
    n = int(position.group(1)) if position else 99
    packaging = 'verpack' in str(path).lower() or 'verpakking' in str(path).lower()
    return packaging, n != 1, n, str(path)


def convert(source, target):
    if target.exists():
        with Image.open(target) as image:
            image.verify()
        return
    with Image.open(source) as original:
        original.load()
        image = ImageOps.exif_transpose(original)
        profile = image.info.get('icc_profile')
        if profile:
            image = ImageCms.profileToProfile(
                image, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                ImageCms.createProfile('sRGB'), outputMode='RGBA' if 'A' in image.getbands() else 'RGB')
        elif image.mode not in ('RGB', 'RGBA'):
            image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
        image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, 'WEBP', quality=95, method=4)
    with Image.open(target) as check:
        check.verify()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--shopify-cache', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    args.report.mkdir(parents=True, exist_ok=True)
    database = supplier_database_path(SLUG)
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    products = conn.execute('SELECT sku,ean,source_title,raw_data_json FROM products ORDER BY sku').fetchall()
    before = [dict(r) for r in conn.execute('SELECT * FROM product_images ORDER BY sku,position,id')]
    existing = defaultdict(list)
    for r in before:
        existing[r['sku']].append(r)
    files = sorted(p for p in args.source.rglob('*') if p.suffix.lower() in EXTENSIONS and p.is_file())
    by_gtin = defaultdict(list)
    for path in files:
        for code in set(re.findall(r'(?<!\d)\d{8,14}(?!\d)', path.stem)):
            by_gtin[gtin(code)].append(path)
    shopify = json.loads(args.shopify_cache.read_text()) if args.shopify_cache else {}
    counts = Counter(products=len(products), source_images=len(files),
                     missing_before=sum(not existing[r['sku']] for r in products))
    audit = []
    changes = {}
    for product_index, product in enumerate(products, 1):
        sku = product['sku']
        raw = json.loads(product['raw_data_json'])
        codes = [('gtin_piece', raw.get('gtin_piece') or raw.get('GTIN-code')),
                 ('gtin_package', raw.get('gtin_package') or raw.get('GTIN/verpakking'))]
        candidates = []
        for kind, code in codes:
            candidates.extend((kind, str(code), p) for p in sorted(by_gtin.get(gtin(code), []), key=rank))
        item = dict(sku=sku, title=product['source_title'], gtin_piece=codes[0][1],
                    gtin_package=codes[1][1], local_match='', source='', primary='',
                    shopify_status='', shopify_product_id='', shopify_image='',
                    existing_images=len(existing[sku]), errors='')
        if candidates:
            counts['local_gtin_matched'] += 1
        for kind, code, source in candidates:
            stat = source.stat()
            identity = f'{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}'
            digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
            target = ROOT / 'data/product_maker_supplier_assets' / SLUG / 'ean_photos' / (digest + '.webp')
            if args.apply:
                try:
                    convert(source, target)
                except Exception as exc:
                    item['errors'] += f'{source.name}: {type(exc).__name__}: {exc}; '
                    continue
            item.update(local_match=kind, source=str(source), primary=str(target))
            changes[sku] = (str(target), product['source_title'])
            counts['local_primary'] += 1
            counts[kind] += 1
            break
        if not item['local_match']:
            counts['without_local_photo'] += 1
            match = shopify.get(sku.upper())
            if match:
                counts['shopify_exact_sku'] += 1
                p, v = match['product'], match['variant']
                item['shopify_product_id'] = p['id']
                variant_images = [m['image']['url'] for m in (v.get('media') or {}).get('nodes', []) if m.get('image', {}).get('url')]
                product_images = [m['image']['url'] for m in (p.get('media') or {}).get('nodes', []) if (m.get('image') or {}).get('url')]
                if variant_images:
                    url, status = variant_images[0], 'variant_image'
                elif len(p['variants']['nodes']) == 1 and product_images:
                    url, status = product_images[0], 'single_variant_product_image'
                elif product_images:
                    url, status = '', 'product_image_without_variant_assignment'
                else:
                    url, status = '', 'no_shopify_image'
                item.update(shopify_status=status, shopify_image=url or (product_images[0] if product_images else ''))
                counts['shopify_' + status] += 1
                if url and not existing[sku]:
                    changes[sku] = (url, product['source_title'])
                    item['primary'] = url
                    counts['shopify_filled_missing'] += 1
            else:
                item['shopify_status'] = 'sku_not_found' if args.shopify_cache else 'not_checked'
                counts['shopify_' + item['shopify_status']] += 1
        item['has_photo_after'] = bool(item['primary'] or existing[sku])
        if not item['has_photo_after']:
            counts['missing_after'] += 1
        audit.append(item)
        if product_index % 50 == 0:
            print(f'{product_index}/{len(products)} checked; {counts["local_primary"]} local photos', flush=True)
    if args.apply:
        backup = args.report / 'before.sqlite3'
        if backup.exists():
            raise RuntimeError('Use a fresh report directory; backup already exists')
        with sqlite3.connect(backup) as dest:
            conn.backup(dest)
        (args.report / 'images-before.json').write_text(json.dumps(before, indent=2))
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            for sku, (url, alt) in changes.items():
                old = conn.execute('SELECT id,image_url FROM product_images WHERE sku=? ORDER BY position,id', (sku,)).fetchall()
                for position, row in enumerate((r for r in old if r['image_url'] != url), 2):
                    conn.execute('UPDATE product_images SET position=? WHERE id=?', (position, row['id']))
                conn.execute('''INSERT INTO product_images(sku,image_url,position,alt_text) VALUES(?,?,1,?)
                    ON CONFLICT(sku,image_url) DO UPDATE SET position=1,alt_text=excluded.alt_text''', (sku,url,alt))
                conn.execute('UPDATE products SET updated_at=? WHERE sku=?', (now,sku))
        for sku, (url, _) in changes.items():
            first = conn.execute('SELECT image_url FROM product_images WHERE sku=? ORDER BY position,id LIMIT 1', (sku,)).fetchone()
            assert first[0] == url, sku
        actual_missing = conn.execute('SELECT COUNT(*) FROM products p WHERE NOT EXISTS(SELECT 1 FROM product_images i WHERE i.sku=p.sku)').fetchone()[0]
        assert actual_missing == counts['missing_after']
        counts['verified_primary_changes'] = len(changes)
    (args.report / 'summary.json').write_text(json.dumps(dict(counts), indent=2))
    for name, rows in [('all-products.csv',audit), ('without-local-photo.csv',[r for r in audit if not r['local_match']]),
                       ('without-any-photo.csv',[r for r in audit if not r['has_photo_after']])]:
        with (args.report / name).open('w', newline='', encoding='utf-8-sig') as out:
            writer = csv.DictWriter(out, fieldnames=list(audit[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(dict(counts), indent=2), flush=True)


if __name__ == '__main__':
    main()
