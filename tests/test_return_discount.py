from app.shopify.return_discount import _fully_refunded, basic_action, personal_code, qualifying_machine_groups


def payload(**changes):
    value = {
        "id": 123,
        "admin_graphql_api_id": "gid://shopify/Order/123",
        "name": "WS99999",
        "financial_status": "paid",
        "source_name": "web",
        "customer": {"id": 456, "admin_graphql_api_id": "gid://shopify/Customer/456", "email": "test@example.com"},
        "discount_codes": [],
        "line_items": [],
    }
    value.update(changes)
    return value


def test_online_and_pos_are_eligible_sources():
    assert basic_action(payload(source_name="web")) == "check_machine"
    assert basic_action(payload(source_name="pos")) == "check_machine"


def test_unpaid_and_unsupported_source_fail_closed():
    assert basic_action(payload(financial_status="pending")) == "not_paid"
    assert basic_action(payload(source_name="shopify_draft_order")) == "unsupported_source"




def test_pos_without_email_fails_closed():
    assert basic_action(payload(source_name="pos", email=None, contact_email=None, customer={"id": 456})) == "no_email"


def test_missing_customer_fails_closed():
    assert basic_action(payload(customer=None)) == "no_customer"


def test_personal_code_is_redeemed_not_reissued():
    assert basic_action(payload(discount_codes=[{"code": "bedankt-ws1-abc"}])) == "redeem"


def test_personal_code_is_stable_and_prefixed():
    assert personal_code(payload()) == personal_code(payload())
    assert personal_code(payload()).startswith("BEDANKT-WS99999-")


class FakeClient:
    def graphql(self, query, variables):
        assert variables["ids"] == ["gid://shopify/Product/11", "gid://shopify/Product/22"]
        return {"nodes": [
            {"id": variables["ids"][0], "group": {"value": "mig"}},
            {"id": variables["ids"][1], "group": None},
        ]}


def test_only_full_price_marked_machine_qualifies():
    order = payload(line_items=[
        {"product_id": 11, "total_discount": "0.00"},
        {"product_id": 22, "total_discount": "0.00"},
        {"product_id": 33, "total_discount": "1.00"},
    ])
    assert qualifying_machine_groups(FakeClient(), order) == ["mig"]


class FakeRefundClient:
    def __init__(self, total, refunded):
        self.total = total
        self.refunded = refunded

    def graphql(self, query, variables):
        return {"order": {"totalPriceSet": {"shopMoney": {"amount": self.total}}, "totalRefundedSet": {"shopMoney": {"amount": self.refunded}}}}


def test_only_full_refund_revokes_source_credit():
    assert _fully_refunded(FakeRefundClient("100.00", "100.00"), "gid://shopify/Order/1") is True
    assert _fully_refunded(FakeRefundClient("100.00", "20.00"), "gid://shopify/Order/1") is False
