from app.shopify.sync import (
    _certilas_bundle_metafields,
    _certilas_mandatory_bundle_quantity,
    _certilas_stale_bundle_metafield_identifiers,
    _price_only_variant_rows,
    _selling_price,
)


def certilas_product(unit="2,4", bundle="14,4", sale_price=250.69):
    return {
        "_supplier_slug": "certilas",
        "sale_price": sale_price,
        "_raw_data": {
            "KG Ceweld Unit": unit,
            "KG Ceweld Bundle": bundle,
        },
    }


def test_certilas_whole_bundle_uses_complete_bundle_price():
    product = certilas_product()

    assert _certilas_mandatory_bundle_quantity(product) == 6
    assert _selling_price(product) == 1504.14


def test_certilas_four_pack_bundle_is_supported():
    product = certilas_product(unit="2,0", bundle="8,0", sale_price=197)

    assert _certilas_mandatory_bundle_quantity(product) == 4
    assert _selling_price(product) == 788.0


def test_ambiguous_certilas_ratio_is_not_guessed():
    product = certilas_product(unit="2,0", bundle="13,2", sale_price=52.77)

    assert _certilas_mandatory_bundle_quantity(product) is None
    assert _selling_price(product) == 52.77


def test_missing_or_nan_bundle_weight_is_not_a_bundle():
    missing = certilas_product(bundle=None)
    nan = certilas_product(bundle="nan")

    assert _certilas_mandatory_bundle_quantity(missing) is None
    assert _certilas_mandatory_bundle_quantity(nan) is None


def test_other_supplier_price_is_unchanged():
    product = certilas_product()
    product["_supplier_slug"] = "tecweld"

    assert _certilas_mandatory_bundle_quantity(product) is None
    assert _selling_price(product) == 250.69


def metafields_by_key(product):
    return {item["key"]: item for item in _certilas_bundle_metafields(product)}


def test_certilas_bundle_metafields_and_notice_follow_pim_formula():
    fields = metafields_by_key(certilas_product())

    assert fields["verplichte_bundel"]["value"] == "true"
    assert fields["verpakkingen_per_bundel"]["value"] == "6"
    assert fields["kg_per_verpakking"]["value"] == "2.4"
    assert fields["kg_per_bundel"]["value"] == "14.4"
    assert fields["prijs_per_verpakking"]["value"] == "250.69"
    assert fields["minimale_afname"]["value"] == (
        "Let op: deze prijs geldt voor één verplichte bundel van "
        "6 verpakkingen à 2,4 kg. Totaalgewicht 14,4 kg. "
        "Prijs per verpakking €250,69."
    )


def test_certilas_missing_bundle_clears_bundle_only_fields():
    fields = metafields_by_key(certilas_product(unit="3,2", bundle=None))

    assert fields["verplichte_bundel"]["value"] == "false"
    assert fields["kg_per_verpakking"]["value"] == "3.2"
    assert fields["prijs_per_verpakking"]["value"] == "250.69"
    assert "verpakkingen_per_bundel" not in fields
    assert "kg_per_bundel" not in fields
    assert "minimale_afname" not in fields


def test_price_only_rows_also_persist_certilas_bundle_fields():
    product = certilas_product()
    product["sku"] = "50620"
    existing = {
        "50620": {
            "product": {"id": "gid://shopify/Product/1"},
            "variant": {"id": "gid://shopify/ProductVariant/2"},
        }
    }

    rows = _price_only_variant_rows([product], existing)

    variant = rows[0]["variants"][0]
    assert variant["price"] == "1504.14"
    assert {item["key"] for item in variant["metafields"]} == {
        "verplichte_bundel", "verpakkingen_per_bundel",
        "kg_per_verpakking", "kg_per_bundel", "prijs_per_verpakking",
        "minimale_afname",
    }


def test_stale_bundle_and_product_notice_fields_are_deleted():
    product = certilas_product(unit="3,2", bundle=None)
    product["sku"] = "50650"
    existing = {
        "50650": {
            "product": {
                "id": "gid://shopify/Product/1",
                "metafields": {"nodes": [{"key": "minimale_afname"}]},
            },
            "variant": {
                "id": "gid://shopify/ProductVariant/2",
                "metafields": {"nodes": [
                    {"key": "verpakkingen_per_bundel"},
                    {"key": "kg_per_bundel"},
                    {"key": "minimale_afname"},
                ]},
            },
        }
    }

    identifiers = _certilas_stale_bundle_metafield_identifiers(
        [product], existing
    )

    assert {(item["ownerId"], item["key"]) for item in identifiers} == {
        ("gid://shopify/Product/1", "minimale_afname"),
        ("gid://shopify/ProductVariant/2", "verpakkingen_per_bundel"),
        ("gid://shopify/ProductVariant/2", "kg_per_bundel"),
        ("gid://shopify/ProductVariant/2", "minimale_afname"),
    }
