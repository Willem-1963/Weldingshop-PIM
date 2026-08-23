from app.suppliers.routes import supplier_route


def test_only_tecweld_uses_dutch_customer_content_route():
    assert supplier_route("tecweld").localizes_customer_content_to_dutch is True
    for slug in ("certilas", "edge", "sp-tools", "valkenpower", "kentie"):
        assert supplier_route(slug).localizes_customer_content_to_dutch is False
