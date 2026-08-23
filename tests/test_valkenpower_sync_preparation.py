import json
import sqlite3

from app.shopify.sync import _common_product_content_errors
from app.suppliers.quality import quality_policy_for
from app.suppliers import valkenpower_sync_preparation as preparation


def create_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE products(
               sku TEXT PRIMARY KEY,supplier_sku TEXT,source_present INTEGER,
               vendor TEXT,brand TEXT,source_title TEXT,source_description TEXT,
               product_type TEXT,category TEXT,category_full TEXT,
               product_group_name TEXT,filter_values_json TEXT,execution TEXT,
               subcategory_3 TEXT,subcategory_4 TEXT,subcategory_5 TEXT,
               html_description TEXT,ai_tags_json TEXT,raw_data_json TEXT,
               updated_at TEXT)"""
        )
        connection.execute(
            """CREATE TABLE official_category_evidence(
               supplier_sku TEXT PRIMARY KEY,status TEXT,hierarchy_json TEXT,
               source_url TEXT,official_product_code TEXT,matched_by TEXT,
               verified_at TEXT)"""
        )
        connection.execute(
            """INSERT INTO products VALUES(
               'VP-TBP03L','TBP03L',1,'Valkenpower','Valkenpower',
               'Fluxon 3-delige verrijdbare gereedschapskistenset',?,
               '','','','Werkplaatsuitrusting','["Werkplaatsinrichting"]',
               'Gereedschapskisten','','','','','["Valkenpower"]','{}','')""",
            ("Uitgebreide officiële productomschrijving. " * 12,),
        )
        connection.execute(
            """INSERT INTO official_category_evidence VALUES(
               'TBP03L','confirmed',
               '["Werkplaatsuitrusting","Werkplaatsinrichting","Gereedschapskisten"]',
               'https://www.valkenpower.com/product','TBP03L',
               'exact_supplier_sku','2026-08-17T18:58:37+00:00')"""
        )


def test_confirmed_breadcrumb_prepares_description_and_tags(tmp_path, monkeypatch):
    database = tmp_path / "valkenpower.sqlite"
    create_database(database)
    monkeypatch.setattr(preparation, "init_supplier_database", lambda slug: database)

    result = preparation.prepare_valkenpower_products_for_sync()

    with sqlite3.connect(database) as connection:
        description, tags_json, raw_json = connection.execute(
            """SELECT html_description,ai_tags_json,raw_data_json
                 FROM products WHERE sku='VP-TBP03L'"""
        ).fetchone()
    tags = json.loads(tags_json)
    assert result["ready"] == 1
    assert result["prepared"] == 1
    assert len({tag.casefold() for tag in tags}) >= 3
    assert "Gereedschapskisten" in tags
    assert len(description) >= 300
    assert json.loads(raw_json)["valkenpower_category_evidence"]["hierarchy"] == [
        "Werkplaatsuitrusting", "Werkplaatsinrichting", "Gereedschapskisten"
    ]


def test_valkenpower_incomplete_product_is_isolated_until_breadcrumb_exists():
    policy = quality_policy_for("Valkenpower", supplier_slug="valkenpower")
    product = {
        "sku": "VP-NEW",
        "vendor": "Valkenpower",
        "_supplier_slug": "valkenpower",
        "_raw_data": {},
        "price": 10,
        "images": [{"image_url": "https://example.test/product.jpg"}],
        "source_title": "Nieuw product",
        "source_description": "Officiële productinformatie. " * 20,
        "ai_tags_json": '["Valkenpower","Gereedschap","Nieuw"]',
        "filter_values_json": '["Gereedschap"]',
        "product_group_name": "Werkplaatsuitrusting",
    }

    assert policy.isolate_incomplete_content is True
    assert "officiële Valkenpower-breadcrumb ontbreekt" in (
        _common_product_content_errors(product)
    )
