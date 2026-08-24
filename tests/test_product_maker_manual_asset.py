from app.product_maker_standalone import service as service_module
from app.product_maker_standalone.page import _preview_image_source
from app.product_maker_standalone.service import ProductMakerService


def test_manually_added_asset_can_be_selected_immediately(tmp_path):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    draft_id = service.save_draft(
        None, supplier_id=None, sku="MANUAL-1", vendor="Test",
        title="Handmatige foto",
    )

    asset_id = service.add_asset(
        draft_id, "image", "https://example.test/product.jpg",
        title="Vooraanzicht", source_url="https://example.test/product.jpg",
        official=True, identifier_verified=True, selected=True,
    )

    assets = service.list_assets(draft_id)
    assert asset_id == assets[0]["id"]
    assert assets[0]["selected"] == 1
    assert assets[0]["official"] == 1
    assert assets[0]["identifier_verified"] == 1


def test_adding_existing_asset_does_not_clear_selection(tmp_path):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    draft_id = service.save_draft(
        None, supplier_id=None, sku="MANUAL-2", vendor="Test", title="Foto",
    )
    url = "https://example.test/product.jpg"
    service.add_asset(draft_id, "image", url, selected=True)

    service.add_asset(draft_id, "image", url, title="Nieuwe titel")

    asset = service.list_assets(draft_id)[0]
    assert asset["selected"] == 1
    assert asset["title"] == "Nieuwe titel"


def test_uploaded_image_is_saved_and_selected(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "DEFAULT_UPLOAD_DIRECTORY", tmp_path / "uploads")
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    draft_id = service.save_draft(
        None, supplier_id=None, sku="UPLOAD-1", vendor="Test", title="Foto",
    )

    asset_id = service.save_uploaded_image(
        draft_id, "product.JPG", b"image bytes", title="Product voorzijde",
    )

    asset = service.list_assets(draft_id)[0]
    assert asset["id"] == asset_id
    assert asset["selected"] == 1
    assert asset["identifier_verified"] == 1
    assert asset["title"] == "Product voorzijde"
    assert service_module.Path(asset["url"]).read_bytes() == b"image bytes"


def test_local_image_is_embedded_in_theme_preview(tmp_path):
    image = tmp_path / "product.png"
    image.write_bytes(b"preview image")

    source = _preview_image_source(str(image))

    assert source.startswith("data:image/png;base64,")
    assert str(image) not in source


def test_remote_image_url_is_unchanged_in_theme_preview():
    url = "https://example.test/product.jpg"
    assert _preview_image_source(url) == url


def test_rebuilding_from_pim_preserves_manual_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "DEFAULT_UPLOAD_DIRECTORY", tmp_path / "uploads")
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    draft_id = service.save_draft(
        None, supplier_id=None, sku="REBUILD-1", vendor="Test", title="Foto",
    )
    service.save_uploaded_image(draft_id, "product.jpg", b"image")
    service.add_asset(
        draft_id, "image", "https://supplier.test/old.jpg",
        source_url="https://supplier.test/product", official=True,
    )

    service.clear_source_material(draft_id, preserve_manual_uploads=True)

    assets = service.list_assets(draft_id)
    assert len(assets) == 1
    assert assets[0]["source_url"] == "handmatige upload"
    assert assets[0]["selected"] == 1
