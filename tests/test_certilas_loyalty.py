from app.shopify.certilas_loyalty import classify_paid_order, personal_code


def order(**overrides):
    value = {
        "id": 7803905048905,
        "admin_graphql_api_id": "gid://shopify/Order/7803905048905",
        "name": "WS50376",
        "financial_status": "paid",
        "source_name": "web",
        "customer": {"id": 9128890007881},
        "line_items": [{"vendor": "Certilas", "total_discount": "0.00"}],
        "discount_codes": [],
    }
    value.update(overrides)
    return value


def test_full_price_certilas_web_order_grants():
    assert classify_paid_order(order()) == "grant"


def test_personal_code_redemption_never_reissues():
    assert classify_paid_order(order(discount_codes=[{"code": "CERTILAS-50376-ABC123"}])) == "redeem"


def test_discounted_certilas_order_does_not_grant():
    assert classify_paid_order(order(line_items=[{"vendor": "Certilas", "total_discount": "1.00"}])) == "not_full_price"


def test_pos_order_does_not_grant():
    assert classify_paid_order(order(source_name="pos")) == "not_online_store"


def test_code_is_stable_for_retry():
    assert personal_code(order()) == personal_code(order())
    assert personal_code(order()).startswith("CERTILAS-WS50376-")
