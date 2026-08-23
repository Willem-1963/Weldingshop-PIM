from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.shopify.client import ShopifyClient


DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "manual_product_maker.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS manual_product_drafts(
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           sku TEXT NOT NULL UNIQUE COLLATE NOCASE,
           vendor TEXT NOT NULL,title TEXT NOT NULL,description_html TEXT NOT NULL,
           purchase_price TEXT NOT NULL,sale_price TEXT NOT NULL,
           initial_quantity INTEGER NOT NULL DEFAULT 0,
           purchase_unit TEXT NOT NULL DEFAULT 'stuk',
           sales_unit TEXT NOT NULL DEFAULT 'stuk',
           unit_factor TEXT NOT NULL DEFAULT '1',product_type TEXT NOT NULL DEFAULT '',
           tags_json TEXT NOT NULL DEFAULT '[]',image_urls_json TEXT NOT NULL DEFAULT '[]',
           source_url TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',
           status TEXT NOT NULL DEFAULT 'draft',shopify_product_id TEXT NOT NULL DEFAULT '',
           shopify_variant_id TEXT NOT NULL DEFAULT '',shopify_admin_url TEXT NOT NULL DEFAULT '',
           created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"""
    )
    return connection


def _lines(value: str) -> list[str]:
    return list(dict.fromkeys(line.strip() for line in str(value).splitlines() if line.strip()))


def list_drafts() -> list[dict[str, Any]]:
    with _db() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM manual_product_drafts ORDER BY updated_at DESC"
        )]


def get_draft(draft_id: int) -> dict[str, Any]:
    with _db() as db:
        row = db.execute(
            "SELECT * FROM manual_product_drafts WHERE id=?", (int(draft_id),)
        ).fetchone()
    if not row:
        raise ValueError("PIM-productconcept niet gevonden")
    result = dict(row)
    result["tags"] = json.loads(result.pop("tags_json") or "[]")
    result["image_urls"] = json.loads(result.pop("image_urls_json") or "[]")
    return result


def save_draft(draft_id: int | None = None, **values: Any) -> int:
    sku = str(values.get("sku") or "").strip()
    vendor = str(values.get("vendor") or "").strip()
    title = str(values.get("title") or "").strip()
    if not sku or not vendor or not title:
        raise ValueError("SKU, leverancier/merk en titel zijn verplicht")
    purchase = Decimal(str(values.get("purchase_price") or "0").replace(",", "."))
    sale = Decimal(str(values.get("sale_price") or "0").replace(",", "."))
    factor = Decimal(str(values.get("unit_factor") or "1").replace(",", "."))
    quantity = int(values.get("initial_quantity") or 0)
    if purchase <= 0 or sale <= 0 or factor <= 0 or quantity < 0:
        raise ValueError("Prijzen en factor moeten positief zijn; voorraad mag niet negatief zijn")
    record = (
        sku, vendor, title, str(values.get("description_html") or ""),
        f"{purchase:.2f}", f"{sale:.2f}", quantity,
        str(values.get("purchase_unit") or "stuk").strip(),
        str(values.get("sales_unit") or "stuk").strip(), f"{factor.normalize()}",
        str(values.get("product_type") or "").strip(),
        json.dumps(_lines(values.get("tags") or ""), ensure_ascii=False),
        json.dumps(_lines(values.get("image_urls") or ""), ensure_ascii=False),
        str(values.get("source_url") or "").strip(),
        str(values.get("notes") or "").strip(), _now(),
    )
    with _db() as db:
        if draft_id is None:
            cursor = db.execute(
                """INSERT INTO manual_product_drafts(
                   sku,vendor,title,description_html,purchase_price,sale_price,
                   initial_quantity,purchase_unit,sales_unit,unit_factor,product_type,
                   tags_json,image_urls_json,source_url,notes,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (*record[:-1], record[-1], record[-1]),
            )
            return int(cursor.lastrowid)
        current = db.execute(
            "SELECT status FROM manual_product_drafts WHERE id=?", (int(draft_id),)
        ).fetchone()
        if not current:
            raise ValueError("PIM-productconcept niet gevonden")
        if current["status"] not in {"draft", "shopify_draft"}:
            raise ValueError("Een actief product kan hier niet zonder aparte wijzigingscontrole worden aangepast")
        db.execute(
            """UPDATE manual_product_drafts SET sku=?,vendor=?,title=?,description_html=?,
               purchase_price=?,sale_price=?,initial_quantity=?,purchase_unit=?,sales_unit=?,
               unit_factor=?,product_type=?,tags_json=?,image_urls_json=?,source_url=?,notes=?,
               updated_at=? WHERE id=?""",
            (*record, int(draft_id)),
        )
        return int(draft_id)


def publish_shopify_draft(draft_id: int, location_id: str) -> dict[str, Any]:
    draft = get_draft(draft_id)
    if draft["status"] != "draft":
        raise ValueError("Dit PIM-concept is al naar Shopify gestuurd")
    if not draft["image_urls"]:
        raise ValueError("Minimaal één gecontroleerde HTTPS-foto is verplicht")
    if any(not str(url).startswith("https://") for url in draft["image_urls"]):
        raise ValueError("Alle foto-URL’s moeten met https:// beginnen")
    if not str(location_id).startswith("gid://shopify/Location/"):
        raise ValueError("Kies een geldige Shopify-locatie")
    client = ShopifyClient.from_settings()
    escaped_sku = draft["sku"].replace("\\", "\\\\").replace('"', '\\"')
    existing = client.graphql(
        "query($q:String!){productVariants(first:5,query:$q){nodes{id sku}}}",
        {"q": f'sku:"{escaped_sku}"'},
    )
    exact = [
        item for item in (existing.get("productVariants") or {}).get("nodes") or []
        if str(item.get("sku") or "").strip().upper() == draft["sku"].upper()
    ]
    if exact:
        raise ValueError(f"Shopify bevat SKU {draft['sku']} al")
    handle = re.sub(r"[^a-z0-9]+", "-", f"{draft['vendor']}-{draft['sku']}".lower()).strip("-")
    product_input = {
        "title": draft["title"], "handle": handle,
        "descriptionHtml": draft["description_html"], "vendor": draft["vendor"],
        "productType": draft["product_type"], "tags": draft["tags"], "status": "DRAFT",
        "productOptions": [{"name": "Title", "position": 1,
                            "values": [{"name": "Default Title"}]}],
        "variants": [{
            "optionValues": [{"optionName": "Title", "name": "Default Title"}],
            "price": draft["sale_price"], "sku": draft["sku"], "taxable": True,
            "inventoryPolicy": "DENY",
            "inventoryItem": {"sku": draft["sku"], "cost": draft["purchase_price"],
                              "tracked": True, "requiresShipping": True},
            "inventoryQuantities": [{"locationId": location_id, "name": "available",
                                     "quantity": int(draft["initial_quantity"])}],
        }],
        "files": [{"originalSource": url, "contentType": "IMAGE", "alt": draft["title"]}
                  for url in draft["image_urls"]],
    }
    payload = client.graphql(
        """mutation($input:ProductSetInput!){productSet(synchronous:true,input:$input){
        product{id title handle status variants(first:5){nodes{id sku}}}
        userErrors{code field message}}}""",
        {"input": product_input},
    ).get("productSet") or {}
    if payload.get("userErrors"):
        raise RuntimeError("Shopify weigerde het concept: " + "; ".join(
            str(item.get("message") or item) for item in payload["userErrors"]
        ))
    product = payload.get("product") or {}
    variants = (product.get("variants") or {}).get("nodes") or []
    if not product.get("id") or not variants:
        raise RuntimeError("Shopify gaf geen product en variant terug")
    admin_url = f"https://{client.shop_domain}/admin/products/{str(product['id']).rsplit('/', 1)[-1]}"
    with _db() as db:
        db.execute(
            """UPDATE manual_product_drafts SET status='shopify_draft',
               shopify_product_id=?,shopify_variant_id=?,shopify_admin_url=?,updated_at=?
               WHERE id=? AND status='draft'""",
            (product["id"], variants[0]["id"], admin_url, _now(), int(draft_id)),
        )
    return {"product_id": product["id"], "admin_url": admin_url, "status": "shopify_draft"}
