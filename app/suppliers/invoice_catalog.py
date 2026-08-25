from __future__ import annotations

import json
import os
from typing import Any

from app.suppliers.hub import _connect, init_supplier_database, utc_now


ERP_BASE_URL = os.environ.get("WELDINGSHOP_ERP_BASE_URL", "https://erp.weldingshop.nl").rstrip("/")


def _text(value: Any) -> str:
    return str(value or "").strip()


def invoice_evidence_counts(slug: str, skus: list[str]) -> dict[str, int]:
    """Aantal unieke inkoopfacturen voor de zichtbare productregels."""
    if not skus:
        return {}
    path = init_supplier_database(slug)
    with _connect(path) as conn:
        exists = conn.execute(
            """SELECT 1 FROM sqlite_master WHERE type='table'
               AND name='supplier_invoice_product_evidence'"""
        ).fetchone()
        if not exists:
            return {}
        placeholders = ",".join("?" for _ in skus)
        rows = conn.execute(
            f"""SELECT sku,COUNT(DISTINCT invoice_number) invoice_count
                FROM supplier_invoice_product_evidence
                WHERE sku IN ({placeholders}) COLLATE NOCASE GROUP BY sku""",
            tuple(skus),
        ).fetchall()
    return {str(row["sku"]).casefold(): int(row["invoice_count"]) for row in rows}


def list_product_invoice_evidence(slug: str, sku: str) -> list[dict[str, Any]]:
    """Factuurhistorie voor een PIM-product, nieuwste factuur eerst."""
    path = init_supplier_database(slug)
    with _connect(path) as conn:
        exists = conn.execute(
            """SELECT 1 FROM sqlite_master WHERE type='table'
               AND name='supplier_invoice_product_evidence'"""
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            """SELECT invoice_number,invoice_date,line_number,quantity,
                      net_unit_price,currency,supplier_article_number,
                      evidence_json,recorded_at
               FROM supplier_invoice_product_evidence
               WHERE sku=? COLLATE NOCASE
               ORDER BY invoice_date DESC,recorded_at DESC,line_number DESC""",
            (sku,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            evidence = json.loads(item.pop("evidence_json") or "{}")
        except json.JSONDecodeError:
            evidence = {}
        invoice = evidence.get("invoice") or {}
        intake_id = int(invoice.get("erp_intake_id") or invoice.get("intake_id") or 0)
        item["intake_id"] = intake_id
        item["erp_url"] = (
            f"{ERP_BASE_URL}/purchase-invoice-inbox/{intake_id}"
            if intake_id else ""
        )
        item["pdf_url"] = (
            f"{ERP_BASE_URL}/purchase-invoice-inbox/{intake_id}/original-pdf"
            if intake_id else ""
        )
        result.append(item)
    return result


def persist_linked_invoice_product(
    slug: str, *, invoice: dict[str, Any], shopify: dict[str, Any],
) -> dict[str, Any]:
    """Upsert one exactly linked invoice product into its supplier PIM.

    Invoice evidence is immutable. Shopify is used to bootstrap missing catalog
    fields; richer/manual PIM content is never replaced by empty or older data.
    """
    sku = _text(shopify.get("sku") or invoice.get("shopify_sku"))
    supplier_sku = _text(invoice.get("supplier_article_number"))
    if not slug or not sku or not supplier_sku:
        raise ValueError("Leverancier, Shopify-SKU en leveranciersartikel zijn verplicht")
    if _text(invoice.get("mapping_status")) not in {"mapped", "confirmed"}:
        raise ValueError("Alleen een exact bevestigde factuurkoppeling mag naar de PIM")
    if _text(shopify.get("sku")).casefold() != sku.casefold():
        raise ValueError("Shopify-snapshot hoort niet bij de gekoppelde SKU")

    path = init_supplier_database(slug)
    now = utc_now()
    product = shopify.get("product") or {}
    category = product.get("category") or {}
    images = product.get("images") or []
    raw_snapshot = {
        "source": "linked_purchase_invoice",
        "supplier_slug": slug,
        "latest_invoice": invoice,
        "shopify": shopify,
        "synced_at": now,
    }
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS supplier_invoice_product_evidence(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                supplier_article_number TEXT NOT NULL,
                invoice_number TEXT NOT NULL,
                invoice_date TEXT NOT NULL DEFAULT '',
                line_number INTEGER NOT NULL,
                quantity TEXT NOT NULL DEFAULT '',
                net_unit_price TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'EUR',
                relation_id TEXT NOT NULL DEFAULT '',
                shopify_product_id TEXT NOT NULL,
                shopify_variant_id TEXT NOT NULL,
                shopify_inventory_item_id TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(invoice_number,line_number,sku),
                FOREIGN KEY(sku) REFERENCES products(sku) ON DELETE CASCADE
            );
            """
        )
        existing = conn.execute(
            "SELECT * FROM products WHERE sku=? COLLATE NOCASE", (sku,)
        ).fetchone()
        if existing:
            current = dict(existing)
            try:
                raw = json.loads(current.get("raw_data_json") or "{}")
            except json.JSONDecodeError:
                raw = {}
            raw["invoice_catalog"] = raw_snapshot
            conn.execute(
                """UPDATE products SET
                   supplier_sku=CASE WHEN TRIM(COALESCE(supplier_sku,''))=''
                       THEN ? ELSE supplier_sku END,
                   ean=CASE WHEN TRIM(COALESCE(ean,''))='' THEN ? ELSE ean END,
                   vendor=CASE WHEN TRIM(COALESCE(vendor,''))='' THEN ? ELSE vendor END,
                   brand=CASE WHEN TRIM(COALESCE(brand,''))='' THEN ? ELSE brand END,
                   source_title=CASE WHEN TRIM(COALESCE(source_title,''))=''
                       THEN ? ELSE source_title END,
                   source_description=CASE WHEN TRIM(COALESCE(source_description,''))=''
                       THEN ? ELSE source_description END,
                   cost_price=?,sale_price=?,product_type=CASE
                       WHEN TRIM(COALESCE(product_type,''))='' THEN ? ELSE product_type END,
                   category=CASE WHEN TRIM(COALESCE(category,''))=''
                       THEN ? ELSE category END,
                   category_full=CASE WHEN TRIM(COALESCE(category_full,''))=''
                       THEN ? ELSE category_full END,
                   stock_quantity=?,available=?,shopify_handle=?,shopify_status=?,
                   raw_data_json=?,source_present=1,last_seen_at=?,updated_at=?
                   WHERE sku=? COLLATE NOCASE""",
                (
                    supplier_sku, _text(shopify.get("barcode")),
                    _text(product.get("vendor")), _text(product.get("vendor")),
                    _text(product.get("title")) or sku,
                    _text(product.get("description_html")),
                    float(invoice.get("net_unit_price") or 0),
                    float(shopify.get("price") or 0), _text(product.get("product_type")),
                    _text(category.get("name")), _text(category.get("full_name")),
                    int(shopify.get("inventory_quantity") or 0),
                    int(int(shopify.get("inventory_quantity") or 0) > 0),
                    _text(product.get("handle")), _text(product.get("status") or "draft").lower(),
                    json.dumps(raw, ensure_ascii=False), now, now, sku,
                ),
            )
            created = False
        else:
            conn.execute(
                """INSERT INTO products(
                   sku,supplier_sku,ean,vendor,brand,source_title,source_description,
                   cost_price,sale_price,stock_quantity,available,product_type,
                   category,category_full,source_updated_at,source_present,
                   shopify_handle,shopify_status,raw_data_json,first_seen_at,
                   last_seen_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sku, supplier_sku, _text(shopify.get("barcode")),
                    _text(product.get("vendor")), _text(product.get("vendor")),
                    _text(product.get("title")) or sku,
                    _text(product.get("description_html")),
                    float(invoice.get("net_unit_price") or 0),
                    float(shopify.get("price") or 0),
                    int(shopify.get("inventory_quantity") or 0),
                    int(int(shopify.get("inventory_quantity") or 0) > 0),
                    _text(product.get("product_type")), _text(category.get("name")),
                    _text(category.get("full_name")), now, 1,
                    _text(product.get("handle")),
                    _text(product.get("status") or "draft").lower(),
                    json.dumps({"invoice_catalog": raw_snapshot}, ensure_ascii=False),
                    now, now, now,
                ),
            )
            created = True
        for position, image in enumerate(images, start=1):
            url = _text(image.get("url"))
            if url.startswith("https://"):
                conn.execute(
                    """INSERT INTO product_images(sku,image_url,position,alt_text)
                       VALUES(?,?,?,?) ON CONFLICT(sku,image_url) DO UPDATE SET
                       position=excluded.position,
                       alt_text=CASE WHEN TRIM(COALESCE(product_images.alt_text,''))=''
                           THEN excluded.alt_text ELSE product_images.alt_text END""",
                    (sku, url, position, _text(image.get("alt"))),
                )
        conn.execute(
            """INSERT OR IGNORE INTO supplier_invoice_product_evidence(
               sku,supplier_article_number,invoice_number,invoice_date,line_number,
               quantity,net_unit_price,currency,relation_id,shopify_product_id,
               shopify_variant_id,shopify_inventory_item_id,evidence_json,recorded_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sku, supplier_sku, _text(invoice.get("invoice_number")),
                _text(invoice.get("invoice_date")), int(invoice.get("line_number") or 0),
                _text(invoice.get("quantity")), _text(invoice.get("net_unit_price")),
                _text(invoice.get("currency") or "EUR"), _text(invoice.get("relation_id")),
                _text(product.get("id")), _text(shopify.get("id")),
                _text(shopify.get("inventory_item_id")),
                json.dumps({"invoice": invoice, "shopify": shopify}, ensure_ascii=False), now,
            ),
        )
    return {"slug": slug, "sku": sku, "created": created, "images": len(images)}
