from app.shopify import sync


def test_collection_inventory_policy_exclusion_has_highest_priority(monkeypatch):
    memberships = {
        "continue": {"SKU-CONTINUE", "SKU-DENY", "SKU-EXCLUDED"},
        "deny": {"SKU-DENY", "SKU-EXCLUDED"},
        "exclude": {"SKU-EXCLUDED"},
    }
    monkeypatch.setattr(
        sync,
        "collection_product_skus",
        lambda client, collection_id: memberships[collection_id],
    )
    supplier = {
        "request_options": {
            "continue_selling_collection_rules": [
                {"collection_id": "continue", "continue_selling": True},
                {"collection_id": "deny", "continue_selling": False},
                {"collection_id": "exclude", "exclude": True},
            ]
        }
    }

    continue_skus, deny_skus, excluded_skus = (
        sync._collection_inventory_policy_skus(object(), supplier)
    )

    assert continue_skus == {"SKU-CONTINUE"}
    assert deny_skus == {"SKU-DENY"}
    assert excluded_skus == {"SKU-EXCLUDED"}


def test_excluded_collection_is_not_bulk_updated(monkeypatch):
    updated = []
    monkeypatch.setattr(
        sync,
        "set_collection_inventory_policy",
        lambda client, collection_id, continue_selling: updated.append(
            (collection_id, continue_selling)
        ) or 1,
    )
    supplier = {
        "request_options": {
            "continue_selling_collection_rules": [
                {"collection_id": "excluded", "exclude": True},
                {"collection_id": "active", "continue_selling": True},
            ]
        }
    }

    count = sync._apply_collection_inventory_policies(object(), supplier)

    assert count == 1
    assert updated == [("active", True)]
