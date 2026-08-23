from email.message import Message

from app.suppliers import on_demand_import


class _Response:
    status = 200

    def __init__(self, body: str):
        self._body = body.encode()
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int):
        return self._body


def test_kentie_search_resolves_only_exact_sku(monkeypatch):
    page = """
    <div class="item-box"><div class="sku">395012</div>
      <a href="/nl/wrong">Verkeerd product</a></div>
    <div class="item-box"><div class="sku">5012</div>
      <h2 class="product-title">
        <a href="/nl/insteeknippel-gas-met-wartel-916l-unf">Goed product</a>
      </h2></div>
    """
    monkeypatch.setattr(
        on_demand_import.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(page),
    )

    result = on_demand_import._official_product_search_url(
        "https://www.kentie.shop/nl", "5012", {"kentie.shop"}
    )

    assert result == (
        "https://www.kentie.shop/nl/insteeknippel-gas-met-wartel-916l-unf"
    )


def test_kentie_detail_page_only_uses_exact_product_gallery(monkeypatch):
    page = """
    <html><head>
      <meta property="og:image"
            content="https://www.kentie.shop/images/thumbs/0001149_917005_550.jpeg">
    </head><body>
      <div class="product-essential">
        <div class="gallery"><div class="picture">
          <a href="https://www.kentie.shop/images/thumbs/0001149_917005.jpeg">
            <img src="https://www.kentie.shop/images/thumbs/0001149_917005_550.jpeg">
          </a>
        </div></div>
        <img src="https://www.kentie.shop/Plugins/Misc.Kentie/Content/StockImages/onstock.png">
        <span class="sku">917005</span>
      </div>
      <div class="related-products-grid">
        <img src="https://www.kentie.shop/images/thumbs/0000055_other-product_415.jpeg">
      </div>
    </body></html>
    """
    monkeypatch.setattr(
        on_demand_import.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(page),
    )

    images = on_demand_import._page_images_for_article(
        "https://www.kentie.shop/nl/reduceerventiel-stikstof-kayser-0-10-bar",
        "917005",
        {"kentie.shop"},
    )

    assert images == [
        "https://www.kentie.shop/images/thumbs/0001149_917005.jpeg"
    ]
    assert all("other-product" not in image for image in images)


def test_verified_kentie_page_uses_supplier_feed_without_ai_rejudgement(monkeypatch):
    page = """
    <div class="product-name"><h1 itemprop="name">Drukregelaar 0-4 bar</h1></div>
    <span class="value" itemprop="sku">1510723</span>
    """
    monkeypatch.setattr(
        on_demand_import.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(page),
    )

    data = on_demand_import._deterministic_kentie_data(
        "https://www.kentie.shop/nl/drukregelaar", "1510723",
        {
            "Omschrijving": "Reduceerventiel propaan 0-4 bar 21.8l",
            "Toevoeging 1": 'Kombi / DIN x 3/8"l 12 kg/uur',
            "Toevoeging 2": "voorheen 1010191",
            "Drukbereik": "0-4 bar", "EAN": 8719349072228.0,
        },
        "oude titel",
    )

    assert data["exact_match"] is True
    assert data["matched_article_number"] == "1510723"
    assert data["title"] == "Drukregelaar 0-4 bar"
    assert "Kombi / DIN" in data["description"]
    assert data["technical_specifications"]["Drukbereik"] == "0-4 bar"
    assert data["ean"] == "8719349072228"
    assert "rechtstreeks en letterlijk bevestigd" in data["source_summary"]


def test_verified_kentie_page_builds_complete_text_from_pim_context(monkeypatch):
    page = '<div class="product-name"><h1>Slang propaan per meter</h1></div>'
    monkeypatch.setattr(
        on_demand_import.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _Response(page),
    )

    data = on_demand_import._deterministic_kentie_data(
        "https://www.kentie.shop/nl/slang-propaan-per-meter", "1000046", {},
        "Slang propaan per meter",
        {"product_group_name": "Slang", "category": "Propaan", "execution": "per meter"},
    )

    assert "officieel Kentie-product" not in data["description"]
    assert data["technical_specifications"]["Artikelnummer"] == "1000046"
    assert data["technical_specifications"]["Productgroep"] == "Slang"


def test_kentie_visible_description_overrides_conflicting_old_execution(monkeypatch):
    page = """
    <div class="product-name"><h1>Handbrander en schild en turbokop 45mm</h1></div>
    <span itemprop="sku">1510501</span>
    <div class="short-description">Handbrander met turbokop met een diameter van 45mm.
    Voorzien van een spaarregeling en een zwaar (2mm) aluminium minium beschermschild.
    Wordt geleverd in een doos.</div>
    """
    monkeypatch.setattr(
        on_demand_import.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _Response(page),
    )

    data = on_demand_import._deterministic_kentie_data(
        "https://www.kentie.shop/nl/handbrander", "1510501", {},
        "Handbrander met schild en turbokop 50 mm",
        {"category": "Propaan", "execution": "50 mm"},
    )

    assert "diameter van 45mm" in data["description"]
    assert "spaarregeling" in data["description"]
    assert "50 mm" not in data["description"]
    assert "Uitvoering" not in data["technical_specifications"]
    assert all("50 mm" not in value for value in data["technical_specifications"].values())


def test_short_official_kentie_description_is_not_padded_with_internal_phrase(monkeypatch):
    page = """
    <h1>Campingregelaar 30 gram</h1><span itemprop="sku">1510630</span>
    <div class="short-description">Drukregelaar voor een Campingaz tank.</div>
    """
    monkeypatch.setattr(
        on_demand_import.urllib.request, "urlopen",
        lambda *_args, **_kwargs: _Response(page),
    )
    data = on_demand_import._deterministic_kentie_data(
        "https://www.kentie.shop/nl/campingregelaar", "1510630", {},
        "Campingregelaar", {"category": "Propaan"},
    )
    assert data["description"] == "Drukregelaar voor een Campingaz tank."
    assert "officieel Kentie-product" not in data["description"]
