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
