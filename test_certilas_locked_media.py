from app.suppliers import website_enrichment as MODULE


def test_merge_icons_preserves_locked_editorial_content():
    original = (
        "<h2>Eigen titel</h2><p>Handmatig behouden tekst.</p>"
        "<h3>Lasposities</h3><p>PA, PB.</p><h3>Classificatie</h3><p>AWS</p>"
    )
    result = MODULE._merge_welding_position_icons(
        original,
        ["PA", "PB"],
        {
            "PA": "https://cdn.shopify.test/certilas-laspositie-pa.png",
            "PB": "https://cdn.shopify.test/certilas-laspositie-pb.png",
        },
    )
    assert "Handmatig behouden tekst." in result
    assert "<h3>Classificatie</h3>" in result
    assert "<p>PA, PB.</p>" not in result
    assert "certilas-laspositie-pa.png" in result
    assert "certilas-laspositie-pb.png" in result


def test_merge_icons_is_idempotent():
    first = MODULE._merge_welding_position_icons(
        "<h3>Lasposities</h3><p>PA.</p>",
        ["PA"],
        {"PA": "https://cdn.shopify.test/certilas-laspositie-pa.png"},
    )
    second = MODULE._merge_welding_position_icons(
        first,
        ["PA"],
        {"PA": "https://cdn.shopify.test/certilas-laspositie-pa.png"},
    )
    assert second.count("certilas-laspositie-pa.png") == 1


def test_merge_icons_can_append_missing_section_without_rewriting_copy():
    result = MODULE._merge_welding_position_icons(
        "<h2>Titel</h2><p>Redactionele inhoud.</p>",
        ["PC"],
        {"PC": "https://cdn.shopify.test/certilas-laspositie-pc.png"},
    )
    assert "Redactionele inhoud." in result
    assert "<h3>Lasposities</h3>" in result
    assert "certilas-laspositie-pc.png" in result
