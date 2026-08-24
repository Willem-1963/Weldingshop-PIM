import sqlite3

from app.product_maker_standalone.service import ProductMakerService


def test_exact_verified_product_page_passes_official_source_check(tmp_path):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    supplier_id = service.save_supplier("Merk", "merk.example", brand="Merk")
    draft_id = service.save_draft(
        supplier_id=supplier_id, sku="LA1801", ean="", manufacturer_number="",
        vendor="Merk", title="Product", description_html="<p>Omschrijving</p>",
        short_description="Omschrijving", seo_title="", seo_description="",
        purchase_price="10", sale_price="20", compare_at_price="",
        initial_quantity=1, purchase_unit="stuk", sales_unit="stuk",
        unit_factor="1", product_type="Gereedschap", category_id="cat",
        category_label="Categorie", tags=[], metafields=[],
        source_url="https://merk.example/la1801", notes="",
    )
    service.add_evidence(
        draft_id, "source_url", "https://merk.example/la1801",
        state="proven", source_url="https://merk.example/la1801",
        matched_by="sku", confidence=1, approved=False,
    )

    report = service.quality_report(draft_id)
    assert report["checks"]["Officiële productbron"] is True


def test_price_from_purchase_invoice_allows_zero_prices(tmp_path):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    supplier_id = service.save_supplier("Merk", "merk.example", brand="Merk")
    draft_id = service.save_draft(
        supplier_id=supplier_id, sku="FACTUUR-1", vendor="Merk", title="Product",
        description_html="<p>Omschrijving</p>", purchase_price="0", sale_price="0",
        price_from_purchase_invoice=True, initial_quantity=0,
        purchase_unit="stuk", sales_unit="stuk", unit_factor="1",
        category_id="cat", source_url="https://merk.example/product",
    )

    report = service.quality_report(draft_id)

    assert report["checks"]["Inkoopprijs geldig of komt uit inkoopfactuur"] is True
    assert report["checks"]["Verkoopprijs geldig of wacht op inkoopfactuur"] is True
    assert service.get_draft(draft_id)["price_from_purchase_invoice"] == 1


def test_zero_prices_remain_blocking_by_default(tmp_path):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    draft_id = service.save_draft(
        sku="GEEN-FACTUUR-1", vendor="Merk", purchase_price="0", sale_price="0",
        unit_factor="1",
    )

    report = service.quality_report(draft_id)

    assert report["checks"]["Inkoopprijs geldig of komt uit inkoopfactuur"] is False
    assert report["checks"]["Verkoopprijs geldig of wacht op inkoopfactuur"] is False
    assert service.get_draft(draft_id)["price_from_purchase_invoice"] == 0


def test_latest_exact_sku_invoice_price_is_imported(tmp_path):
    erp_database = tmp_path / "erp.sqlite3"
    with sqlite3.connect(erp_database) as erp:
        erp.execute(
            """CREATE TABLE supplier_product_purchase_history(
                id INTEGER PRIMARY KEY, supplier_name TEXT,
                supplier_article_number TEXT, shopify_sku TEXT,
                invoice_number TEXT, invoice_date TEXT, unit_price TEXT,
                currency TEXT)"""
        )
        erp.executemany(
            "INSERT INTO supplier_product_purchase_history VALUES(?,?,?,?,?,?,?,?)",
            [
                (1, "Harder", "OUD", "1.01.30.220.20", "100", "2026-01-01", "0.40", "EUR"),
                (2, "Harder", "HLT/1013022020", "1.01.30.220.20", "226591", "2026-08-21", "0.51", "EUR"),
            ],
        )
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    draft_id = service.save_draft(
        sku="1.01.30.220.20", vendor="President Safety", purchase_price="0",
        sale_price="0", unit_factor="1", price_from_purchase_invoice=True,
    )

    result = service.refresh_purchase_invoice_price(draft_id, erp_database)

    assert result["invoice_number"] == "226591"
    assert result["proposed_sale_price"] == "0.72"
    assert service.get_draft(draft_id)["purchase_price"] == "0.51"
    assert service.get_draft(draft_id)["sale_price"] == "0.72"
    evidence = service.list_evidence(draft_id)
    assert {item["matched_by"] for item in evidence} == {
        "exact_shopify_sku", "purchase_price_markup_1_41",
    }


def test_invoice_price_does_not_replace_existing_sale_price(tmp_path):
    erp_database = tmp_path / "erp.sqlite3"
    with sqlite3.connect(erp_database) as erp:
        erp.execute(
            """CREATE TABLE supplier_product_purchase_history(
                id INTEGER PRIMARY KEY, supplier_name TEXT,
                supplier_article_number TEXT, shopify_sku TEXT,
                invoice_number TEXT, invoice_date TEXT, unit_price TEXT,
                currency TEXT)"""
        )
        erp.execute(
            "INSERT INTO supplier_product_purchase_history VALUES(1,'Leverancier','A','SKU-1','F-1','2026-08-21','10','EUR')"
        )
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    draft_id = service.save_draft(
        sku="SKU-1", vendor="Merk", purchase_price="0", sale_price="20",
        unit_factor="1", price_from_purchase_invoice=True,
    )

    result = service.refresh_purchase_invoice_price(draft_id, erp_database)

    assert result["proposed_sale_price"] == ""
    assert service.get_draft(draft_id)["sale_price"] == "20.00"
