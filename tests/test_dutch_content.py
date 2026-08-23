import sqlite3

import pytest

from app.suppliers.dutch_content import (
    assert_dutch_product_content,
    init_content_localization_tables,
    polish_score,
    record_product_localization,
    validate_complete_product_translation,
    weldingshop_product_html,
    weldingshop_document_html,
)
from app.suppliers.tecweld_dutch_chain import _needs_product_translation


def test_polish_customer_text_is_blocked():
    with pytest.raises(ValueError, match="Poolse tekstsignalen"):
        assert_dutch_product_content({
            "title": "Uchwyt spawalniczy",
            "description": "Produkt jest przeznaczony do spawania oraz jest wyposażony.",
            "technical_specifications": {"Napięcie zasilania": "230 V"},
        })


def test_polish_product_group_is_blocked():
    with pytest.raises(ValueError, match="Poolse tekstsignalen"):
        assert_dutch_product_content({
            "title": "Sherman DIGITIG 200",
            "product_group_name": "Urządzenia do spawania",
            "description": "Professionele TIG-lasapparatuur.",
            "technical_specifications": {},
        })


def test_english_content_still_requires_localization_status():
    data = {
        "title": "Professional welding machine",
        "product_group_name": "Welding machines",
        "description": "Professional equipment for workshop use.",
        "technical_specifications": {},
    }
    assert _needs_product_translation(None, None, data) is True
    assert _needs_product_translation("passed", 1, data) is True
    assert _needs_product_translation("passed", 2, data) is True
    dutch_data = {
        **data,
        "title": "Professioneel lasapparaat",
        "product_group_name": "Lasapparaten",
        "description": "Professionele apparatuur voor gebruik in de werkplaats.",
    }
    assert _needs_product_translation("passed", 2, dutch_data) is False


def test_strict_translation_rejects_missing_technical_value():
    source = {
        "title": "Pressure regulator PRO-SET 20 bar",
        "product_group_name": "Pressure regulators",
        "description": "Professional regulator for 230 V workshop equipment.",
        "technical_specifications": {"Maximum pressure": "20 bar"},
        "accessories": [],
        "feature_icons": [],
    }
    translated = {
        "title": "Drukregelaar PRO-SET",
        "product_group_name": "Drukregelaars",
        "description": "Professionele regelaar voor werkplaatsapparatuur.",
        "technical_specifications": {"Maximale druk": ""},
        "accessories": [],
        "feature_icons": [],
    }
    with pytest.raises(ValueError, match="technische waarden/codes ontbreken"):
        validate_complete_product_translation(source, translated)


def test_strict_translation_accepts_complete_dutch_result():
    source = {
        "title": "Pressure regulator PRO-SET 20 bar",
        "product_group_name": "Pressure regulators",
        "description": "Professional regulator for workshop equipment.",
        "technical_specifications": {"Maximum pressure": "20 bar"},
        "accessories": ["Handle"],
        "feature_icons": [],
    }
    translated = {
        "title": "Drukregelaar PRO-SET 20 bar",
        "product_group_name": "Drukregelaars",
        "description": "Professionele regelaar voor gebruik in de werkplaats.",
        "technical_specifications": {"Maximale druk": "20 bar"},
        "accessories": ["Handgreep"],
        "feature_icons": [],
    }
    validate_complete_product_translation(source, translated)


def test_dutch_content_passes_and_has_weldingshop_attribution():
    data = {
        "title": "Sherman MIG/MAG-lastoorts",
        "description": "Deze lastoorts is bedoeld voor handmatig MIG/MAG-lassen.",
        "technical_specifications": {"Nominale lengte": "4 m"},
    }
    assert_dutch_product_content(data)
    rendered = weldingshop_product_html(
        data["title"], data["description"], data["technical_specifications"]
    )
    assert "lang='nl-NL'" in rendered
    assert "Weldingshop.nl verzorgt de Nederlandse productinformatie" in rendered
    assert polish_score(rendered) == 0


def test_localization_audit_is_supplier_database_local():
    connection = sqlite3.connect(":memory:")
    init_content_localization_tables(connection)
    record_product_localization(
        connection, sku="7812078", title="Sherman lastoorts",
        description="Nederlandse technische productomschrijving.",
        specifications={"Lengte": "4 m"}, now="2026-08-13T00:00:00+00:00",
    )
    row = connection.execute(
        "SELECT language,status,profile_key FROM product_content_localization WHERE sku=?",
        ("7812078",),
    ).fetchone()
    assert row == ("nl-NL", "passed", "tecweld-nl-weldingshop")


def test_document_template_is_dutch_and_attributed():
    rendered = weldingshop_document_html(
        title="Sherman Nederlandse handleiding",
        source_url="https://tecweld.pl/manual.pdf",
        source_revision="abc123",
        sections=[{"heading": "Veiligheid", "content": "Draag geschikte bescherming."}],
    )
    assert "lang='nl-NL'" in rendered
    assert "Nederlandse Weldingshop-uitgave" in rendered
    assert "Tecweld/Sherman is de fabrikant" in rendered
