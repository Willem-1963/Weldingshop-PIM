from app.shopify import certilas_draft_pricing as mod


class FakeClient:
    def __init__(self, draft, prices=None):
        self.draft = draft
        self.prices = prices or []
        self.calls = []

    def graphql(self, query, variables=None):
        self.calls.append((query, variables))
        if "DraftForRetailPricing" in query:
            return {"draftOrder": self.draft}
        if "CertilasRetailFixedPrices" in query:
            return {"priceList": {"p0": {"nodes": self.prices}}}
        if "ApplyRetailPricesToDraft" in query:
            return {"draftOrderUpdate": {"draftOrder": {"id": self.draft["id"], "name": self.draft["name"]}, "userErrors": []}}
        raise AssertionError(query)


def draft(price=None, tags=None, discount=None):
    return {
        "id": "gid://shopify/DraftOrder/1",
        "name": "#D1",
        "tags": tags if tags is not None else ["winkelprijs"],
        "presentmentCurrencyCode": "EUR",
        "lineItems": {
            "nodes": [{
                "uuid": "u1", "quantity": 1, "custom": False, "sku": "C1",
                "customAttributes": [], "components": [], "appliedDiscount": discount,
                "priceOverride": price,
                "variant": {"id": "gid://shopify/ProductVariant/9", "managed": {"value": "true"}},
            }],
            "pageInfo": {"hasNextPage": False},
        },
    }


def price(amount="10.50"):
    return {"variant": {"id": "gid://shopify/ProductVariant/9"}, "price": {"amount": amount, "currencyCode": "EUR"}}


def test_unsupported_topic_does_nothing():
    client = FakeClient(draft())
    result = mod.process_draft_order_webhook("orders/create", "w1", {}, client=client)
    assert result["status"] == "ignored"
    assert client.calls == []


def test_missing_tag_does_not_load_prices():
    client = FakeClient(draft(tags=[]))
    result = mod.process_draft_order_webhook("draft_orders/update", "w1", {"id": 1}, client=client)
    assert result["reason"] == "no_winkelprijs_tag"
    assert len(client.calls) == 1


def test_applies_current_pos_price():
    client = FakeClient(draft(), [price()])
    result = mod.process_draft_order_webhook("draft_orders/update", "w1", {"id": 1}, client=client)
    assert result["status"] == "completed"
    mutation = [call for call in client.calls if "ApplyRetailPricesToDraft" in call[0]][0]
    assert mutation[1]["input"]["lineItems"][0]["priceOverride"] == {"amount": "10.50", "currencyCode": "EUR"}


def test_already_current_is_noop():
    client = FakeClient(draft({"amount": "10.50", "currencyCode": "EUR"}), [price()])
    result = mod.process_draft_order_webhook("draft_orders/update", "w1", {"id": 1}, client=client)
    assert result["status"] == "noop"
    assert not any("ApplyRetailPricesToDraft" in call[0] for call in client.calls)


def test_discounted_draft_fails_closed():
    client = FakeClient(draft(discount={"valueType": "PERCENTAGE"}), [price()])
    result = mod.process_draft_order_webhook("draft_orders/update", "w1", {"id": 1}, client=client)
    assert result["reason"] == "unsupported_lines"
    assert len(client.calls) == 1
