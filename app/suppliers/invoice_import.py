"""Import an exact supplier invoice article without website research."""
from __future__ import annotations

import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.suppliers.hub import _connect, init_supplier_database, utc_now


def import_invoice_article(supplier: dict[str, Any], article: str) -> dict[str, Any] | None:
    options = supplier.get('request_options') or {}
    if isinstance(options, str):
        options = json.loads(options)
    relation = str(options.get('erp_relation_id') or '').strip()
    if not relation:
        return None
    path = Path(os.environ.get('WELDINGSHOP_ERP_DATABASE_PATH', '/root/weldingshop-erp/data/weldingshop_erp.sqlite3'))
    if not path.is_file():
        raise ValueError('Factuurdatabase is niet beschikbaar.')
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('''SELECT i.id erp_intake_id,i.relation_id,i.invoice_number,
            i.invoice_date,i.document_sha256,l.line_number,l.article_number,
            l.description,l.quantity,COALESCE(NULLIF(p.net_unit_price,''),l.unit_price) net_unit_price
            FROM supplier_invoice_intake i
            JOIN supplier_invoice_intake_lines l ON l.intake_id=i.id
            LEFT JOIN supplier_invoice_line_pricing p ON p.intake_id=l.intake_id AND p.line_number=l.line_number
            WHERE i.relation_id=? AND l.article_number=? COLLATE NOCASE AND i.amount_status='match'
            ORDER BY i.invoice_date DESC,i.id DESC,l.line_number LIMIT 1''', (relation, article)).fetchone()
    if not row:
        return None
    invoice = dict(row)
    if not invoice['net_unit_price'] or Decimal(invoice['net_unit_price']) <= 0:
        raise ValueError('Deze factuurregel heeft geen positieve inkoopprijs; beoordeel het artikel handmatig.')
    now = utc_now()
    with _connect(init_supplier_database(supplier['slug'])) as db:
        existing = db.execute('SELECT * FROM products WHERE supplier_sku=? COLLATE NOCASE OR sku=? COLLATE NOCASE', (article, article)).fetchall()
        if len(existing) > 1:
            raise ValueError('Meerdere PIM-artikelen gevonden voor dit artikelnummer.')
        if existing:
            # An existing PIM record may use another sales unit or have richer content.
            # Opening it must never overwrite its prices, status or content.
            return {'sku': existing[0]['sku'], 'created': False, 'images': 0,
                    'enrichment_skipped': True, 'enrichment_message': 'Bestaand PIM-artikel geopend; geen webonderzoek uitgevoerd.'}
        raw = json.dumps({'invoice_import': {'source': 'purchase_invoice', 'invoice': invoice}}, ensure_ascii=False)
        db.execute('''INSERT INTO products(sku,supplier_sku,vendor,source_title,cost_price,
            shopify_status,source_present,raw_data_json,first_seen_at,last_seen_at,updated_at)
            VALUES(?,?,?,?,?,'draft',1,?,?,?,?)''',
            (article, article, supplier['name'], invoice['description'], float(invoice['net_unit_price']), raw, now, now, now))
    return {'sku': article, 'created': True, 'images': 0, 'enrichment_skipped': True,
            'enrichment_message': f"Toegevoegd vanuit factuur {invoice['invoice_number']} met inkoopprijs € {Decimal(invoice['net_unit_price']):.2f}; geen webonderzoek uitgevoerd."}
