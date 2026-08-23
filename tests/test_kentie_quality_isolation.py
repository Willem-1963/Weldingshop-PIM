from app.shopify.sync import (
    _common_product_content_errors,
    _set_product_delivery_notice,
)
from app.suppliers.quality import quality_policy_for


def product(images):
    return {
        "sku": "K-1",
        "vendor": "Kentie",
        "_supplier_slug": "kentie",
        "price": 10,
        "images": images,
        "html_description": "<p>" + ("Betrouwbare productinformatie. " * 20) + "</p>",
        "ai_tags_json": '["Kentie","Propaan","Reduceerventiel"]',
        "filter_values_json": '["Propaan","Reduceerventiel"]',
        "product_group_name": "Reduceerventiel",
    }


def test_kentie_accepts_one_approved_image():
    policy = quality_policy_for("Kentie", supplier_slug="kentie")
    assert policy.image_minimum == 1
    assert policy.tag_minimum == 2
    assert policy.description_minimum == 80
    assert policy.isolate_incomplete_content is True
    assert _common_product_content_errors(product([{"image_url": "https://example.test/a.jpg"}])) == []


def test_kentie_accepts_two_unique_tags_case_insensitively():
    policy = quality_policy_for("Kentie", supplier_slug="kentie")
    assert policy.tags_are_complete(["Kentie", "Manometer"])
    assert not policy.tags_are_complete(["manometer", "Manometer"])


def test_delivery_notice_uses_custom_levertijd():
    result = _set_product_delivery_notice(
        [{
            "namespace": "custom", "key": "productgroep",
            "type": "single_line_text_field", "value": "Manometer",
        }],
        "Let op: levertijd kan afwijken.",
    )
    by_key = {(item["namespace"], item["key"]): item for item in result}
    assert by_key[("custom", "verwachte_product_levertijd")] == {
        "namespace": "custom",
        "key": "verwachte_product_levertijd",
        "type": "single_line_text_field",
        "value": "Let op: levertijd kan afwijken.",
    }
    assert ("custom", "productgroep") in by_key


def test_kentie_without_image_is_incomplete():
    assert "goedgekeurde productafbeelding" in " ".join(
        _common_product_content_errors(product([]))
    )


def test_supplier_specific_contracts_do_not_leak_into_kentie():
    kentie = quality_policy_for("Kentie", supplier_slug="kentie")
    certilas = quality_policy_for("Certilas", supplier_slug="certilas")
    incomplete_filters = {"filter_values_json": "[]", "_raw_data": {}}
    assert kentie.validation_errors(incomplete_filters, "<p>tekst</p>", []) == []
    assert certilas.validation_errors(incomplete_filters, "<p>tekst</p>", [])


def test_kentie_description_length_is_stable_after_html_entity_decoding():
    policy = quality_policy_for("Kentie", supplier_slug="kentie")
    pim_html = "<p>" + ("x" * 270) + "&quot;</p>"
    shopify_html = "<p>" + ("x" * 270) + '\"</p>'
    assert policy.description_is_complete(pim_html) == policy.description_is_complete(
        shopify_html
    )


def test_kentie_placeholder_title_is_rejected():
    policy = quality_policy_for("Kentie", supplier_slug="kentie")
    errors = policy.validation_errors(
        {"ai_title": "Onbevestigd", "source_title": "Onbevestigd"},
        "<p>tekst</p>", [],
    )
    assert errors == ["placeholdertitel is niet toegestaan"]


def test_kentie_internal_unconfirmed_match_text_is_rejected():
    policy = quality_policy_for("Kentie", supplier_slug="kentie")
    description = (
        "<p>Ik kan de gevraagde exacte match niet bevestigen op basis van de "
        "beschikbare officiële Kentie-bronnen.</p>" + ("x" * 400)
    )
    assert policy.description_is_complete(description) is False
    assert policy.validation_errors(
        {"ai_title": "Snijvoorstuk KS 20 ring hevel"}, description, [],
    ) == ["interne of onbevestigde bronmatchtekst is niet toegestaan"]
