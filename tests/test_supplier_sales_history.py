from app.suppliers.sales_history import (
    _history_bounds,
    _matches_vendor,
    _orders_with_supplier_lines,
)


def test_vendor_matching_is_exact_and_case_insensitive():
    assert _matches_vendor("Certilas", ("certilas", "CEWELD"))
    assert _matches_vendor(" ceweld ", ("Certilas", "CEWELD"))
    assert not _matches_vendor("Certilas-Legeringstoeslag", ("Certilas",))
    assert not _matches_vendor("Weldingshop", ("Certilas",))


def test_history_metadata_ignores_unrelated_shopify_orders():
    orders = {
        "unrelated": {
            "date": "2019-01-01", "cancelled": False, "lines": [],
        },
        "supplier-old": {
            "date": "2024-03-04", "cancelled": False,
            "lines": [{"sku": "A", "quantity": 1}],
        },
        "supplier-new": {
            "date": "2026-08-31", "cancelled": False,
            "lines": [{"sku": "B", "quantity": 2}],
        },
    }

    filtered = _orders_with_supplier_lines(orders)

    assert set(filtered) == {"supplier-old", "supplier-new"}
    assert _history_bounds(filtered) == ("2024-03-04", "2026-08-31")


def test_cancelled_supplier_order_does_not_expand_history_bounds():
    orders = {
        "cancelled": {
            "date": "2026-08-31", "cancelled": True,
            "lines": [{"sku": "A", "quantity": 1}],
        }
    }

    filtered = _orders_with_supplier_lines(orders)

    assert set(filtered) == {"cancelled"}
    assert _history_bounds(filtered) == ("", "")
