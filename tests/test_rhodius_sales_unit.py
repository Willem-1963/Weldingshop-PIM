import pytest
from app.shopify.sync import _input, _price_only_variant_rows, _eligible
from app.suppliers.rhodius_sales_unit import sales_unit_rule, shopify_stock_quantity


def product(**raw):
    return {'_supplier_slug': 'rhodius-abrasives-gmbh', '_rhodius_sales_unit': 'package', 'sku': 'TEST-1',
            'source_title': 'Schijf', 'images': [], 'price': 7.865999999999999,
            'cost_price': 3.69702, 'ean': 'piece',
            '_raw_data': {'VE': 10, 'GTIN-code': 'piece', 'GTIN/verpakking': 'pack',
                          'Prijseenheid': '€/stuk', 'Gewicht in kg/stuk': 0.11, **raw}}


def test_export_uses_ve_and_rounds_after_conversion():
    p = product()
    result = _input(p, None)
    v = result['variants'][0]
    assert v['barcode'] == 'pack'
    assert v['price'] == '78.66'
    assert v['inventoryItem']['cost'] == '36.97'
    assert v['inventoryItem']['measurement']['weight']['value'] == 1.1
    assert '10 stuks' in result['title']
    assert p['price'] == 7.865999999999999
    assert shopify_stock_quantity(p, 29) == 2


def test_missing_package_gtin_clears_piece_and_flags_draft():
    result = _input(product(**{'GTIN/verpakking': None}), None)
    assert result['variants'][0]['barcode'] == ''
    assert result['status'] == 'DRAFT'
    assert 'controle_gtin_verkoopeenheid' in result['tags']


def test_one_piece_and_equal_gtins():
    p = product(**{'VE': 1, 'GTIN/verpakking': 'piece'})
    assert _input(p, None)['variants'][0]['barcode'] == 'piece'


def test_pack_price_is_not_multiplied_twice():
    p = product(**{'Prijseenheid': '€/pak'})
    assert _input(p, None)['variants'][0]['price'] == '7.87'
    assert shopify_stock_quantity(p, 29) == 29


def test_verified_pack_gtin_requires_matching_quantity():
    p = product(**{'GTIN/verpakking': None, 'website_import': {
        'verification': 'official_page', 'technical_specifications': {
            'inhoud': '10', 'gtin_code_verpakkingseenheid': 'website-pack'}}})
    assert sales_unit_rule(p)['barcode'] == 'website-pack'
    p['_raw_data']['website_import']['technical_specifications']['inhoud'] = '5'
    assert sales_unit_rule(p)['barcode'] == ''


@pytest.mark.parametrize('ve', [None, '', 0, -2, 'nan', 'inf', 1.5])
def test_invalid_ve_blocks_export(ve):
    with pytest.raises(ValueError, match='VE'):
        _input(product(VE=ve), None)


def test_compound_ve_and_price_only_guard():
    p = product(VE='1x5')
    assert sales_unit_rule(p)['quantity'] == 5
    with pytest.raises(ValueError, match='volledige synchronisatie'):
        _price_only_variant_rows([p], {})


def test_missing_gtin_blocks_real_product_and_package_weight_is_leading():
    p = product(**{'GTIN/verpakking': None})
    p['sku'] = '1'
    with pytest.raises(ValueError, match='TEST-concept'):
        _input(p, None)
    result = _input(product(**{'Gewicht/VE': '3,000 kg'}), None)
    assert result['variants'][0]['inventoryItem']['measurement']['weight']['value'] == 3


def test_default_piece_stays_scannable_without_package_gtin():
    p = product(**{'GTIN/verpakking': None})
    p.pop('_rhodius_sales_unit')
    p['sku'] = '1'
    result = _input(p, None)
    v = result['variants'][0]
    assert v['barcode'] == 'piece'
    assert v['price'] == '7.87'
    assert v['inventoryItem']['measurement']['weight']['value'] == .11
    assert shopify_stock_quantity(p, 29) == 29
    assert 'controle_gtin_verkoopeenheid' not in result['tags']
    assert '1 stuk(s)' in result['descriptionHtml']
    assert sales_unit_rule(p)['online_policy'] == 'undecided'


def test_piece_price_and_stock_from_pack_source():
    p = product(**{'Prijseenheid': '€/pak', 'VE': 3})
    p['_rhodius_sales_unit'] = 'piece'
    p['price'] = 10
    p['cost_price'] = 6
    result = _input(p, None)
    assert result['variants'][0]['price'] == '3.33'
    assert result['variants'][0]['inventoryItem']['cost'] == '2.00'
    assert shopify_stock_quantity(p, 10) == 30


def test_same_gtin_cannot_scan_as_two_different_quantities():
    result = _input(product(**{'GTIN/verpakking': 'piece'}), None)
    assert result['variants'][0]['barcode'] == ''
    assert 'controle_gtin_verkoopeenheid' in result['tags']


def test_undecided_online_policy_does_not_publish_scannable_units():
    p = product()
    p['images'] = [{'image_url': 'https://example.org/image.jpg'}]
    p['available'] = 1
    assert not _eligible(p)
