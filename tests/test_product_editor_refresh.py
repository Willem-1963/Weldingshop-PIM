from app.product_maker_standalone import page


def test_editor_widget_values_are_cleared_after_automatic_build(monkeypatch):
    state = {
        "pm_ws_title_7": "",
        "pm_ws_description_7": "",
        "pm_publication_status_7": "Actief",
        "unrelated": "keep",
    }
    monkeypatch.setattr(page.st, "session_state", state)

    page._clear_product_editor_widgets()

    assert state == {
        "pm_publication_status_7": "Actief",
        "unrelated": "keep",
    }
