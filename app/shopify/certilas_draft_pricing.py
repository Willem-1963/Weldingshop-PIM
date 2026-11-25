from __future__ import annotations

from typing import Any

from app.shopify.client import ShopifyClient

SUPPORTED_TOPICS = {"draft_orders/create", "draft_orders/update"}
REQUIRED_TAG = "winkelprijs"
EXPECTED_CURRENCY = "EUR"
PRICE_LIST_ID = "gid://shopify/PriceList/31914918217"

def _gid(kind: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("gid://"):
        return text
    return f"gid://shopify/{kind}/{text}"


def _fixed_prices(
    client: ShopifyClient, variant_ids: list[str]
) -> dict[str, dict[str, str]]:
    prices: dict[str, dict[str, str]] = {}
    unique_ids = list(dict.fromkeys(variant_ids))
    for chunk_start in range(0, len(unique_ids), 50):
        chunk = unique_ids[chunk_start : chunk_start + 50]
        definitions = ["$id:ID!"]
        fields = []
        variables: dict[str, Any] = {"id": PRICE_LIST_ID}
        for index, variant_id in enumerate(chunk):
            key = f"q{index}"
            alias = f"p{index}"
            definitions.append("$" + key + ":String!")
            variables[key] = f"variant_id:{variant_id.rsplit('/', 1)[-1]}"
            fields.append(
                alias + ":prices(first:2,query:$" + key + ",originType:FIXED)"
                "{nodes{variant{id} price{amount currencyCode}}}"
            )
        query = (
            "query CertilasRetailFixedPrices("
            + ",".join(definitions)
            + "){priceList(id:$id){"
            + " ".join(fields)
            + "}}"
        )
        data = client.graphql(query, variables)
        price_list = data.get("priceList")
        if not price_list:
            raise RuntimeError("De NL retail-prijslijst bestaat niet.")
        for index in range(len(chunk)):
            for row in (price_list.get(f"p{index}") or {}).get("nodes") or []:
                variant_id = ((row.get("variant") or {}).get("id") or "").strip()
                price = row.get("price") or {}
                if variant_id and price.get("amount") and price.get("currencyCode"):
                    prices[variant_id] = {
                        "amount": str(price["amount"]),
                        "currencyCode": str(price["currencyCode"]),
                    }
    return prices

def _load_draft(client: ShopifyClient, draft_id: str) -> dict[str, Any] | None:
    data = client.graphql(
        """
        query DraftForRetailPricing($id:ID!){
          draftOrder(id:$id){
            id name tags presentmentCurrencyCode
            lineItems(first:250){
              nodes{
                uuid quantity custom sku
                customAttributes{key value}
                components{uuid}
                appliedDiscount{valueType}
                priceOverride{amount currencyCode}
                variant{
                  id
                  managed:metafield(namespace:"custom",key:"certilas_product"){value}
                }
              }
              pageInfo{hasNextPage}
            }
          }
        }
        """,
        {"id": draft_id},
    )
    return data.get("draftOrder")


def process_draft_order_webhook(
    topic: str,
    webhook_id: str,
    payload: dict[str, Any],
    *,
    client: ShopifyClient | None = None,
) -> dict[str, Any]:
    normalized_topic = str(topic or "").strip().lower()
    if normalized_topic not in SUPPORTED_TOPICS:
        return {"status": "ignored", "reason": "unsupported_topic"}

    draft_id = _gid(
        "DraftOrder", payload.get("admin_graphql_api_id") or payload.get("id")
    )
    if not draft_id:
        raise ValueError("Conceptbestelling-ID ontbreekt.")
    client = client or ShopifyClient.from_settings()
    draft = _load_draft(client, draft_id)
    if not draft:
        return {"status": "ignored", "reason": "draft_missing", "draft_id": draft_id}

    tags = {str(tag).strip().casefold() for tag in draft.get("tags") or []}
    if REQUIRED_TAG.casefold() not in tags:
        return {"status": "ignored", "reason": "no_winkelprijs_tag", "draft": draft.get("name")}
    if draft.get("presentmentCurrencyCode") != EXPECTED_CURRENCY:
        return {"status": "ignored", "reason": "non_eur", "draft": draft.get("name")}

    connection = draft.get("lineItems") or {}
    if (connection.get("pageInfo") or {}).get("hasNextPage"):
        return {"status": "ignored", "reason": "more_than_250_lines", "draft": draft.get("name")}
    lines = connection.get("nodes") or []
    unsafe = []
    for line in lines:
        if line.get("custom"):
            unsafe.append("maatwerkregel")
        if line.get("components"):
            unsafe.append("bundelregel")
        if line.get("appliedDiscount") is not None:
            unsafe.append("regelkorting")
        if not line.get("variant"):
            unsafe.append("regel zonder variant")
    if unsafe:
        return {
            "status": "ignored",
            "reason": "unsupported_lines",
            "draft": draft.get("name"),
            "details": sorted(set(unsafe)),
        }

    managed_variant_ids = [
        line["variant"]["id"]
        for line in lines
        if ((line["variant"].get("managed") or {}).get("value") or "").casefold()
        == "true"
    ]
    prices = _fixed_prices(client, managed_variant_ids)
    line_inputs = []
    changed_skus = []
    missing_skus = []
    for line in lines:
        variant = line["variant"]
        variant_id = variant["id"]
        line_input: dict[str, Any] = {
            "variantId": variant_id,
            "quantity": line["quantity"],
            "uuid": line["uuid"],
        }
        if line.get("customAttributes"):
            line_input["customAttributes"] = line["customAttributes"]
        target = None
        if ((variant.get("managed") or {}).get("value") or "").casefold() == "true":
            target = prices.get(variant_id)
            if not target or target.get("currencyCode") != EXPECTED_CURRENCY:
                missing_skus.append(line.get("sku") or variant_id)
                target = None
        if target:
            line_input["priceOverride"] = target
            current = line.get("priceOverride") or {}
            if str(current.get("amount") or "") != str(target["amount"]) or current.get("currencyCode") != target["currencyCode"]:
                changed_skus.append(line.get("sku") or variant_id)
        elif line.get("priceOverride") is not None:
            line_input["priceOverride"] = line["priceOverride"]
        line_inputs.append(line_input)

    if not changed_skus:
        return {
            "status": "noop",
            "reason": "prices_already_current",
            "draft": draft.get("name"),
            "missing_skus": missing_skus,
        }

    result = client.graphql(
        """
        mutation ApplyRetailPricesToDraft($id:ID!,$input:DraftOrderInput!){
          draftOrderUpdate(id:$id,input:$input){
            draftOrder{id name tags presentmentCurrencyCode}
            userErrors{field message}
          }
        }
        """,
        {"id": draft["id"], "input": {"lineItems": line_inputs}},
    )["draftOrderUpdate"]
    errors = result.get("userErrors") or []
    if errors:
        raise RuntimeError(str(errors))
    return {
        "status": "completed",
        "draft": draft.get("name"),
        "changed_skus": changed_skus,
        "missing_skus": missing_skus,
        "webhook_id": webhook_id,
    }
