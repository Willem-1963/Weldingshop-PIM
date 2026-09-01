from app.suppliers.hub import _compose_source_fields


def test_collection_allows_intentionally_empty_labels_and_units():
    collection = {
        "format": "text",
        "separator": " · ",
        "items": [
            {"field": "Productsoort", "label": "", "unit": ""},
            {"field": "Productnaam", "label": "", "unit": ""},
            {"field": "Afmetingen", "label": "", "unit": ""},
        ],
    }
    record = {
        "Productsoort": "Doorslijpschijf voor stationaire machines",
        "Productnaam": "ST21",
        "Afmetingen": "400x4,0x32,00",
    }

    assert _compose_source_fields(collection, record) == (
        "Doorslijpschijf voor stationaire machines · ST21 · 400x4,0x32,00"
    )


def test_collection_keeps_legacy_field_name_when_label_key_is_absent():
    collection = {
        "format": "text",
        "items": [{"field": "Productnaam", "unit": ""}],
    }

    assert _compose_source_fields(collection, {"Productnaam": "ST21"}) == (
        "Productnaam: ST21"
    )
