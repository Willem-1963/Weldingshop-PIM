import csv
import json
import sqlite3
import sys
from PIL import Image
from scripts import match_rhodius_local_photos as importer


def test_both_gtins_primary_order_and_existing_images_are_preserved(tmp_path, monkeypatch):
    db = tmp_path / 'supplier.sqlite'
    source = tmp_path / 'photos'
    source.mkdir()
    for suffix in ('p01', 'p31'):
        Image.new('RGB', (20, 20), 'white').save(source / f'photo_4011890112915_{suffix}.tif')
    with sqlite3.connect(db) as conn:
        conn.executescript('''
            CREATE TABLE products(sku TEXT PRIMARY KEY,ean TEXT,source_title TEXT,raw_data_json TEXT,updated_at TEXT);
            CREATE TABLE product_images(id INTEGER PRIMARY KEY,sku TEXT,image_url TEXT,position INTEGER,alt_text TEXT,UNIQUE(sku,image_url));
        ''')
        for sku, raw in [('piece', {'gtin_piece':'4011890112915'}),
                         ('package', {'gtin_piece':'4011890112922','gtin_package':'4011890112915'}),
                         ('missing', {'gtin_piece':'4011890112916'})]:
            conn.execute('INSERT INTO products VALUES(?,?,?,?,NULL)', (sku,'',sku,json.dumps(raw)))
        conn.execute("INSERT INTO product_images VALUES(1,'piece','https://example.com/old.jpg',1,'old')")
    monkeypatch.setattr(importer, 'ROOT', tmp_path)
    monkeypatch.setattr(importer, 'supplier_database_path', lambda _: db)
    report = tmp_path / 'report'
    monkeypatch.setattr(sys, 'argv', ['import', '--source',str(source),'--report',str(report),'--apply'])
    importer.main()
    summary = json.loads((report / 'summary.json').read_text())
    assert summary['gtin_piece'] == 1
    assert summary['gtin_package'] == 1
    assert summary['missing_after'] == 1
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT image_url,position FROM product_images WHERE sku='piece' ORDER BY position").fetchall()
    assert rows[0][1] == 1 and rows[0][0].endswith('.webp')
    assert rows[1] == ('https://example.com/old.jpg',2)
    with (report / 'all-products.csv').open(encoding='utf-8-sig') as f:
        audit = list(csv.DictReader(f))
    assert all(row['source'].endswith('_p01.tif') for row in audit if row['local_match'])
    assert (report / 'before.sqlite3').exists()


def test_gtin_padding_does_not_allow_partial_match():
    assert importer.gtin('4011890112915') == importer.gtin('04011890112915')
    assert importer.gtin('4011890112915.0') == importer.gtin('4011890112915')
    assert importer.gtin('') == ''
    assert importer.gtin('4011890112915') != importer.gtin('4011890112916')
