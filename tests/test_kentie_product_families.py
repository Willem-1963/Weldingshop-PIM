import sqlite3

from app.kentie_product_families import (
    save_kentie_product_family,
    search_kentie_family_candidates,
)


def _database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE products(
          sku TEXT PRIMARY KEY,supplier_sku TEXT,source_title TEXT,ai_title TEXT,
          ean TEXT,sale_price REAL,source_present INTEGER,raw_data_json TEXT);
        CREATE TABLE product_images(
          id INTEGER PRIMARY KEY,sku TEXT,image_url TEXT,position INTEGER,alt_text TEXT);
        CREATE TABLE product_families(
          family_key TEXT PRIMARY KEY,title TEXT,family_json TEXT,website_url TEXT,
          ai_research_allowed INTEGER DEFAULT 0,
          website_match_policy TEXT DEFAULT 'exact_sku_or_ean',calculated_at TEXT);
        CREATE TABLE product_family_variants(
          family_key TEXT,sku TEXT,variant_title TEXT,position INTEGER,
          PRIMARY KEY(family_key,sku));
        """)
        connection.executemany(
            "INSERT INTO products VALUES(?,?,?,?,?,?,1,'{}')",
            [
                ("K-1", "PR6", "Stronghand C-klem 150 mm", "", "1", 10),
                ("K-2", "PR18", "Stronghand C-klem 460 mm", "", "2", 20),
                ("OTHER-1", "X", "Ander product", "", "3", 30),
            ],
        )
        connection.execute(
            "INSERT INTO product_images VALUES(1,'K-1','https://kentie.test/1.jpg',1,'Klem')"
        )


def test_searches_only_selected_database_by_skus_or_shared_title(tmp_path):
    database = tmp_path / "kentie.sqlite"
    _database(database)
    assert [row["sku"] for row in search_kentie_family_candidates(
        "PR6, PR18", database
    )] == ["K-1", "K-2"]
    assert [row["sku"] for row in search_kentie_family_candidates(
        "Stronghand C-klem", database
    )] == ["K-1", "K-2"]


def test_saves_only_explicitly_selected_kentie_variants(tmp_path):
    database = tmp_path / "kentie.sqlite"
    _database(database)
    family = save_kentie_product_family(["K-1", "K-2"], "Klemmen", database)
    assert [item["sku"] for item in family["variants"]] == ["K-1", "K-2"]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM product_family_variants"
        ).fetchone()[0] == 2
