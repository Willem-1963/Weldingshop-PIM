from app.shopify.sync import (
    _automatic_status_demotion_allowed, _draft_status_lock_skus,
    _product_set_row_skus, _staged_product_status,
)


def test_automatic_status_demotion_is_disabled_by_default():
    assert _automatic_status_demotion_allowed({}) is False
    assert _automatic_status_demotion_allowed({"request_options": {}}) is False


def test_automatic_status_demotion_requires_explicit_opt_in():
    supplier = {"request_options": {"allow_shopify_status_demotion": True}}
    assert _automatic_status_demotion_allowed(supplier) is True



def test_draft_status_lock_is_exact_and_normalized():
    supplier = {"request_options": {"draft_status_lock_skus": [" blstr ", ""]}}
    assert _draft_status_lock_skus(supplier) == {"BLSTR"}
    assert _product_set_row_skus({"input": {"variants": [{"sku": "blstr"}]}}) == {"BLSTR"}


def test_owner_draft_lock_overrides_existing_active_status():
    assert _staged_product_status("p1", {"p1": "ACTIVE"}, force_draft=True) == "DRAFT"
    assert _staged_product_status("p1", {"p1": "ACTIVE"}) == "ACTIVE"
