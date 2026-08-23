from app.product_maker_standalone.service import ProductMakerService


def test_central_suppliers_are_mirrored_with_stable_slug(tmp_path):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    count = service.sync_registered_suppliers([
        {
            "slug": "certilas",
            "name": "Certilas",
            "website_url": "https://certilas.com/nl",
            "source_location": "",
            "enabled": 1,
        },
        {
            "slug": "harder-lastechniek",
            "name": "Harder Lastechniek",
            "website_url": "",
            "source_location": "",
            "enabled": 1,
        },
    ])

    suppliers = service.list_suppliers(synced_only=True)
    assert count == 2
    assert [item["sync_slug"] for item in suppliers] == [
        "certilas", "harder-lastechniek",
    ]
    assert suppliers[0]["approved_domains"] == ["certilas.com"]
    assert suppliers[1]["approved_domains"] == []


def test_supplier_sync_updates_instead_of_duplicating(tmp_path):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    supplier = {
        "slug": "valkenpower", "name": "Valkenpower", "enabled": 1,
        "website_url": "https://www.valkenpower.com/", "source_location": "",
    }
    service.sync_registered_suppliers([supplier])
    service.sync_registered_suppliers([{**supplier, "name": "Valkenpower Benelux"}])

    suppliers = service.list_suppliers(synced_only=True)
    assert len(suppliers) == 1
    assert suppliers[0]["name"] == "Valkenpower Benelux"


def test_incidental_product_and_temporary_supplier_are_fully_removed(tmp_path):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    supplier_id = service.save_supplier(
        "Incidental Tools", "incidental.example", brand="Incidental Tools"
    )
    draft_id = service.save_draft(
        supplier_id=supplier_id, sku="INC-1", ean="", manufacturer_number="",
        vendor="Incidental Tools", title="Incidenteel product",
        description_html="<p>Beschrijving</p>", short_description="Beschrijving",
        seo_title="", seo_description="", purchase_price="10", sale_price="20",
        compare_at_price="", initial_quantity=1, purchase_unit="stuk",
        sales_unit="stuk", unit_factor="1", product_type="Gereedschap",
        category_id="", category_label="", tags=[], metafields=[],
        source_url="https://incidental.example/product", notes="",
    )
    service.mark_incidental(draft_id)
    service.add_evidence(draft_id, "sku", "INC-1", state="proven")

    assert service.delete_incidental_draft(draft_id) is True
    assert service.list_drafts() == []
    assert service.list_suppliers() == []
