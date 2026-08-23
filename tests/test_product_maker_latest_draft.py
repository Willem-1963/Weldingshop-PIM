from app.product_maker_standalone.service import ProductMakerService


def test_drafts_are_listed_most_recent_first(tmp_path):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    supplier_id = service.save_supplier("Merk", "merk.example", brand="Merk")
    common = dict(
        supplier_id=supplier_id, ean="", manufacturer_number="", vendor="Merk",
        description_html="", short_description="", seo_title="", seo_description="",
        purchase_price="0", sale_price="0", compare_at_price="",
        initial_quantity=0, purchase_unit="stuk", sales_unit="stuk", unit_factor="1",
        product_type="", category_id="", category_label="", tags=[], metafields=[],
        source_url="", notes="",
    )
    first = service.save_draft(sku="OLD", title="Oud", **common)
    second = service.save_draft(sku="NEW", title="Nieuw", **common)
    with service.connect() as db:
        db.execute("UPDATE pm_drafts SET updated_at='2026-01-01' WHERE id=?", (first,))
        db.execute("UPDATE pm_drafts SET updated_at='2026-01-02' WHERE id=?", (second,))

    assert service.list_drafts()[0]["id"] == second
