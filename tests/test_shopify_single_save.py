import sqlite3
from types import SimpleNamespace

from app.shopify import sync


def test_document_conversion_uses_shared_cache_for_read_only_sku_dir(
    monkeypatch, tmp_path
):
    localized = tmp_path / "localized"
    sku_dir = localized / "7812873"
    sku_dir.mkdir(parents=True)
    html_path = sku_dir / "folder.html"
    html_path.write_text("<p>Folder</p>")
    database = tmp_path / "documents.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE product_documents (id INTEGER, local_path TEXT, "
            "filename TEXT, mime_type TEXT, size_bytes INTEGER, sha256 TEXT, "
            "updated_at TEXT)"
        )
        connection.execute("INSERT INTO product_documents(id) VALUES(1)")

    monkeypatch.setattr(sync.os, "access", lambda path, mode: False)
    monkeypatch.setattr(sync.Path, "is_file", lambda self: True)

    def fake_run(arguments, **kwargs):
        output = next(
            value.split("=", 1)[1]
            for value in arguments if value.startswith("--print-to-pdf=")
        )
        sync.Path(output).write_bytes(b"%PDF-test")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(sync.subprocess, "run", fake_run)

    pdf_path, filename, mime_type = sync._shopify_compatible_document(
        database,
        {"id": 1, "title": "Folder", "filename": "folder.html",
         "mime_type": "text/html"},
        html_path,
    )

    assert pdf_path.parent == localized / "shopify-pdf-cache"
    assert filename == "folder.pdf"
    assert mime_type == "application/pdf"


def test_single_save_detects_variant_before_documents(monkeypatch, tmp_path):
    database = tmp_path / "supplier.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE products (sku TEXT, shopify_handle TEXT, "
            "shopify_status TEXT, updated_at TEXT)"
        )
        connection.execute("INSERT INTO products(sku) VALUES('VAR-1')")

    calls = []

    class Client:
        shop_domain = "example.myshopify.com"

        def find_variant_by_sku(self, sku):
            calls.append(("inspect", sku))
            return {
                "id": "gid://shopify/ProductVariant/2",
                "sku": sku,
                "title": "1,0 mm",
                "selectedOptions": [{"name": "Diameter", "value": "1,0 mm"}],
                "media": {"nodes": []},
                "product": {
                    "id": "gid://shopify/Product/1",
                    "title": "Familie",
                    "handle": "familie",
                    "status": "ACTIVE",
                    "vendor": "Tecweld",
                    "options": [{"name": "Diameter"}],
                    "variants": {"nodes": [{"id": "2"}]},
                },
            }

        def graphql(self, query, variables):
            calls.append(("update", variables))
            return {"productVariantsBulkUpdate": {
                "productVariants": [{
                    "id": "gid://shopify/ProductVariant/2", "sku": "VAR-1"
                }],
                "userErrors": [],
            }}

    monkeypatch.setattr(sync, "get_shopify_settings", lambda: {"enabled": True})
    monkeypatch.setattr(sync, "get_supplier", lambda slug: {"name": "Tecweld"})
    monkeypatch.setattr(
        sync, "supplier_route",
        lambda slug: SimpleNamespace(shopify_vendor_names=("Tecweld",)),
    )
    monkeypatch.setattr(sync, "_source_products", lambda slug: [{
        "sku": "VAR-1", "raw_data_json": "{}"
    }])
    monkeypatch.setattr(sync.ShopifyClient, "from_settings", lambda: Client())
    monkeypatch.setattr(sync, "supplier_database_path", lambda slug: database)
    monkeypatch.setattr(sync, "_input", lambda source, existing: {
        "variants": [{
            "id": existing["variant"]["id"],
            "optionValues": [{"optionName": "Maat", "name": "1"}],
            "price": "12.50",
        }]
    })
    monkeypatch.setattr(
        sync, "_upload_product_documents",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("documenten mogen niet vóór/voor variantupdate starten")
        ),
    )

    result = sync.upload_pim_product_draft("tecweld", "VAR-1")

    assert result["target_type"] == "variant"
    assert calls[0] == ("inspect", "VAR-1")
    assert calls[1][0] == "update"
    variant = calls[1][1]["variants"][0]
    assert "optionValues" not in variant
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT shopify_status FROM products WHERE sku='VAR-1'"
        ).fetchone()[0] == "active"
