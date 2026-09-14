from app.suppliers.quality.valkenpower import POLICY


def test_locked_category_import_is_valid_without_website():
    product = {'sku': 'VP-A', '_raw_data': {'category_file_import': {
        'locked': True, 'values': {'product_group_name': 'Werkplaatsuitrusting'},
    }}}
    assert POLICY.validation_errors(product, '', []) == []


def test_explicit_manual_review_is_valid_for_exact_sku():
    product = {'sku': 'VP-A', '_raw_data': {'manual_category_review': {
        'approved': True, 'sku': 'VP-A', 'reviewed_at': '2026-09-14',
    }}}
    assert POLICY.validation_errors(product, '', []) == []
    product['sku'] = 'VP-B'
    assert POLICY.validation_errors(product, '', [])


def test_shopify_active_or_unapproved_category_does_not_count_as_review():
    product = {'sku': 'VP-A', 'shopify_status': 'active', '_raw_data': {
        'category_file_import': {'values': {'product_group_name': 'Werkplaatsuitrusting'}},
    }}
    assert POLICY.validation_errors(product, '', [])
