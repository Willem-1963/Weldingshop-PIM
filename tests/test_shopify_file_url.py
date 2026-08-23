from app.shopify.sync import _shopify_file_url


def test_shopify_file_url_encodes_spaces_in_supplier_filename():
    assert _shopify_file_url(
        "https://www.kentie.shop/images/1510459- M14X1.jpeg"
    ) == "https://www.kentie.shop/images/1510459-%20M14X1.jpeg"


def test_shopify_file_url_preserves_existing_encoding():
    assert _shopify_file_url(
        "https://www.kentie.shop/images/a%20b.jpeg"
    ) == "https://www.kentie.shop/images/a%20b.jpeg"
