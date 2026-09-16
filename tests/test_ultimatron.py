import copy
import json
from types import SimpleNamespace

import pytest
from app.suppliers import ultimatron as u

URL = 'https://ultimatron-france.fr/produit/test/'
PAGE = '''<div class="summary"><h1 class="product_title">Batterie 12V</h1>SKU: ULM-12-200</div>
<div id="tab-description"><p>Description batterie 12V.</p><table>
<tr><td>Tension de reconnexion</td><td>12V</td></tr>
<tr><td>Tension de reconnexion</td><td>13V</td></tr></table></div>
<div id="tab-additional_information"><table><tr><td>GTIN</td><td>1234567890123</td></tr></table></div>
<div class="woocommerce-product-gallery"><figure data-src="/wp-content/uploads/photo.webp"><img src="/wp-content/uploads/photo.webp"></figure></div>
<div class="related"><img src="/wp-content/uploads/wrong.webp">SKU: ULM-12-200H</div>'''


def test_exact_identity_gallery_and_duplicate_specification_labels():
    result = u.parse_product(PAGE, 'ULM-12-200', URL)
    assert len(result['image_urls']) == 1
    assert 'wrong' not in result['image_urls'][0]
    assert list(result['technical_specifications'].values()) == ['12V', '13V', '1234567890123']
    assert result['ean'] == '1234567890123'
    with pytest.raises(ValueError, match='niet exact'):
        u.parse_product(PAGE, 'ULM-12-200H', URL)


def test_category_deduplicates_sku_and_rejects_external_links():
    card = '<li class="product"><a data-product_sku="A"></a><h2 class="woocommerce-loop-product__title"><a href="{url}">X</a></h2></li>'
    assert len(u.parse_category(card.format(url=URL) * 2)) == 1
    with pytest.raises(ValueError):
        u.parse_category(card.format(url='https://other.example/produit/a'))


def test_translation_rejects_lost_technical_values():
    source = u.parse_product(PAGE, 'ULM-12-200', URL)
    bad = copy.deepcopy(source)
    bad['technical_specifications']['Tension de reconnexion'] = '99V'
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: SimpleNamespace(output_text=json.dumps(bad))))
    client.with_options = lambda **kw: client
    provider = SimpleNamespace(client=client, model='test')
    with pytest.raises(ValueError, match='vertaling afgekeurd'):
        u.translate(source, provider, {'translation': {}})


def test_import_preserves_prices_stock_and_manual_content(monkeypatch, tmp_path):
    from app.suppliers import hub
    from app.suppliers.enrichment_profiles import default_enrichment_profile
    monkeypatch.setattr(hub, 'SUPPLIER_DIR', tmp_path)
    profile = default_enrichment_profile('ultimatron')
    profile['translation']['enabled'] = True
    monkeypatch.setattr(u, 'get_enrichment_profile', lambda slug: profile)
    monkeypatch.setattr(u, 'fetch', lambda url: PAGE)
    monkeypatch.setattr(u, 'translate', lambda source, provider, profile: {
        'title': 'Lithiumaccu 12V', 'description': 'Nederlandse accubeschrijving 12V.',
        'technical_specifications': {'Spanning': '12V'}})
    result = u.import_product('ULM-12-200', provider=object(), product_url=URL)
    assert result['created'] and result['images'] == 1
    with hub._connect(result['database']) as conn:
        conn.execute("UPDATE products SET price=199,cost_price=100,stock_quantity=8 WHERE sku='ULM-12-200'")
    u.import_product('ULM-12-200', provider=object(), product_url=URL)
    with hub._connect(result['database']) as conn:
        row = conn.execute('SELECT price,cost_price,stock_quantity,shopify_status FROM products').fetchone()
        assert tuple(row) == (199,100,8,'draft')
        conn.execute('UPDATE products SET content_locked=1')
    with pytest.raises(ValueError, match='vergrendelde'):
        u.import_product('ULM-12-200', provider=object(), product_url=URL)


def test_translation_restores_protected_numbers_without_changing_notation():
    source = {'title': 'Batterie 12.8V', 'description': 'Une batterie de 12.8V avec 5 ans de garantie.',
              'technical_specifications': {'Tension': '12.8V'}}
    def create(**kwargs):
        payload = json.loads(kwargs['input'].split('\n', 1)[1])
        payload['title'] = payload['title'].replace('Batterie', 'Accu')
        payload['description'] = payload['description'].replace('Une batterie de', 'Een accu van').replace('avec', 'met').replace('ans de garantie', 'jaar garantie')
        payload['technical_specifications'] = {'Spanning': payload['technical_specifications']['Tension']}
        return SimpleNamespace(output_text=json.dumps(payload))
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    client.with_options = lambda **kwargs: client
    result = u.translate(source, SimpleNamespace(client=client, model='test'), {'translation': {}})
    assert result['title'] == 'Accu 12.8V'
    assert result['technical_specifications'] == {'Spanning': '12.8V'}
    assert '5 jaar' in result['description']


def test_catalogue_reports_failures_and_continues(monkeypatch, tmp_path):
    monkeypatch.setattr(u, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(u, 'discover', lambda: {'A': URL, 'B': URL})
    def import_product(sku, **kwargs):
        if sku == 'A':
            raise ValueError('Vertaling afgekeurd')
        return {'sku': sku}
    monkeypatch.setattr(u, 'import_product', import_product)
    u.run_catalogue()
    status = u.job_status()
    assert (status['completed'], status['failed'], status['total']) == (1, 1, 2)
    assert status['status'] == 'completed_with_errors'
    assert status['errors'][0]['sku'] == 'A'
