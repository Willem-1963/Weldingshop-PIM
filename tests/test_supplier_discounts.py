import sqlite3

from app.suppliers import discounts


def test_lookup_discount_product_includes_unavailable_current_sku(tmp_path, monkeypatch):
    database = tmp_path / "supplier.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE products(
               sku TEXT, source_title TEXT, product_type TEXT, category TEXT,
               available INTEGER, source_present INTEGER
           )"""
    )
    connection.execute(
        "INSERT INTO products VALUES(?,?,?,?,?,?)",
        ("7812873", "Elektromagnetische klep VZCT 6,5FS", "Elektrozawory", "Elektrozawory", 0, 1),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(discounts, "init_supplier_database", lambda _slug: database)

    product = discounts.lookup_discount_product("tecweld", "7812873")

    assert product is not None
    assert product["source_title"] == "Elektromagnetische klep VZCT 6,5FS"
    assert product["available"] == 0
    options = discounts.discount_product_options("tecweld")
    assert options["7812873"]["source_title"] == "Elektromagnetische klep VZCT 6,5FS"


def test_delete_discount_rule_removes_only_selected_rule(tmp_path, monkeypatch):
    database = tmp_path / "supplier.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE purchase_discount_rules(
               id INTEGER PRIMARY KEY, name TEXT, match_field TEXT,
               match_value TEXT, discount_percent REAL, basis_field TEXT,
               priority INTEGER, enabled INTEGER, created_at TEXT, updated_at TEXT
           )"""
    )
    connection.execute(
        """CREATE TABLE purchase_discount_runs(
               id INTEGER PRIMARY KEY, applied_at TEXT,
               products_updated INTEGER, message TEXT
           )"""
    )
    connection.executemany(
        "INSERT INTO purchase_discount_rules VALUES(?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "verkeerd", "sku", "7812873", 10, "price", 0, 1, "now", "now"),
            (2, "behouden", "all", "", 5, "price", 0, 1, "now", "now"),
        ],
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(discounts, "init_supplier_database", lambda _slug: database)

    discounts.delete_discount_rule("tecweld", 1)

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT id FROM purchase_discount_rules ORDER BY id"
    ).fetchall() == [(2,)]
    connection.close()


def test_scoped_sales_rule_prefers_specific_sku(tmp_path, monkeypatch):
    database = tmp_path / "supplier.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE products(
               sku TEXT PRIMARY KEY,source_title TEXT,product_type TEXT,
               category TEXT,category_full TEXT,price REAL,cost_price REAL,
               sale_price REAL,gross_purchase_price_per_kg REAL,
               kg_per_sales_unit REAL,source_present INTEGER,updated_at TEXT
           )"""
    )
    connection.executemany(
        "INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("7812873", "Klep", "Kleppen", "Gas", "Gas > Kleppen", 100, 50, None, None, None, 1, "now"),
            ("2", "Ander", "Overig", "Gas", "Gas > Overig", 100, 50, None, None, None, 1, "now"),
        ],
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(discounts, "init_supplier_database", lambda _slug: database)
    monkeypatch.setattr(discounts, "get_supplier", lambda _slug: {"request_options": {}})
    discounts.save_scoped_sales_price_rule(
        "tecweld", name="algemeen", match_field="all", match_value="",
        rule_type="markup_on_cost", rule_value=10,
    )
    sku_rule_id = discounts.save_scoped_sales_price_rule(
        "tecweld", name="klep", match_field="sku", match_value="7812873",
        rule_type="fixed_markup", rule_value=20,
    )

    rows = {row["SKU"]: row for row in discounts.preview_sales_prices("tecweld", limit=None)}

    assert rows["7812873"]["Berekende verkoopprijs"] == 75
    assert rows["7812873"]["Verkoopprijsregel"] == "algemeen + klep"
    assert rows["2"]["Verkoopprijsregel"] == "algemeen"
    assert rows["2"]["Berekende verkoopprijs"] == 55
    assert "Legeringstoeslag" not in rows["7812873"]

    discounts.update_sales_price_rule(
        "tecweld", sku_rule_id, name="klep gewijzigd", match_field="sku",
        match_value="7812873", rule_type="fixed_markup", rule_value=25,
        priority=10,
    )
    changed = {
        row["SKU"]: row for row in discounts.preview_sales_prices("tecweld", limit=None)
    }
    assert changed["7812873"]["Verkoopprijsregel"] == "algemeen + klep gewijzigd"
    assert changed["7812873"]["Berekende verkoopprijs"] == 80


def test_none_sales_rule_uses_regular_supplier_price_as_fallback(
    tmp_path, monkeypatch,
):
    database = tmp_path / "supplier.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE products(
               sku TEXT PRIMARY KEY,source_title TEXT,product_type TEXT,
               category TEXT,category_full TEXT,price REAL,cost_price REAL,
               sale_price REAL,gross_purchase_price_per_kg REAL,
               kg_per_sales_unit REAL,source_present INTEGER,updated_at TEXT
           )"""
    )
    connection.execute(
        "INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("1514126-KIT", "Kemper set", "Kemper", "Propaan", "Propaan",
         102.55, 61.53, None, None, None, 1, "now"),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(discounts, "init_supplier_database", lambda _slug: database)
    monkeypatch.setattr(
        discounts, "get_supplier",
        lambda _slug: {"request_options": {
            "sales_price_rule_type": "none",
            "sales_max_discount_percent": 20,
        }},
    )

    row = discounts.preview_sales_prices("kentie", limit=None)[0]

    assert row["Berekende verkoopprijs"] == 102.55


def test_markup_on_cost_does_not_require_a_gross_supplier_price(
    tmp_path, monkeypatch,
):
    database = tmp_path / "supplier-without-gross-price.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE products(
               sku TEXT PRIMARY KEY,source_title TEXT,product_type TEXT,
               category TEXT,category_full TEXT,price REAL,cost_price REAL,
               sale_price REAL,gross_purchase_price_per_kg REAL,
               kg_per_sales_unit REAL,source_present INTEGER,updated_at TEXT
           )"""
    )
    connection.execute(
        "INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("HARDER-1", "Harder artikel", "", "", "", None, 10, 51,
         None, None, 1, "now"),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(discounts, "init_supplier_database", lambda _slug: database)
    monkeypatch.setattr(discounts, "get_supplier", lambda _slug: {"request_options": {}})
    discounts.save_scoped_sales_price_rule(
        "harder-lastechniek", name="41 procent", match_field="all",
        match_value="", rule_type="markup_on_cost", rule_value=41,
    )

    row = discounts.preview_sales_prices("harder-lastechniek", limit=None)[0]

    assert row["Berekende verkoopprijs"] == 14.1
