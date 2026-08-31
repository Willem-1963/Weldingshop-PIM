import json

from scripts.prepare_rhodius_excel_import import normalize, number, ve_number


def _row(**overrides):
    row = {
        "Artikelnummer": 211300,
        "Prijseenheid": "€/pak",
        "VE": 25,
        "Gewicht in kg/stuk": 0.017,
        "Gewicht/VE": None,
        "GTIN-code": 4011890112915,
        "GTIN/verpakking": 4011890124314,
        "Productnaam": "XTK6 EXACT PACK",
        "Afmetingen": "115x0,6x22,23",
        "Korrel / Draad Ø": None,
        "Opmerkingen": None,
        "Bruto prijs 2026": 84.17655,
        "Netto prijs": 40.404744,
        "Korting": 0.52,
        "Productsoort": "Doorslijpschijf extra dun",
        "Prijscategorie": "PK01",
        "Kwaliteitsklasse": "BRAINTOOL®",
        "Toepassing": "ll",
    }
    row.update(overrides)
    return row


def test_pack_uses_packaging_gtin_and_total_sales_weight():
    product = normalize(_row())

    assert product["ean"] == "4011890124314"
    assert product["purchase_unit"] == "pak"
    assert product["sales_unit"] == "pak"
    assert product["weight_grams"] == 425
    assert product["purchase_discount_percent"] == 52


def test_filters_are_plain_strings_for_pim_product_list():
    product = normalize(_row())
    filters = json.loads(product["filter_values_json"])

    assert filters == ["BRAINTOOL®", "115x0,6x22,23", "VE 25"]
    assert all(isinstance(value, str) for value in filters)
    assert " | ".join(filters) == "BRAINTOOL® | 115x0,6x22,23 | VE 25"


def test_price_on_request_stays_missing_instead_of_becoming_zero():
    product = normalize(_row(
        **{"Bruto prijs 2026": "op verzoek", "Netto prijs": "op verzoek"}
    ))

    assert product["price"] is None
    assert product["cost_price"] is None


def test_compound_packaging_quantity_is_normalized():
    assert ve_number("1x5") == 5
    assert ve_number("1x10") == 10
    assert number("0,850 kg") == 0.85
