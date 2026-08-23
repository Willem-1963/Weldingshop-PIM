from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import requests

from app.suppliers.hub import (
    REGISTRY_PATH,
    _connect,
    _decrypt,
    _encrypt,
    init_registry,
    list_products,
    utc_now,
)


DEFAULT_API_VERSION = "2026-07"
THROTTLE_RETRIES = 8
THROTTLE_FALLBACK_SECONDS = 1.5
THROTTLE_MAX_WAIT_SECONDS = 30.0


def _is_throttled(errors: Any) -> bool:
    return any(
        str((error.get("extensions") or {}).get("code") or "").upper()
        == "THROTTLED"
        for error in errors or []
        if isinstance(error, dict)
    )


def _throttle_wait_seconds(payload: dict[str, Any], attempt: int) -> float:
    """Use Shopify's reported query budget, with bounded fallback backoff."""
    cost = (payload.get("extensions") or {}).get("cost") or {}
    status = cost.get("throttleStatus") or {}
    requested = float(cost.get("requestedQueryCost") or 0)
    available = float(status.get("currentlyAvailable") or 0)
    restore_rate = float(status.get("restoreRate") or 0)
    if restore_rate > 0 and requested > available:
        return min(
            THROTTLE_MAX_WAIT_SECONDS,
            max(1.0, (requested - available) / restore_rate + 0.25),
        )
    return min(
        THROTTLE_MAX_WAIT_SECONDS,
        THROTTLE_FALLBACK_SECONDS * (2 ** attempt),
    )


def init_shopify_settings() -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shopify_settings (
                id INTEGER PRIMARY KEY CHECK (id=1),
                shop_domain TEXT,
                access_token_encrypted TEXT,
                api_version TEXT NOT NULL DEFAULT '2026-07',
                location_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shopify_connection_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checked_at TEXT NOT NULL,
                status TEXT NOT NULL,
                shop_domain TEXT,
                message TEXT
            );
            """
        )
        columns = {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(shopify_settings)"
            ).fetchall()
        }
        if "webhook_secret_encrypted" not in columns:
            conn.execute(
                "ALTER TABLE shopify_settings ADD COLUMN webhook_secret_encrypted TEXT"
            )


def normalize_shop_domain(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.removeprefix("https://").removeprefix("http://").strip("/")
    if "/" in value:
        value = value.split("/", 1)[0]
    if value and "." not in value:
        value = f"{value}.myshopify.com"
    return value


def save_shopify_settings(
    *,
    shop_domain: str,
    access_token: str | None = None,
    api_version: str = DEFAULT_API_VERSION,
    location_id: str = "",
    enabled: bool = False,
    webhook_secret: str | None = None,
) -> None:
    init_shopify_settings()
    shop_domain = normalize_shop_domain(shop_domain)
    with _connect(REGISTRY_PATH) as conn:
        current = conn.execute(
            """
            SELECT access_token_encrypted,webhook_secret_encrypted
            FROM shopify_settings WHERE id=1
            """
        ).fetchone()
        encrypted = (
            _encrypt(access_token)
            if access_token is not None
            else (current["access_token_encrypted"] if current else None)
        )
        encrypted_webhook_secret = (
            _encrypt(webhook_secret)
            if webhook_secret is not None
            else (current["webhook_secret_encrypted"] if current else None)
        )
        conn.execute(
            """
            INSERT INTO shopify_settings(
                id,shop_domain,access_token_encrypted,api_version,
                location_id,enabled,updated_at,webhook_secret_encrypted
            ) VALUES(1,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                shop_domain=excluded.shop_domain,
                access_token_encrypted=excluded.access_token_encrypted,
                api_version=excluded.api_version,
                location_id=excluded.location_id,
                enabled=excluded.enabled,
                webhook_secret_encrypted=excluded.webhook_secret_encrypted,
                updated_at=excluded.updated_at
            """,
            (
                shop_domain, encrypted, api_version or DEFAULT_API_VERSION,
                location_id.strip(), int(enabled), utc_now(), encrypted_webhook_secret,
            ),
        )


def get_shopify_settings(include_token: bool = False) -> dict[str, Any]:
    init_shopify_settings()
    with _connect(REGISTRY_PATH) as conn:
        row = conn.execute("SELECT * FROM shopify_settings WHERE id=1").fetchone()
    if not row:
        return {
            "shop_domain": "",
            "api_version": DEFAULT_API_VERSION,
            "location_id": "",
            "enabled": 0,
            "has_token": False,
            "has_webhook_secret": False,
        }
    result = dict(row)
    result["has_token"] = bool(result.get("access_token_encrypted"))
    result["has_webhook_secret"] = bool(result.get("webhook_secret_encrypted"))
    if include_token:
        result["access_token"] = _decrypt(result.get("access_token_encrypted"))
        result["webhook_secret"] = _decrypt(result.get("webhook_secret_encrypted"))
    result.pop("access_token_encrypted", None)
    result.pop("webhook_secret_encrypted", None)
    return result


@dataclass
class ShopifyClient:
    shop_domain: str
    access_token: str
    api_version: str = DEFAULT_API_VERSION

    @classmethod
    def from_settings(cls) -> "ShopifyClient":
        settings = get_shopify_settings(include_token=True)
        if not settings.get("shop_domain"):
            raise ValueError("Shopify-shop ontbreekt.")
        if not settings.get("access_token"):
            raise ValueError("Shopify Admin API-token ontbreekt.")
        return cls(
            settings["shop_domain"],
            settings["access_token"],
            settings.get("api_version") or DEFAULT_API_VERSION,
        )

    @property
    def endpoint(self) -> str:
        return (
            f"https://{self.shop_domain}/admin/api/"
            f"{self.api_version}/graphql.json"
        )

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(THROTTLE_RETRIES):
            response = requests.post(
                self.endpoint,
                headers={
                    "X-Shopify-Access-Token": self.access_token,
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables or {}},
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            errors = payload.get("errors") or []
            if not errors:
                return payload.get("data") or {}
            if not _is_throttled(errors) or attempt == THROTTLE_RETRIES - 1:
                raise RuntimeError(str(errors))
            time.sleep(_throttle_wait_seconds(payload, attempt))
        raise AssertionError("unreachable")

    def shop_and_locations(self) -> dict[str, Any]:
        return self.graphql(
            """
            query PimConnectionTest {
              shop { name myshopifyDomain }
              locations(first: 50, includeInactive: false) {
                nodes { id name isActive fulfillsOnlineOrders }
              }
            }
            """
        )

    def find_variant_by_sku(self, sku: str) -> dict[str, Any] | None:
        escaped = sku.replace("\\", "\\\\").replace('"', '\\"')
        data = self.graphql(
            """
            query PimVariantBySku($query: String!) {
              productVariants(first: 10, query: $query) {
                nodes {
                  id sku title barcode price inventoryQuantity
                  selectedOptions { name value }
                  inventoryItem { id tracked }
                  media(first: 10) { nodes { id } }
                  product {
                    id title handle status vendor
                    options { name }
                    variants(first: 2) { nodes { id } }
                  }
                }
              }
            }
            """,
            {"query": f'sku:"{escaped}"'},
        )
        nodes = (data.get("productVariants") or {}).get("nodes") or []
        exact = [node for node in nodes if (node.get("sku") or "").strip().upper() == sku.strip().upper()]
        if len(exact) > 1:
            raise RuntimeError(f"Meerdere Shopify-varianten hebben SKU {sku}.")
        return exact[0] if exact else None


def test_shopify_connection() -> dict[str, Any]:
    settings = get_shopify_settings()
    try:
        data = ShopifyClient.from_settings().shop_and_locations()
        shop = data.get("shop") or {}
        locations = (data.get("locations") or {}).get("nodes") or []
        result = {
            "ok": True,
            "shop": shop,
            "locations": locations,
            "message": f"Verbonden met {shop.get('name') or shop.get('myshopifyDomain')}.",
        }
        status = "success"
    except Exception as exc:
        result = {"ok": False, "shop": {}, "locations": [], "message": str(exc)}
        status = "error"
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            """
            INSERT INTO shopify_connection_log(checked_at,status,shop_domain,message)
            VALUES(?,?,?,?)
            """,
            (utc_now(), status, settings.get("shop_domain"), result["message"]),
        )
    return result


def get_shopify_locations() -> list[dict[str, Any]]:
    """Return the active inventory locations without writing a log entry."""
    data = ShopifyClient.from_settings().shop_and_locations()
    return (data.get("locations") or {}).get("nodes") or []


def get_shopify_collections() -> list[dict[str, Any]]:
    """Return all Shopify collections for configuration screens."""
    client = ShopifyClient.from_settings()
    collections: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = client.graphql(
            """
            query PimCollections($after:String){
              collections(first:250,after:$after,sortKey:TITLE){
                nodes{id title handle productsCount{count}}
                pageInfo{hasNextPage endCursor}
              }
            }
            """,
            {"after": cursor},
        )
        connection = data.get("collections") or {}
        collections.extend(connection.get("nodes") or [])
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
    return collections


def collection_product_skus(
    client: ShopifyClient, collection_id: str
) -> set[str]:
    """Return normalized SKUs of every product in one Shopify collection."""
    skus: set[str] = set()
    cursor: str | None = None
    while True:
        data = client.graphql(
            """
            query PimCollectionProducts($id:ID!,$after:String){
              collection(id:$id){
                products(first:100,after:$after){
                  nodes{variants(first:100){nodes{sku}}}
                  pageInfo{hasNextPage endCursor}
                }
              }
            }
            """,
            {"id": collection_id, "after": cursor},
        )
        connection = ((data.get("collection") or {}).get("products") or {})
        for product in connection.get("nodes") or []:
            for variant in (product.get("variants") or {}).get("nodes") or []:
                sku = str(variant.get("sku") or "").strip().upper()
                if sku:
                    skus.add(sku)
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
    return skus


def set_collection_inventory_policy(
    client: ShopifyClient,
    collection_id: str,
    *,
    continue_selling: bool,
) -> int:
    """Apply one inventory policy to all variants in a collection."""
    cursor: str | None = None
    variants_by_product: dict[str, list[dict[str, str]]] = {}
    while True:
        data = client.graphql(
            """
            query PimCollectionPolicyProducts($id:ID!,$after:String){
              collection(id:$id){
                products(first:100,after:$after){
                  nodes{id variants(first:100){nodes{id}}}
                  pageInfo{hasNextPage endCursor}
                }
              }
            }
            """,
            {"id": collection_id, "after": cursor},
        )
        connection = ((data.get("collection") or {}).get("products") or {})
        for product in connection.get("nodes") or []:
            product_id = str(product.get("id") or "")
            variants = [
                {
                    "id": str(variant["id"]),
                    "inventoryPolicy": (
                        "CONTINUE" if continue_selling else "DENY"
                    ),
                }
                for variant in (product.get("variants") or {}).get("nodes") or []
                if variant.get("id")
            ]
            if product_id and variants:
                variants_by_product.setdefault(product_id, []).extend(variants)
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
    mutation = """
    mutation PimSetCollectionPolicy(
      $productId:ID!,$variants:[ProductVariantsBulkInput!]!
    ){
      productVariantsBulkUpdate(productId:$productId,variants:$variants){
        userErrors{field message}
      }
    }
    """
    updated = 0
    for product_id, variants in variants_by_product.items():
        data = client.graphql(
            mutation, {"productId": product_id, "variants": variants}
        )
        errors = (data.get("productVariantsBulkUpdate") or {}).get(
            "userErrors"
        ) or []
        if errors:
            raise RuntimeError(
                "; ".join(str(error.get("message") or error) for error in errors)
            )
        updated += len(variants)
    return updated


def ensure_derived_inventory_webhooks(
    callback_url: str = "https://pim.weldingshop.nl/shopify/webhooks/inventory",
) -> dict[str, Any]:
    """Create the three required subscriptions, without duplicating existing ones."""
    client = ShopifyClient.from_settings()
    topics = {"ORDERS_CREATE", "ORDERS_CANCELLED", "REFUNDS_CREATE"}
    data = client.graphql(
        """
        query PimInventoryWebhooks($first:Int!){
          webhookSubscriptions(first:$first){nodes{id topic uri}}
        }
        """,
        {"first": 250},
    )
    existing = {
        node.get("topic")
        for node in (data.get("webhookSubscriptions") or {}).get("nodes") or []
        if node.get("uri") == callback_url
    }
    created = []
    for topic in sorted(topics - existing):
        result = client.graphql(
            """
            mutation PimCreateInventoryWebhook(
              $topic:WebhookSubscriptionTopic!,
              $webhookSubscription:WebhookSubscriptionInput!
            ){
              webhookSubscriptionCreate(
                topic:$topic,webhookSubscription:$webhookSubscription
              ){
                webhookSubscription{id topic uri}
                userErrors{field message}
              }
            }
            """,
            {"topic": topic, "webhookSubscription": {"uri": callback_url}},
        )["webhookSubscriptionCreate"]
        if result.get("userErrors"):
            raise RuntimeError(str(result["userErrors"]))
        created.append(result["webhookSubscription"])
    return {
        "callback_url": callback_url,
        "created": created,
        "active_topics": sorted(topics),
    }


def preview_supplier_matches(slug: str, limit: int = 25) -> list[dict[str, Any]]:
    client = ShopifyClient.from_settings()
    rows = []
    for product in list_products(slug, max(1, min(int(limit), 100))):
        match = client.find_variant_by_sku(product["sku"])
        pim_price = (
            product["sale_price"]
            if product.get("sale_price") is not None
            else product.get("price")
        )
        rows.append(
            {
                "SKU": product["sku"],
                "PIM titel": product.get("source_title") or "",
                "Gevonden": bool(match),
                "Shopify titel": (match or {}).get("product", {}).get("title", ""),
                "PIM prijs": pim_price,
                "Shopify prijs": (match or {}).get("price"),
                "Shopify status": (match or {}).get("product", {}).get("status", ""),
                "Variant ID": (match or {}).get("id", ""),
                "Product ID": (match or {}).get("product", {}).get("id", ""),
            }
        )
    return rows


def get_metafield_definitions(owner_type: str) -> list[dict[str, Any]]:
    if owner_type not in {"PRODUCT", "PRODUCTVARIANT"}:
        raise ValueError("Ongeldig Shopify-metaveldeigenaarstype.")
    data = ShopifyClient.from_settings().graphql(
        """
        query PimMetafieldDefinitions($ownerType:MetafieldOwnerType!){
          metafieldDefinitions(first:250,ownerType:$ownerType){
            nodes{
              id name namespace key description
              type{name}
            }
          }
        }
        """,
        {"ownerType": owner_type},
    )
    return (data.get("metafieldDefinitions") or {}).get("nodes") or []


def get_shopify_writable_fields() -> list[dict[str, str]]:
    data = ShopifyClient.from_settings().graphql(
        """
        query PimWritableFields {
          product:__type(name:"ProductSetInput"){
            inputFields{name description type{kind name ofType{kind name ofType{kind name}}}}
          }
          variant:__type(name:"ProductVariantSetInput"){
            inputFields{name description type{kind name ofType{kind name ofType{kind name}}}}
          }
          inventory:__type(name:"InventoryItemInput"){
            inputFields{name description type{kind name ofType{kind name ofType{kind name}}}}
          }
          seo:__type(name:"SEOInput"){
            inputFields{name description type{kind name ofType{kind name ofType{kind name}}}}
          }
        }
        """
    )

    def type_name(field_type: dict[str, Any]) -> str:
        current = field_type
        wrappers = []
        while current:
            if current.get("kind") == "LIST":
                wrappers.append("lijst")
            if current.get("name"):
                return " · ".join([current["name"], *wrappers])
            current = current.get("ofType")
        return "onbekend"

    result = []
    labels = {
        "product": "Product",
        "variant": "Variant",
        "inventory": "Voorraaditem",
        "seo": "SEO",
    }
    for section, label in labels.items():
        for field in (data.get(section) or {}).get("inputFields") or []:
            if field["name"] == "id":
                continue
            result.append({
                "target": f"{section}.{field['name']}",
                "section": label,
                "field": field["name"],
                "type": type_name(field["type"]),
                "description": field.get("description") or "",
            })
    return result
