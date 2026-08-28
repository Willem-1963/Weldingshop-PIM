import json
import sqlite3

from app.suppliers import hub


def test_productmaker_values_are_saved_with_persistent_overrides(monkeypatch, tmp_path):
    database = tmp_path / "supplier.sqlite"
    with sqlite3.connect(database) as db:
        db.executescript(
            """CREATE TABLE products(
              sku TEXT PRIMARY KEY,ean TEXT,vendor TEXT,brand TEXT,ai_title TEXT,
              html_description TEXT,source_description TEXT,sale_price REAL,
              cost_price REAL,purchase_unit TEXT,sales_unit TEXT,
              purchase_units_per_sales_unit REAL,stock_quantity INTEGER,
              available INTEGER,product_type TEXT,product_group_name TEXT,
              ai_tags_json TEXT,raw_data_json TEXT,content_locked INTEGER DEFAULT 0,
              content_locked_at TEXT,updated_at TEXT, supplier_sku TEXT,
              source_title TEXT,source_present INTEGER DEFAULT 1,
              first_seen_at TEXT,last_seen_at TEXT
            );
            CREATE TABLE product_images(
              sku TEXT,image_url TEXT,position INTEGER,alt_text TEXT,
              UNIQUE(sku,image_url)
            );
            INSERT INTO products(sku,raw_data_json) VALUES('ABC-1','{}');"""
        )
    monkeypatch.setattr(hub, "init_supplier_database", lambda slug: database)

    result = hub.save_product_maker_values(
        "supplier", "ABC-1",
        {
            "ean": "8712345678901", "vendor": "Merk", "title": "Titel",
            "description_html": "<p>Omschrijving</p>",
            "short_description": "Intro", "sale_price": "20",
            "purchase_price": "10", "purchase_unit": "doos",
            "sales_unit": "stuk", "unit_factor": "2", "initial_quantity": 3,
            "product_type": "Type", "tags": ["Tag"], "seo_title": "SEO",
            "seo_description": "SEO tekst", "compare_at_price": "25",
            "category_id": "cat", "category_label": "Categorie",
            "metafields": [], "source_url": "https://supplier.example/p",
            "notes": "Notitie",
        },
        [{"url": "https://supplier.example/image.jpg", "selected": True}],
    )

    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        product = db.execute("SELECT * FROM products WHERE sku='ABC-1'").fetchone()
        raw = json.loads(product["raw_data_json"])
        images = db.execute("SELECT * FROM product_images").fetchall()
    assert result["saved_images"] == 1
    assert product["ai_title"] == "Titel"
    assert product["sale_price"] == 20
    assert product["content_locked"] == 1
    assert raw["product_maker_overrides"]["seo_title"] == "SEO"
    assert len(images) == 1


def test_productmaker_creates_missing_sku_at_selected_supplier(monkeypatch, tmp_path):
    database = tmp_path / "supplier.sqlite"
    with sqlite3.connect(database) as db:
        db.executescript(
            """CREATE TABLE products(
              sku TEXT PRIMARY KEY,supplier_sku TEXT,ean TEXT,vendor TEXT,brand TEXT,
              source_title TEXT,source_description TEXT,sale_price REAL,cost_price REAL,
              purchase_unit TEXT DEFAULT 'stuk',sales_unit TEXT DEFAULT 'stuk',
              purchase_units_per_sales_unit REAL DEFAULT 1,stock_quantity INTEGER,
              available INTEGER,product_type TEXT,product_group_name TEXT,ai_title TEXT,
              html_description TEXT,ai_tags_json TEXT,raw_data_json TEXT DEFAULT '{}',
              source_present INTEGER DEFAULT 1,content_locked INTEGER DEFAULT 0,
              content_locked_at TEXT,first_seen_at TEXT,last_seen_at TEXT,updated_at TEXT
            );
            CREATE TABLE product_images(
              sku TEXT,image_url TEXT,position INTEGER,alt_text TEXT,
              UNIQUE(sku,image_url)
            );"""
        )
    monkeypatch.setattr(hub, "init_supplier_database", lambda slug: database)

    hub.save_product_maker_values(
        "vynckiers", "LA1801",
        {"title": "Mobiele lasafzuiging", "sale_price": 1700},
    )

    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        product = db.execute(
            "SELECT * FROM products WHERE sku='LA1801'"
        ).fetchone()
    assert product is not None
    assert product["supplier_sku"] == "LA1801"
    assert product["ai_title"] == "Mobiele lasafzuiging"
    assert product["sale_price"] == 1700
    assert product["content_locked"] == 1
    assert json.loads(product["raw_data_json"])["product_maker_created"] is True


def test_productmaker_copies_local_upload_to_durable_supplier_asset(
    monkeypatch, tmp_path,
):
    database = tmp_path / "supplier.sqlite"
    with sqlite3.connect(database) as db:
        db.executescript(
            """CREATE TABLE products(
              sku TEXT PRIMARY KEY,supplier_sku TEXT,ean TEXT,vendor TEXT,brand TEXT,
              source_title TEXT,source_description TEXT,sale_price REAL,cost_price REAL,
              purchase_unit TEXT DEFAULT 'stuk',sales_unit TEXT DEFAULT 'stuk',
              purchase_units_per_sales_unit REAL DEFAULT 1,stock_quantity INTEGER,
              available INTEGER,product_type TEXT,product_group_name TEXT,ai_title TEXT,
              html_description TEXT,ai_tags_json TEXT,raw_data_json TEXT DEFAULT '{}',
              source_present INTEGER DEFAULT 1,content_locked INTEGER DEFAULT 0,
              content_locked_at TEXT,first_seen_at TEXT,last_seen_at TEXT,updated_at TEXT
            );
            CREATE TABLE product_images(
              sku TEXT,image_url TEXT,position INTEGER,alt_text TEXT,
              UNIQUE(sku,image_url)
            );
            INSERT INTO products(sku,raw_data_json) VALUES('LOCAL-1','{}');"""
        )
    monkeypatch.setattr(hub, "init_supplier_database", lambda slug: database)
    upload_root = tmp_path / "product_maker_uploads"
    source = upload_root / "1" / "photo.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"product photo")
    asset_root = tmp_path / "supplier_assets"
    monkeypatch.setattr(hub, "PRODUCT_MAKER_UPLOAD_DIR", upload_root)
    monkeypatch.setattr(hub, "PRODUCT_MAKER_SUPPLIER_ASSET_DIR", asset_root)

    result = hub.save_product_maker_values(
        "supplier", "LOCAL-1", {"title": "Lokale foto", "sale_price": 1},
        [{"url": str(source), "title": "Vooraanzicht", "selected": True}],
    )

    with sqlite3.connect(database) as db:
        image_count = db.execute("SELECT COUNT(*) FROM product_images").fetchone()[0]
        raw = json.loads(db.execute(
            "SELECT raw_data_json FROM products WHERE sku='LOCAL-1'"
        ).fetchone()[0])
    saved = raw["product_maker_overrides"]["manual_images"][0]
    assert result["saved_images"] == 1
    assert image_count == 0
    assert saved["alt_text"] == "Vooraanzicht"
    assert saved["image_url"].startswith(str(asset_root))
    assert open(saved["image_url"], "rb").read() == b"product photo"

    product = hub.get_supplier_product("supplier", "LOCAL-1")
    assert product["images"] == [saved]
