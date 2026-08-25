from app.suppliers.hub import get_supplier_product
from app.suppliers.invoice_catalog import persist_linked_invoice_product


def test_invoice_product_is_created_and_evidence_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr("app.suppliers.hub.SUPPLIER_DIR", tmp_path)
    monkeypatch.setattr("app.suppliers.invoice_catalog.init_supplier_database", lambda slug: __import__("app.suppliers.hub", fromlist=["init_supplier_database"]).init_supplier_database(slug))
    invoice = {
        "mapping_status": "mapped", "supplier_article_number": "42-5000.270",
        "shopify_sku": "5000.270", "invoice_number": "2530797",
        "invoice_date": "2026-08-07", "line_number": 2, "quantity": "3",
        "net_unit_price": "35.7067", "relation_id": "20472237",
    }
    shopify = {
        "id": "gid://shopify/ProductVariant/2", "sku": "5000.270",
        "barcode": "", "price": "50.35", "inventory_quantity": 3,
        "inventory_item_id": "gid://shopify/InventoryItem/2",
        "product": {
            "id": "gid://shopify/Product/2", "title": "Voorste glasplaat",
            "description_html": "<p>Omschrijving</p>", "vendor": "HATEK",
            "product_type": "Lashelmaccessoire", "handle": "voorste-glasplaat",
            "status": "ACTIVE", "category": {"name": "Welding Helmets", "full_name": "Business > Welding Helmets"},
            "images": [{"url": "https://cdn.example/image.jpg", "alt": "Glasplaat"}],
        },
    }

    first = persist_linked_invoice_product("hatek", invoice=invoice, shopify=shopify)
    second = persist_linked_invoice_product("hatek", invoice=invoice, shopify=shopify)

    assert first["created"] is True
    assert second["created"] is False
    product = get_supplier_product("hatek", "5000.270")
    assert product["supplier_sku"] == "42-5000.270"
    assert product["cost_price"] == 35.7067
    assert product["sale_price"] == 50.35
    assert product["images"][0]["image_url"] == "https://cdn.example/image.jpg"


def test_invoice_import_does_not_replace_richer_pim_text(monkeypatch, tmp_path):
    monkeypatch.setattr("app.suppliers.hub.SUPPLIER_DIR", tmp_path)
    monkeypatch.setattr("app.suppliers.invoice_catalog.init_supplier_database", lambda slug: __import__("app.suppliers.hub", fromlist=["init_supplier_database"]).init_supplier_database(slug))
    base_invoice = {"mapping_status": "mapped", "supplier_article_number": "A", "shopify_sku": "S", "invoice_number": "I", "line_number": 1, "net_unit_price": "10"}
    snapshot = {"id": "V", "sku": "S", "price": "14.10", "inventory_item_id": "II", "product": {"id": "P", "title": "Shopify", "description_html": "kort", "images": []}}
    persist_linked_invoice_product("test", invoice=base_invoice, shopify=snapshot)
    from app.suppliers.hub import _connect, init_supplier_database
    with _connect(init_supplier_database("test")) as db:
        db.execute("UPDATE products SET source_title='Rijke PIM-titel',source_description='Rijke omschrijving' WHERE sku='S'")
    snapshot["product"]["title"] = "Nieuwe Shopify-titel"
    persist_linked_invoice_product("test", invoice=base_invoice, shopify=snapshot)
    product = get_supplier_product("test", "S")
    assert product["source_title"] == "Rijke PIM-titel"
    assert product["source_description"] == "Rijke omschrijving"
