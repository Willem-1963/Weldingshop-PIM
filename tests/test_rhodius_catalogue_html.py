from app.suppliers.rhodius_catalogue_enrichment import _render_product_html
from app.suppliers.quality.base import DEFAULT_POLICY


def test_continuation_table_uses_exact_variant_facts():
    product = {'sku': '300565', 'raw_data': {
        'Productnaam': 'SPBR', 'Productsoort': 'Rol schuurlinnen',
        'Afmetingen': '40mmx50m', 'Korrel / Draad Ø': 40,
        'gtin_piece': '4011890023365',
    }}
    rendered = _render_product_html({'title': 'SPBR'}, product)
    assert DEFAULT_POLICY.description_is_complete(rendered)
    for value in ('300565', '40mmx50m', '4011890023365', 'rol schuurlinnen'):
        assert value in rendered
    assert 'None' not in rendered
    assert 'Geschikte materialen' not in rendered


def test_catalogue_description_is_retained_and_source_values_escaped():
    rendered = _render_product_html(
        {'title': 'FS', 'description': 'Lamellenslijper met 6 mm as.'},
        {'sku': '305143', 'raw_data': {'Afmetingen': '<50&30>', 'Vorm': None}},
    )
    assert 'Lamellenslijper met 6 mm as.' in rendered
    assert '&lt;50&amp;30&gt;' in rendered
    assert '<dt>Vorm</dt>' not in rendered
