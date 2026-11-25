"""Eenmalige, één jaar geldige Certilas-winkelprijscode na volle webprijs."""
from __future__ import annotations

import hashlib
import json
import re
import smtplib
import sqlite3
import ssl
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from app.shopify.client import ShopifyClient

FUNCTION_ID = "01a149ad-dff9-7a31-9306-ca5d436744bd"
DATABASE_PATH = Path("data/database/certilas_loyalty.sqlite")
SMTP_HOST = "www49.totaalholding.nl"
SMTP_PORT = 465
SMTP_AUTHINFO_PATH = Path("/etc/mail/authinfo")
MAIL_FROM = "Weldingshop.nl <mail@weldingshop.nl>"
CODE_PREFIX = "CERTILAS-"
VALID_DAYS = 365


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS loyalty_orders(
                order_id TEXT PRIMARY KEY,
                order_name TEXT NOT NULL,
                customer_id TEXT,
                webhook_id TEXT,
                status TEXT NOT NULL,
                reason TEXT,
                code TEXT,
                expires_on TEXT,
                email_sent_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS loyalty_orders_code ON loyalty_orders(code) WHERE code IS NOT NULL"
        )


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _gid(kind: str, value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("gid://"):
        return text
    return f"gid://shopify/{kind}/{text}"


def _order_id(payload: dict[str, Any]) -> str:
    return _gid("Order", payload.get("admin_graphql_api_id") or payload.get("id"))


def _customer_id(payload: dict[str, Any]) -> str:
    customer = payload.get("customer") or {}
    raw = customer.get("admin_graphql_api_id") or customer.get("id")
    return _gid("Customer", raw) if raw else ""


def _order_name(payload: dict[str, Any]) -> str:
    return str(payload.get("name") or payload.get("order_number") or "").strip()


def _discount_codes(payload: dict[str, Any]) -> list[str]:
    return [
        str(item.get("code") or "").strip().upper()
        for item in payload.get("discount_codes") or []
        if item.get("code")
    ]


def classify_paid_order(payload: dict[str, Any]) -> str:
    """Return grant, redeem, or a concrete skip reason."""
    if str(payload.get("financial_status") or "").casefold() not in {"paid", "partially_paid"}:
        return "not_paid"
    if str(payload.get("source_name") or "").casefold() != "web":
        return "not_online_store"
    if not _customer_id(payload):
        return "no_customer"
    codes = _discount_codes(payload)
    if any(code.startswith(CODE_PREFIX) for code in codes):
        return "redeem"
    certilas_lines = [
        line for line in payload.get("line_items") or []
        if str(line.get("vendor") or "").strip().casefold() in {"certilas", "ceweld"}
    ]
    if not certilas_lines:
        return "no_certilas"
    if codes or any(_decimal(line.get("total_discount")) > 0 for line in certilas_lines):
        return "not_full_price"
    return "grant"


def personal_code(payload: dict[str, Any]) -> str:
    order_name = re.sub(r"[^0-9A-Z]", "", _order_name(payload).upper()) or "ORDER"
    digest = hashlib.sha256(_order_id(payload).encode("utf-8")).hexdigest()[:6].upper()
    return f"{CODE_PREFIX}{order_name}-{digest}"


def _discount_node(client: ShopifyClient) -> str:
    data = client.graphql(
        """query{discountNodes(first:100){nodes{id discount{__typename ... on DiscountCodeApp{status appDiscountType{functionId}}}}}}"""
    )
    matches = [
        node["id"] for node in data["discountNodes"]["nodes"]
        if node["discount"].get("__typename") == "DiscountCodeApp"
        and node["discount"].get("status") == "ACTIVE"
        and (node["discount"].get("appDiscountType") or {}).get("functionId") == FUNCTION_ID
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Verwacht exact één actieve Certilas-kortingsnode, gevonden: {len(matches)}")
    return matches[0]


def _code_node(client: ShopifyClient, code: str) -> str:
    data = client.graphql(
        """query($code:String!){codeDiscountNodeByCode(code:$code){id}}""",
        {"code": code},
    )
    return str((data.get("codeDiscountNodeByCode") or {}).get("id") or "")


def ensure_code(client: ShopifyClient, code: str) -> str:
    discount_id = _discount_node(client)
    existing = _code_node(client, code)
    if existing:
        if existing != discount_id:
            raise RuntimeError("Persoonlijke code bestaat onder een andere korting.")
        return discount_id
    payload = client.graphql(
        """mutation($discountId:ID!,$codes:[DiscountRedeemCodeInput!]!){discountRedeemCodeBulkAdd(discountId:$discountId,codes:$codes){bulkCreation{id done importedCount failedCount} userErrors{field message code}}}""",
        {"discountId": discount_id, "codes": [{"code": code}]},
    )["discountRedeemCodeBulkAdd"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
    job = payload.get("bulkCreation") or {}
    job_id = job.get("id")
    for _ in range(20):
        if _code_node(client, code) == discount_id:
            return discount_id
        if job_id:
            status = client.graphql(
                """query($id:ID!){discountRedeemCodeBulkCreation(id:$id){done importedCount failedCount}}""",
                {"id": job_id},
            ).get("discountRedeemCodeBulkCreation") or {}
            if status.get("done") and int(status.get("failedCount") or 0) > 0:
                raise RuntimeError(f"Kortingscode-import mislukt: {status}")
        time.sleep(0.25)
    raise RuntimeError("Kortingscode is na bulkimport niet teruggelezen.")


def _has_active_credit(client: ShopifyClient, customer_id: str) -> bool:
    data = client.graphql(
        """query($id:ID!){customer(id:$id){active:metafield(namespace:"custom",key:"certilas_winkelprijs_actief"){value} expires:metafield(namespace:"custom",key:"certilas_winkelprijs_geldig_tot"){value}}}""",
        {"id": customer_id},
    ).get("customer") or {}
    active = str((data.get("active") or {}).get("value") or "").casefold() == "true"
    expires = str((data.get("expires") or {}).get("value") or "")
    return active and (not expires or expires >= date.today().isoformat())


def _set_metafields(client: ShopifyClient, values: list[dict[str, str]]) -> None:
    payload = client.graphql(
        """mutation($metafields:[MetafieldsSetInput!]!){metafieldsSet(metafields:$metafields){metafields{id key value} userErrors{field message code}}}""",
        {"metafields": values},
    )["metafieldsSet"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))


def _grant_customer(client: ShopifyClient, customer_id: str, order_id: str, order_name: str, code: str, expires_on: str) -> None:
    _set_metafields(client, [
        {"ownerId": customer_id, "namespace": "custom", "key": "certilas_winkelprijs_actief", "type": "boolean", "value": "true"},
        {"ownerId": customer_id, "namespace": "custom", "key": "certilas_winkelprijs_code", "type": "single_line_text_field", "value": code},
        {"ownerId": customer_id, "namespace": "custom", "key": "certilas_winkelprijs_geldig_tot", "type": "date", "value": expires_on},
        {"ownerId": customer_id, "namespace": "custom", "key": "certilas_winkelprijs_bron_order", "type": "single_line_text_field", "value": order_name},
        {"ownerId": order_id, "namespace": "custom", "key": "certilas_winkelprijs_code", "type": "single_line_text_field", "value": code},
        {"ownerId": order_id, "namespace": "custom", "key": "certilas_winkelprijs_geldig_tot", "type": "date", "value": expires_on},
    ])
    tags = client.graphql(
        """mutation($id:ID!,$tags:[String!]!){tagsAdd(id:$id,tags:$tags){node{id} userErrors{field message}}}""",
        {"id": order_id, "tags": ["Certilas winkelprijscode uitgegeven"]},
    )["tagsAdd"]
    if tags.get("userErrors"):
        raise RuntimeError(json.dumps(tags["userErrors"], ensure_ascii=False))


def _redeem_customer(client: ShopifyClient, customer_id: str, order_id: str, order_name: str) -> None:
    _set_metafields(client, [
        {"ownerId": customer_id, "namespace": "custom", "key": "certilas_winkelprijs_actief", "type": "boolean", "value": "false"},
        {"ownerId": customer_id, "namespace": "custom", "key": "certilas_winkelprijs_gebruikt_order", "type": "single_line_text_field", "value": order_name},
        {"ownerId": customer_id, "namespace": "custom", "key": "certilas_winkelprijs_gebruikt_op", "type": "date_time", "value": utc_now()},
    ])
    client.graphql(
        """mutation($id:ID!,$tags:[String!]!){tagsAdd(id:$id,tags:$tags){node{id} userErrors{field message}}}""",
        {"id": order_id, "tags": ["Certilas winkelprijscode gebruikt"]},
    )


def _smtp_credentials() -> tuple[str, str]:
    from app.suppliers.scheduler import _smtp_credentials as existing_credentials
    return existing_credentials()

def send_customer_email(payload: dict[str, Any], code: str, expires_on: str) -> None:
    recipient = str(payload.get("contact_email") or payload.get("email") or "").strip()
    if not recipient:
        raise RuntimeError("Bestelling heeft geen e-mailadres voor de winkelprijscode.")
    first_name = str(((payload.get("customer") or {}).get("first_name") or "")).strip()
    salutation = f"Beste {first_name}," if first_name else "Beste klant,"
    body = chr(10).join([
        salutation,
        "",
        "Bedankt voor uw bestelling bij Weldingshop.nl.",
        "",
        "Bij uw eerstvolgende webshopbestelling kunt u Certilas-producten kopen tegen onze winkelprijs.",
        f"Uw persoonlijke kortingscode is: {code}",
        f"De code is één keer geldig tot en met {expires_on}.",
        "",
        "De code geldt alleen voor Certilas-producten, is gekoppeld aan uw klantaccount en kan niet met andere kortingen worden gecombineerd.",
        "Vul de code in tijdens het afrekenen met hetzelfde e-mailadres als bij uw eerdere bestelling.",
        "",
        "Met vriendelijke groet,",
        "Weldingshop.nl",
    ])
    message = EmailMessage()
    message["From"] = MAIL_FROM
    message["To"] = recipient
    message["Subject"] = "Uw persoonlijke Certilas winkelprijscode"
    message.set_content(body)
    username, password = _smtp_credentials()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30, context=ssl.create_default_context()) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)


def _record(order_id: str, **fields: Any) -> None:
    init_database()
    fields["updated_at"] = utc_now()
    with sqlite3.connect(DATABASE_PATH) as conn:
        existing = conn.execute("SELECT order_id FROM loyalty_orders WHERE order_id=?", (order_id,)).fetchone()
        if existing:
            assignments = ",".join(f"{key}=?" for key in fields)
            conn.execute(f"UPDATE loyalty_orders SET {assignments} WHERE order_id=?", (*fields.values(), order_id))
        else:
            fields.setdefault("order_name", order_id)
            fields.setdefault("status", "processing")
            fields.setdefault("created_at", utc_now())
            columns = ["order_id", *fields.keys()]
            conn.execute(
                f"INSERT INTO loyalty_orders({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                (order_id, *fields.values()),
            )


def process_loyalty_webhook(topic: str, webhook_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_database()
    if topic not in {"orders/create", "orders/paid"}:
        return {"status": "ignored", "reason": "topic"}
    order_id = _order_id(payload)
    order_name = _order_name(payload)
    customer_id = _customer_id(payload)
    action = classify_paid_order(payload)
    if action == "redeem":
        _redeem_customer(ShopifyClient.from_settings(), customer_id, order_id, order_name)
        _record(order_id, order_name=order_name, customer_id=customer_id, webhook_id=webhook_id, status="redeemed", reason="personal_code_used")
        return {"status": "redeemed"}
    if action != "grant":
        _record(order_id, order_name=order_name, customer_id=customer_id or None, webhook_id=webhook_id, status="ignored", reason=action)
        return {"status": "ignored", "reason": action}
    with sqlite3.connect(DATABASE_PATH) as conn:
        existing = conn.execute("SELECT * FROM loyalty_orders WHERE order_id=?", (order_id,)).fetchone()
        columns = [item[1] for item in conn.execute("PRAGMA table_info(loyalty_orders)")]
    prior = dict(zip(columns, existing)) if existing else {}
    if prior.get("status") == "completed":
        return {"status": "completed", "code": prior.get("code"), "idempotent": True}
    client = ShopifyClient.from_settings()
    if not prior.get("code") and _has_active_credit(client, customer_id):
        _record(order_id, order_name=order_name, customer_id=customer_id, webhook_id=webhook_id, status="ignored", reason="customer_already_has_active_credit")
        return {"status": "ignored", "reason": "customer_already_has_active_credit"}
    code = str(prior.get("code") or personal_code(payload))
    expires_on = str(prior.get("expires_on") or (date.today() + timedelta(days=VALID_DAYS)).isoformat())
    _record(order_id, order_name=order_name, customer_id=customer_id, webhook_id=webhook_id, status="processing", code=code, expires_on=expires_on)
    ensure_code(client, code)
    _grant_customer(client, customer_id, order_id, order_name, code, expires_on)
    if not prior.get("email_sent_at"):
        send_customer_email(payload, code, expires_on)
    sent_at = prior.get("email_sent_at") or utc_now()
    _record(order_id, status="completed", email_sent_at=sent_at, reason="full_price_certilas_web_order")
    return {"status": "completed", "code": code, "expires_on": expires_on}


def expire_customer_credits() -> dict[str, int]:
    init_database()
    client = ShopifyClient.from_settings()
    today = date.today().isoformat()
    with sqlite3.connect(DATABASE_PATH) as conn:
        rows = conn.execute(
            """SELECT order_id,customer_id FROM loyalty_orders
               WHERE status='completed' AND expires_on<? AND customer_id IS NOT NULL""",
            (today,),
        ).fetchall()
    expired = 0
    for order_id, customer_id in rows:
        _set_metafields(client, [{
            "ownerId": customer_id,
            "namespace": "custom",
            "key": "certilas_winkelprijs_actief",
            "type": "boolean",
            "value": "false",
        }])
        _record(order_id, status="expired", reason="validity_ended")
        expired += 1
    return {"expired": expired}
