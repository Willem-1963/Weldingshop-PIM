from app.product_maker_standalone.page import _missing_price


def test_missing_price_warning_accepts_comma_decimal():
    assert _missing_price({"sale_price": "0"}) is True
    assert _missing_price({"sale_price": ""}) is True
    assert _missing_price({"sale_price": "geen prijs"}) is True
    assert _missing_price({"sale_price": "12,50"}) is False
