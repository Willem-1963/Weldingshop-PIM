"""Persoonlijk bedankvoordeel na een nieuwe MIG/MAG-, TIG- of plasmamachine."""
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
from app.suppliers.scheduler import _smtp_credentials

FUNCTION_ID = "01a14a0a-0c20-7e32-a6ec-96369268fef8"
DATABASE_PATH = Path("data/database/return_discount.sqlite")
CODE_PREFIX = "BEDANKT-"
VALID_DAYS = 90
ALLOWED_SOURCES = {"web", "pos"}
MAIL_FROM = "Weldingshop.nl <mail@weldingshop.nl>"
SMTP_HOST = "www49.totaalholding.nl"
SMTP_PORT = 465


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS return_discount_orders(
                order_id TEXT PRIMARY KEY,
                order_name TEXT NOT NULL,
                customer_id TEXT,
                webhook_id TEXT,
                status TEXT NOT NULL,
                reason TEXT,
                code TEXT,
                expires_on TEXT,
                machine_groups TEXT,
                email_sent_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS return_discount_code ON return_discount_orders(code) WHERE code IS NOT NULL")


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
    return [str(item.get("code") or "").strip().upper() for item in payload.get("discount_codes") or [] if item.get("code")]


def _recipient_email(payload: dict[str, Any]) -> str:
    customer = payload.get("customer") or {}
    return str(payload.get("contact_email") or payload.get("email") or customer.get("email") or "").strip()


def basic_action(payload: dict[str, Any]) -> str:
    if str(payload.get("financial_status") or "").casefold() != "paid":
        return "not_paid"
    if not _customer_id(payload):
        return "no_customer"
    if any(code.startswith(CODE_PREFIX) for code in _discount_codes(payload)):
        return "redeem"
    if str(payload.get("source_name") or "").casefold() not in ALLOWED_SOURCES:
        return "unsupported_source"
    if not _recipient_email(payload):
        return "no_email"
    return "check_machine"


def qualifying_machine_groups(client: ShopifyClient, payload: dict[str, Any]) -> list[str]:
    product_ids = []
    for line in payload.get("line_items") or []:
        raw = line.get("product_id")
        if raw and _decimal(line.get("total_discount")) == 0:
            product_ids.append(_gid("Product", raw))
    product_ids = list(dict.fromkeys(product_ids))
    if not product_ids:
        return []
    data = client.graphql(
        """query($ids:[ID!]!){nodes(ids:$ids){... on Product{id group:metafield(namespace:"custom",key:"return_discount_machine_group"){value}}}}""",
        {"ids": product_ids},
    )
    groups = {str((node.get("group") or {}).get("value") or "") for node in data.get("nodes") or [] if node}
    return sorted(group for group in groups if group in {"mig", "tig", "plasma"})


def personal_code(payload: dict[str, Any]) -> str:
    order_name = re.sub(r"[^0-9A-Z]", "", _order_name(payload).upper()) or "ORDER"
    digest = hashlib.sha256(_order_id(payload).encode("utf-8")).hexdigest()[:6].upper()
    return f"{CODE_PREFIX}{order_name}-{digest}"


def _discount_node(client: ShopifyClient) -> str:
    data = client.graphql("""query{discountNodes(first:100){nodes{id discount{__typename ... on DiscountCodeApp{status appDiscountType{functionId}}}}}}""")
    matches = [node["id"] for node in data["discountNodes"]["nodes"] if node["discount"].get("__typename") == "DiscountCodeApp" and node["discount"].get("status") == "ACTIVE" and (node["discount"].get("appDiscountType") or {}).get("functionId") == FUNCTION_ID]
    if len(matches) != 1:
        raise RuntimeError(f"Verwacht exact één actief bedankvoordeel, gevonden: {len(matches)}")
    return matches[0]


def _code_node(client: ShopifyClient, code: str) -> str:
    data = client.graphql("""query($code:String!){codeDiscountNodeByCode(code:$code){id}}""", {"code": code})
    return str((data.get("codeDiscountNodeByCode") or {}).get("id") or "")


def ensure_code(client: ShopifyClient, code: str) -> str:
    discount_id = _discount_node(client)
    existing = _code_node(client, code)
    if existing:
        if existing != discount_id:
            raise RuntimeError("Bedankcode bestaat onder een andere korting.")
        return discount_id
    payload = client.graphql(
        """mutation($discountId:ID!,$codes:[DiscountRedeemCodeInput!]!){discountRedeemCodeBulkAdd(discountId:$discountId,codes:$codes){bulkCreation{id done importedCount failedCount} userErrors{field message code}}}""",
        {"discountId": discount_id, "codes": [{"code": code}]},
    )["discountRedeemCodeBulkAdd"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
    for _ in range(20):
        if _code_node(client, code) == discount_id:
            return discount_id
        time.sleep(0.25)
    raise RuntimeError("Bedankcode is na bulkimport niet teruggelezen.")


def _set_metafields(client: ShopifyClient, values: list[dict[str, str]]) -> None:
    payload = client.graphql(
        """mutation($metafields:[MetafieldsSetInput!]!){metafieldsSet(metafields:$metafields){metafields{id key value} userErrors{field message code}}}""",
        {"metafields": values},
    )["metafieldsSet"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))


def _has_active_credit(client: ShopifyClient, customer_id: str) -> bool:
    data = client.graphql(
        """query($id:ID!){customer(id:$id){active:metafield(namespace:"custom",key:"return_discount_active"){value} expires:metafield(namespace:"custom",key:"return_discount_expires_on"){value}}}""",
        {"id": customer_id},
    ).get("customer") or {}
    active = str((data.get("active") or {}).get("value") or "").casefold() == "true"
    expires = str((data.get("expires") or {}).get("value") or "")
    return active and (not expires or expires >= date.today().isoformat())


def _grant_customer(client: ShopifyClient, customer_id: str, order_id: str, order_name: str, code: str, expires_on: str, groups: list[str]) -> None:
    group_value = ", ".join(groups)
    _set_metafields(client, [
        {"ownerId": customer_id, "namespace": "custom", "key": "return_discount_active", "type": "boolean", "value": "true"},
        {"ownerId": customer_id, "namespace": "custom", "key": "return_discount_code", "type": "single_line_text_field", "value": code},
        {"ownerId": customer_id, "namespace": "custom", "key": "return_discount_expires_on", "type": "date", "value": expires_on},
        {"ownerId": customer_id, "namespace": "custom", "key": "return_discount_source_order", "type": "single_line_text_field", "value": order_name},
        {"ownerId": order_id, "namespace": "custom", "key": "return_discount_code", "type": "single_line_text_field", "value": code},
        {"ownerId": order_id, "namespace": "custom", "key": "return_discount_expires_on", "type": "date", "value": expires_on},
        {"ownerId": order_id, "namespace": "custom", "key": "return_discount_machine_groups", "type": "single_line_text_field", "value": group_value},
    ])
    result = client.graphql("""mutation($id:ID!,$tags:[String!]!){tagsAdd(id:$id,tags:$tags){node{id} userErrors{field message}}}""", {"id": order_id, "tags": ["Bedankvoordeel uitgegeven"]})["tagsAdd"]
    if result.get("userErrors"):
        raise RuntimeError(json.dumps(result["userErrors"], ensure_ascii=False))


def _redeem_customer(client: ShopifyClient, customer_id: str, order_id: str, order_name: str) -> None:
    _set_metafields(client, [
        {"ownerId": customer_id, "namespace": "custom", "key": "return_discount_active", "type": "boolean", "value": "false"},
        {"ownerId": customer_id, "namespace": "custom", "key": "return_discount_used_order", "type": "single_line_text_field", "value": order_name},
        {"ownerId": customer_id, "namespace": "custom", "key": "return_discount_used_at", "type": "date_time", "value": utc_now()},
    ])
    client.graphql("""mutation($id:ID!,$tags:[String!]!){tagsAdd(id:$id,tags:$tags){node{id} userErrors{field message}}}""", {"id": order_id, "tags": ["Bedankvoordeel gebruikt"]})


def send_customer_email(payload: dict[str, Any], code: str, expires_on: str) -> None:
    customer = payload.get("customer") or {}
    recipient = _recipient_email(payload)
    if not recipient:
        raise RuntimeError("Machineorder heeft geen e-mailadres voor het bedankvoordeel.")
    first_name = str(customer.get("first_name") or "").strip()
    salutation = f"Beste {first_name}," if first_name else "Beste klant,"
    body = chr(10).join([
        salutation,
        "",
        "Bedankt voor uw aankoop bij Weldingshop.nl.",
        "",
        "Voor geselecteerde MIG/MAG-, TIG- en plasma-accessoires en slijtdelen ontvangt u 10% bedankvoordeel.",
        f"Uw persoonlijke code is: {code}",
        f"De code is één keer geldig tot en met {expires_on}.",
        "",
        "Het voordeel geldt vanaf €50 aan geschikte artikelen en bedraagt maximaal €40.",
        "De beschermde minimumprijs blijft altijd leidend. De code is gekoppeld aan uw klantaccount en kan niet met andere kortingen worden gecombineerd.",
        "Gebruik bij het afrekenen hetzelfde e-mailadres als bij uw machineaankoop.",
        "",
        "Met vriendelijke groet,",
        "Weldingshop.nl",
    ])
    message = EmailMessage()
    message["From"] = MAIL_FROM
    message["To"] = recipient
    message["Subject"] = "Uw persoonlijke Weldingshop Bedankvoordeel"
    message.set_content(body)
    username, password = _smtp_credentials()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30, context=ssl.create_default_context()) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)


def _record(order_id: str, **fields: Any) -> None:
    init_database()
    fields["updated_at"] = utc_now()
    with sqlite3.connect(DATABASE_PATH) as conn:
        existing = conn.execute("SELECT order_id FROM return_discount_orders WHERE order_id=?", (order_id,)).fetchone()
        if existing:
            assignments = ",".join(f"{key}=?" for key in fields)
            conn.execute(f"UPDATE return_discount_orders SET {assignments} WHERE order_id=?", (*fields.values(), order_id))
        else:
            fields.setdefault("order_name", order_id)
            fields.setdefault("status", "processing")
            fields.setdefault("created_at", utc_now())
            columns = ["order_id", *fields.keys()]
            conn.execute(f"INSERT INTO return_discount_orders({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", (order_id, *fields.values()))


def _prior(order_id: str) -> dict[str, Any]:
    init_database()
    with sqlite3.connect(DATABASE_PATH) as conn:
        row = conn.execute("SELECT * FROM return_discount_orders WHERE order_id=?", (order_id,)).fetchone()
        columns = [item[1] for item in conn.execute("PRAGMA table_info(return_discount_orders)")]
    return dict(zip(columns, row)) if row else {}


def _cancel_source_order(client: ShopifyClient, order_id: str) -> dict[str, Any]:
    prior = _prior(order_id)
    if prior.get("status") != "completed" or not prior.get("customer_id") or not prior.get("code"):
        return {"status": "ignored", "reason": "no_active_source_credit"}
    customer = client.graphql(
        """query($id:ID!){customer(id:$id){active:metafield(namespace:"custom",key:"return_discount_active"){value} code:metafield(namespace:"custom",key:"return_discount_code"){value}}}""",
        {"id": prior["customer_id"]},
    ).get("customer") or {}
    if str((customer.get("active") or {}).get("value") or "").casefold() == "true" and str((customer.get("code") or {}).get("value") or "") == prior["code"]:
        _set_metafields(client, [{"ownerId": prior["customer_id"], "namespace": "custom", "key": "return_discount_active", "type": "boolean", "value": "false"}])
    _record(order_id, status="cancelled", reason="source_order_cancelled")
    return {"status": "cancelled"}


def _fully_refunded(client: ShopifyClient, order_id: str) -> bool:
    order = client.graphql(
        """query($id:ID!){order(id:$id){totalPriceSet{shopMoney{amount}} totalRefundedSet{shopMoney{amount}}}}""",
        {"id": order_id},
    ).get("order") or {}
    total = _decimal((((order.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount")))
    refunded = _decimal((((order.get("totalRefundedSet") or {}).get("shopMoney") or {}).get("amount")))
    return total > 0 and refunded >= total


def process_return_discount_webhook(topic: str, webhook_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    init_database()
    order_id = _gid("Order", payload.get("order_id")) if topic == "refunds/create" else _order_id(payload)
    if topic == "orders/cancelled":
        return _cancel_source_order(ShopifyClient.from_settings(), order_id)
    if topic == "refunds/create":
        client = ShopifyClient.from_settings()
        if _fully_refunded(client, order_id):
            return _cancel_source_order(client, order_id)
        return {"status": "ignored", "reason": "partial_refund"}
    if topic not in {"orders/create", "orders/paid"}:
        return {"status": "ignored", "reason": "topic"}
    order_name = _order_name(payload)
    customer_id = _customer_id(payload)
    action = basic_action(payload)
    if action == "redeem":
        _redeem_customer(ShopifyClient.from_settings(), customer_id, order_id, order_name)
        _record(order_id, order_name=order_name, customer_id=customer_id, webhook_id=webhook_id, status="redeemed", reason="personal_code_used")
        return {"status": "redeemed"}
    if action != "check_machine":
        _record(order_id, order_name=order_name, customer_id=customer_id or None, webhook_id=webhook_id, status="ignored", reason=action)
        return {"status": "ignored", "reason": action}
    prior = _prior(order_id)
    if prior.get("status") == "completed":
        return {"status": "completed", "idempotent": True}
    client = ShopifyClient.from_settings()
    groups = qualifying_machine_groups(client, payload)
    if not groups:
        _record(order_id, order_name=order_name, customer_id=customer_id, webhook_id=webhook_id, status="ignored", reason="no_full_price_qualifying_machine")
        return {"status": "ignored", "reason": "no_full_price_qualifying_machine"}
    if not prior.get("code") and _has_active_credit(client, customer_id):
        _record(order_id, order_name=order_name, customer_id=customer_id, webhook_id=webhook_id, status="ignored", reason="customer_already_has_active_credit", machine_groups=",".join(groups))
        return {"status": "ignored", "reason": "customer_already_has_active_credit"}
    code = str(prior.get("code") or personal_code(payload))
    expires_on = str(prior.get("expires_on") or (date.today() + timedelta(days=VALID_DAYS)).isoformat())
    _record(order_id, order_name=order_name, customer_id=customer_id, webhook_id=webhook_id, status="processing", code=code, expires_on=expires_on, machine_groups=",".join(groups))
    ensure_code(client, code)
    _grant_customer(client, customer_id, order_id, order_name, code, expires_on, groups)
    if not prior.get("email_sent_at"):
        send_customer_email(payload, code, expires_on)
    sent_at = prior.get("email_sent_at") or utc_now()
    _record(order_id, status="completed", email_sent_at=sent_at, reason="new_full_price_machine_order")
    return {"status": "completed", "expires_on": expires_on, "machine_groups": groups}


def expire_return_credits() -> dict[str, int]:
    init_database()
    client = ShopifyClient.from_settings()
    today = date.today().isoformat()
    with sqlite3.connect(DATABASE_PATH) as conn:
        rows = conn.execute(
            """SELECT order_id,customer_id FROM return_discount_orders
               WHERE status='completed' AND expires_on<? AND customer_id IS NOT NULL""",
            (today,),
        ).fetchall()
    expired = 0
    for order_id, customer_id in rows:
        _set_metafields(client, [{
            "ownerId": customer_id,
            "namespace": "custom",
            "key": "return_discount_active",
            "type": "boolean",
            "value": "false",
        }])
        _record(order_id, status="expired", reason="validity_ended")
        expired += 1
    return {"expired": expired}
