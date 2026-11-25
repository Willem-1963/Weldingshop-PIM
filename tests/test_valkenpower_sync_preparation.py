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


def test_putkrik_gets_durable_new_taxonomy_without_breadcrumb(tmp_path, monkeypatch):
    database = tmp_path / "valkenpower.sqlite"
    create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO products VALUES(
               'VP-PK20LMEK','PK20LMEK',1,'Valkenpower','Falco',
               ?,?,
               '','','','','[]','','','','','','[\"Valkenpower\"]','{}','')""",
            (
                "Falco accu putkrik 20 ton medium inclusief accu's",
                "Officiële productomschrijving. " * 20,
            ),
        )
    monkeypatch.setattr(preparation, "init_supplier_database", lambda slug: database)

    result = preparation.prepare_valkenpower_products_for_sync()

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute(
            "SELECT * FROM products WHERE sku='VP-PK20LMEK'"
        ).fetchone())
        values = json.loads(connection.execute(
            "SELECT values_json FROM category_import_locks WHERE sku='VP-PK20LMEK'"
        ).fetchone()[0])
    assert result["automatic_category_locks"] == 1
    assert row["product_group_name"] == "Werkplaatsuitrusting"
    assert row["execution"] == "Putkrikken"
    assert json.loads(row["filter_values_json"]) == [
        "Brugkrikken en putkrikken"
    ]
    assert values["productsoort"] == "Brugkrikken en putkrikken"
    assert "cat-hefbruggen" in values["collection_tags_json"]
    assert "ws-tax-hefbruggen" in values["collection_tags_json"]
    assert json.loads(row["raw_data_json"])["category_file_import"]["locked"] is True


def test_knee_protection_gets_approved_pbm_taxonomy(tmp_path, monkeypatch):
    database = tmp_path / "valkenpower.sqlite"
    create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO products VALUES(
               'VP-KP012G','KP012G',1,'Valkenpower','Bullram',
               ?,?,
               '','','','','[]','','','','','','[\"Valkenpower\"]','{}','')""",
            (
                "Bullram kniebeschermers met gelkussens",
                "Officiële productomschrijving. " * 20,
            ),
        )
        connection.execute(
            """INSERT INTO official_category_evidence VALUES(
               'KP012G','confirmed',
               '["Werkplaatsuitrusting","Monteurskrukken en bedden"]',
               'https://www.valkenpower.com/product','KP012G',
               'exact_supplier_sku','2026-10-09T00:00:00+00:00')"""
        )
    monkeypatch.setattr(preparation, "init_supplier_database", lambda slug: database)

    result = preparation.prepare_valkenpower_products_for_sync()

    with sqlite3.connect(database) as connection:
        values, provenance = connection.execute(
            """SELECT values_json,provenance_json FROM category_import_locks
                 WHERE sku='VP-KP012G'"""
        ).fetchone()
    values = json.loads(values)
    assert result["automatic_rule_locks"] == 1
    assert values["product_group_name"] == "PBM"
    assert values["execution"] == "Kniebescherming en kniestukken"
    assert values["productsoort"] == "Kniebescherming"
    assert "cat-pbm-voetbescherming-kniebescherming" in values[
        "collection_tags_json"
    ]
    assert json.loads(provenance)["method"] == (
        "valkenpower-knee-protection-title-rule-v1"
    )


def test_exact_official_hierarchy_inherits_one_unanimous_new_route(
    tmp_path, monkeypatch,
):
    database = tmp_path / "valkenpower.sqlite"
    create_database(database)
    inherited_values = {
        "product_group_name": "Werkplaatsinrichting",
        "filter_values_json": ["Gereedschapskisten"],
        "execution": "Gereedschapskisten",
        "subcategory_3": "",
        "subcategory_4": "",
        "subcategory_5": "",
        "productsoort": "Gereedschapskist",
        "collection_tags_json": [
            "cat-gereedschap", "cat-werkplaatsinrichting",
            "filter-productsoort-gereedschapskist",
        ],
    }
    with sqlite3.connect(database) as connection:
        preparation.install_category_locks(connection, [{
            "sku": "VP-TBP03L",
            "provenance": {"method": "approved-test", "values": inherited_values},
        }])
        connection.execute(
            """INSERT INTO products VALUES(
               'VP-TBP04L','TBP04L',1,'Valkenpower','Fluxon',
               ?,?,'','','','','[]','','','','','','[\"Valkenpower\"]','{}','')""",
            ("Fluxon gereedschapskist", "Officiële productomschrijving. " * 20),
        )
        connection.execute(
            """INSERT INTO official_category_evidence VALUES(
               'TBP04L','confirmed',
               '["Werkplaatsuitrusting","Werkplaatsinrichting","Gereedschapskisten"]',
               'https://www.valkenpower.com/product','TBP04L',
               'exact_supplier_sku','2026-10-09T00:00:00+00:00')"""
        )
    monkeypatch.setattr(preparation, "init_supplier_database", lambda slug: database)

    result = preparation.prepare_valkenpower_products_for_sync()

    with sqlite3.connect(database) as connection:
        values, provenance = connection.execute(
            """SELECT values_json,provenance_json FROM category_import_locks
                 WHERE sku='VP-TBP04L'"""
        ).fetchone()
    assert result["automatic_inherited_locks"] == 1
    assert json.loads(values) == inherited_values
    assert json.loads(provenance)["template_sku"] == "VP-TBP03L"


def test_unknown_category_is_queued_instead_of_guessed(tmp_path, monkeypatch):
    database = tmp_path / "valkenpower.sqlite"
    create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO products VALUES(
               'VP-UNKNOWN','UNKNOWN',1,'Valkenpower','Valkenpower',
               ?,?,'','','','','[]','','','','','','[\"Valkenpower\"]','{}','')""",
            ("Volledig nieuw product", "Officiële productomschrijving. " * 20),
        )
        connection.execute(
            """INSERT INTO official_category_evidence VALUES(
               'UNKNOWN','confirmed','["Nieuwe hoofdgroep","Nieuwe categorie"]',
               'https://www.valkenpower.com/product','UNKNOWN',
               'exact_supplier_sku','2026-10-09T00:00:00+00:00')"""
        )
    monkeypatch.setattr(preparation, "init_supplier_database", lambda slug: database)

    result = preparation.prepare_valkenpower_products_for_sync()

    with sqlite3.connect(database) as connection:
        queued = connection.execute(
            """SELECT reason,status FROM valkenpower_category_review_queue
                 WHERE sku='VP-UNKNOWN'"""
        ).fetchone()
        lock_table = connection.execute(
            """SELECT 1 FROM sqlite_master
                 WHERE type='table' AND name='category_import_locks'"""
        ).fetchone()
        locked = (
            connection.execute(
                "SELECT 1 FROM category_import_locks WHERE sku='VP-UNKNOWN'"
            ).fetchone()
            if lock_table else None
        )
    assert result["category_review_pending"] >= 1
    assert queued == ("no_unanimous_approved_new_taxonomy_template", "pending")
    assert locked is None
