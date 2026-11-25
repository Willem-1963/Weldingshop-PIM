import json

import pytest

from app.suppliers import hub


@pytest.mark.parametrize(
    ("unit", "gross", "net"),
    [
        ("Gr", 4050.0, 2030.0),
        ("gram", 4050.0, 2030.0),
        ("g", 4050.0, 2030.0),
        ("Kg", 4.05, 2.03),
    ],
)
def test_certilas_price_unit_is_normalized_to_eur_per_kg(unit, gross, net):
    assert hub._certilas_price_per_kg(4.05, unit) == gross
    assert hub._certilas_price_per_kg(2.03, unit) == net


def test_certilas_gram_prices_produce_correct_package_values(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(hub, "SUPPLIER_DIR", tmp_path)
    monkeypatch.setattr(hub, "IMPORT_DIR", tmp_path / "imports")
    monkeypatch.setattr(hub, "REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(
        hub,
        "get_supplier",
        lambda slug: {
            "name": "Certilas",
            "field_mapping": {},
            "request_options": {},
        },
    )
    with hub._connect(hub.REGISTRY_PATH) as connection:
        connection.execute(
            """CREATE TABLE suppliers(
                slug TEXT,last_run_at TEXT,last_run_status TEXT,
                last_run_message TEXT,updated_at TEXT
            )"""
        )
        connection.execute("INSERT INTO suppliers(slug) VALUES ('certilas')")

    record = {
        "sku": "60200",
        "title": "NiCrMo 622 0,2 mm D100",
        "price": 0.37,
        "purchase_unit": "Gr",
        "gross_per_kg": 4.05,
        "discount": 49.9,
        "net_per_kg": 2.03,
        "kg_per_unit": 0.1,
    }
    analysis = hub.SourceAnalysis(
        "json", 1, list(record), [record],
        json.dumps([record]).encode(), [record],
    )
    hub.import_records(
        "certilas",
        analysis,
        {
            "sku": "sku",
            "title": "title",
            "price": "price",
            "sale_price": "price",
            "purchase_unit": "purchase_unit",
            "gross_purchase_price_per_kg": "gross_per_kg",
            "purchase_discount_percent": "discount",
            "net_purchase_price_per_kg": "net_per_kg",
            "kg_per_purchase_unit": "kg_per_unit",
            "kg_per_sales_unit": "kg_per_unit",
        },
    )

    with hub._connect(hub.supplier_database_path("certilas")) as connection:
        product = connection.execute(
            """SELECT gross_purchase_price_per_kg,
                      net_purchase_price_per_kg,cost_price,
                      kg_per_sales_unit,purchase_unit
               FROM products WHERE sku='60200'"""
        ).fetchone()

    assert tuple(product) == (4050.0, 2030.0, 203.0, 0.1, "Gr")
