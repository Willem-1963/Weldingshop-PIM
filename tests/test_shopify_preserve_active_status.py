from app.shopify.sync import _staged_product_status


def test_existing_active_product_stays_active_during_sync():
    assert _staged_product_status(
        "gid://shopify/Product/1",
        {"gid://shopify/Product/1": "ACTIVE"},
    ) == "ACTIVE"


def test_new_product_starts_as_draft_until_validation_finishes():
    assert _staged_product_status("", {}) == "DRAFT"


def test_existing_draft_product_is_not_published_before_validation():
    assert _staged_product_status(
        "gid://shopify/Product/2",
        {"gid://shopify/Product/2": "DRAFT"},
    ) == "DRAFT"
