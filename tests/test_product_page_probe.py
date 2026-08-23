from app.product_maker_standalone import research


class Response:
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def raise_for_status(self):
        return None

    text = """<html><head><title>Professionele boormachine</title></head>
    <body><h1>Professionele boormachine</h1><p>Artikelnummer: ABC-123</p></body></html>"""


def test_visible_sku_is_accepted_without_json_ld(monkeypatch):
    monkeypatch.setattr(research.requests, "get", lambda *args, **kwargs: Response())

    result = research.probe_product_page(
        "https://supplier.example/products/abc-123", "ABC-123", "SKU"
    )

    assert result["sku"] == "ABC-123"
    assert result["title"] == "Professionele boormachine"


def test_identifier_must_still_be_visible_on_page(monkeypatch):
    monkeypatch.setattr(research.requests, "get", lambda *args, **kwargs: Response())

    try:
        research.probe_product_page(
            "https://supplier.example/products/other", "NOT-FOUND", "SKU"
        )
    except ValueError as exc:
        assert "staat niet volledig" in str(exc)
    else:
        raise AssertionError("ValueError expected")
