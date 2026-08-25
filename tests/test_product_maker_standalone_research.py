from pathlib import Path

from app.product_maker_standalone.research import inspect_official_page, probe_product_page
from app.product_maker_standalone.service import ProductMakerService


HTML = """
<html><head><title>Officieel ABC-123</title>
<meta property="og:image" content="https://official.example/media/abc.jpg">
<script type="application/ld+json">{
  "@type":"Product","name":"Officieel product","sku":"ABC-123",
  "gtin13":"8712345678901","mpn":"MPN-123","brand":{"name":"Test"},
  "description":"Bewezen omschrijving","image":["https://official.example/media/abc.jpg"]
}</script></head><body><h1>ABC-123</h1><p>EAN 8712345678901 MPN-123</p>
<a href="/docs/abc-123-datasheet.pdf">Datasheet ABC-123</a></body></html>
"""


class Response:
    headers = {"Content-Type": "text/html"}
    text = HTML
    def raise_for_status(self):
        return None


def test_official_page_requires_identifier_and_extracts_evidence(tmp_path, monkeypatch):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    supplier = service.save_supplier("Test", "official.example")
    draft_id = service.save_draft(
        supplier_id=supplier, sku="ABC-123", ean="8712345678901",
        manufacturer_number="MPN-123", vendor="Test", purchase_price="10",
        sale_price="14", initial_quantity=1, unit_factor="1",
    )
    monkeypatch.setattr("app.product_maker_standalone.research.requests.get", lambda *a, **k: Response())
    result = inspect_official_page(service, draft_id, "https://official.example/p/abc")
    assert result["matched_by"] == "ean"
    assert result["facts"]["title"] == "Officieel product"
    assert len(result["images"]) == 1
    assert result["documents"][0]["kind"] == "datasheet"
    assert any(item["field_name"] == "source_url" for item in service.list_evidence(draft_id))


def test_rejects_unapproved_domain(tmp_path):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    supplier = service.save_supplier("Test", "official.example")
    draft_id = service.save_draft(
        supplier_id=supplier, sku="ABC-123", vendor="Test", purchase_price="1",
        sale_price="2", unit_factor="1",
    )
    try:
        inspect_official_page(service, draft_id, "https://marketplace.example/p/abc")
    except ValueError as exc:
        assert "goedgekeurd" in str(exc)
    else:
        raise AssertionError("Niet-goedgekeurd domein werd geaccepteerd")


def test_exact_automatic_probe_domain_can_be_inspected(tmp_path, monkeypatch):
    service = ProductMakerService(tmp_path / "maker.sqlite3")
    supplier = service.save_supplier("Distributeur", "distributeur.example")
    draft_id = service.save_draft(
        supplier_id=supplier, sku="ABC-123", vendor="Test",
        purchase_price="1", sale_price="2", unit_factor="1",
    )
    monkeypatch.setattr(
        "app.product_maker_standalone.research.requests.get",
        lambda *args, **kwargs: Response(),
    )

    result = inspect_official_page(
        service, draft_id, "https://official.example/p/abc",
        verified_domain="official.example",
    )

    assert result["matched_by"] == "sku"
    assert service.get_supplier(supplier)["approved_domains"] == [
        "distributeur.example"
    ]


def test_probe_reads_microdata_without_json_ld(monkeypatch):
    class MicrodataResponse:
        headers = {"Content-Type": "text/html"}
        text = """<html><head>
        <meta property="og:title" content="Websiteproduct">
        <meta property="og:description" content="Omschrijving van de website">
        </head><body><div class="manufacturers"><span class="value">Kentie</span></div>
        <h1 itemprop="name">Aansluitpen website</h1>
        <span itemprop="sku">998044</span>
        <div class="full-description" itemprop="description"><p>Alleen van de site.</p></div>
        </body></html>"""

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "app.product_maker_standalone.research.requests.get",
        lambda *args, **kwargs: MicrodataResponse(),
    )
    result = probe_product_page(
        "https://www.kentie.shop/nl/product", "998044", "SKU",
    )
    assert result["sku"] == "998044"
    assert result["vendor"] == "Kentie"
    assert result["title"] == "Aansluitpen website"
    assert result["description_html"] == "<p>Alleen van de site.</p>"
