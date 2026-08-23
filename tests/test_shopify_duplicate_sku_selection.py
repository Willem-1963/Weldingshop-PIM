import pytest

from app.shopify.sync import _shopify_products


class FakeClient:
    def __init__(self, products):
        self.products = products
        self.queries = []

    def graphql(self, query, variables):
        self.queries.append(query)
        return {
            "products": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": self.products,
            }
        }


def product(product_id, status, sku="920190", variant_count=1):
    return {
        "id": product_id,
        "title": product_id,
        "handle": product_id,
        "status": status,
        "variants": {"nodes": [
            {"id": f"{product_id}-v-{index}", "sku": sku if index == 0 else f"OTHER-{index}"}
            for index in range(variant_count)
        ]},
    }


@pytest.mark.parametrize("products", [
    [product("gid://shopify/Product/200", "DRAFT"), product("gid://shopify/Product/100", "ACTIVE")],
    [product("gid://shopify/Product/100", "ACTIVE"), product("gid://shopify/Product/200", "DRAFT")],
])
def test_duplicate_choice_is_stable_when_canonical_product_becomes_draft(products):
    result = _shopify_products(FakeClient(products), "Kentie", "kentie")
    assert result["920190"]["product"]["id"].endswith("/200")


def test_family_product_wins_same_status_duplicate_sku():
    result = _shopify_products(
        FakeClient([
            product("gid://shopify/Product/100", "ACTIVE"),
            product("gid://shopify/Product/200", "ACTIVE", variant_count=3),
        ]),
        "Kentie", "kentie",
    )
    assert result["920190"]["product"]["id"].endswith("/200")


def test_newer_product_id_breaks_equal_status_and_size_tie():
    result = _shopify_products(
        FakeClient([
            product("gid://shopify/Product/100", "ACTIVE"),
            product("gid://shopify/Product/200", "ACTIVE"),
        ]),
        "Kentie", "kentie",
    )
    assert result["920190"]["product"]["id"].endswith("/200")


def test_product_inventory_query_stays_below_shopify_cost_limit():
    client = FakeClient([])

    _shopify_products(client, "Kentie", "kentie")

    assert "products(first:20" in client.queries[0]
    assert "products(first:50" not in client.queries[0]
    assert "products(first:100" not in client.queries[0]
