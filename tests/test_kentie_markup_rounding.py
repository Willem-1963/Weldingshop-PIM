from app.suppliers.discounts import _calculated_sales_price


def test_markup_on_cost_uses_decimal_half_up_rounding():
    assert _calculated_sales_price(35, 24.5, 41, 'markup_on_cost') == 34.55
    assert _calculated_sales_price(555, 388.5, 41, 'markup_on_cost') == 547.79
    assert _calculated_sales_price(35, 17.5, 41, 'markup_on_cost') == 24.68
