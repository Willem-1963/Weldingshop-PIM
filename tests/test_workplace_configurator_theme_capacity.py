from pathlib import Path

import pytest

from app.workplace_configurator import _connect
from app.workplace_configurator_update import (
    _delete_theme,
    _duplicate_theme,
    _ensure_theme_capacity,
    _select_disposable_pim_preview,
    _start_run,
    _theme_capacity_preflight,
)


def test_deleted_theme_not_found_is_confirmed_deletion():
    class DeletedClient:
        def graphql(self, query, variables=None):
            if "PimDeleteOldConfiguratorPreview" in query:
                return {"themeDelete": {
                    "deletedThemeId": variables["id"], "userErrors": [],
                }}
            if "PimDeletedTheme" in query:
                raise RuntimeError(
                    "[{'message': 'Theme does not exist', "
                    "'extensions': {'code': 'NOT_FOUND'}}]"
                )
            raise AssertionError(query)

    _delete_theme(DeletedClient(), "gid://shopify/OnlineStoreTheme/3")


def test_theme_capacity_only_discards_oldest_pim_preview():
    themes = [
        {"id": "gid://shopify/OnlineStoreTheme/1", "name": "Live", "role": "MAIN",
         "createdAt": "2026-11-01T00:00:00Z"},
        {"id": "gid://shopify/OnlineStoreTheme/2", "name": "Handmatige preview",
         "role": "UNPUBLISHED", "createdAt": "2026-10-01T00:00:00Z"},
        {"id": "gid://shopify/OnlineStoreTheme/3", "name": "PIM Werkplaatsconfigurator update 3",
         "role": "UNPUBLISHED", "createdAt": "2026-11-02T00:00:00Z"},
        {"id": "gid://shopify/OnlineStoreTheme/4", "name": "PIM Werkplaatsconfigurator update 6",
         "role": "UNPUBLISHED", "createdAt": "2026-11-18T00:00:00Z"},
    ]
    disposable = _select_disposable_pim_preview(
        themes, "gid://shopify/OnlineStoreTheme/1"
    )
    assert disposable["id"].endswith("/3")


def test_single_old_pim_preview_is_disposable_when_main_is_pim_release():
    themes = [
        {"id": "gid://shopify/OnlineStoreTheme/15",
         "name": "PIM Werkplaatsconfigurator update 15", "role": "MAIN",
         "createdAt": "2026-11-19T16:03:36Z"},
        {"id": "gid://shopify/OnlineStoreTheme/6",
         "name": "PIM Werkplaatsconfigurator update 6", "role": "UNPUBLISHED",
         "createdAt": "2026-11-18T12:38:34Z"},
        {"id": "gid://shopify/OnlineStoreTheme/2", "name": "Handmatige preview",
         "role": "UNPUBLISHED", "createdAt": "2026-11-20T00:00:00Z"},
    ]
    disposable = _select_disposable_pim_preview(
        themes, "gid://shopify/OnlineStoreTheme/15"
    )
    assert disposable["id"].endswith("/6")


def test_single_pim_preview_is_kept_when_main_is_manual():
    themes = [
        {"id": "gid://shopify/OnlineStoreTheme/1", "name": "Handmatig live",
         "role": "MAIN", "createdAt": "2026-11-20T00:00:00Z"},
        {"id": "gid://shopify/OnlineStoreTheme/6",
         "name": "PIM Werkplaatsconfigurator update 6", "role": "UNPUBLISHED",
         "createdAt": "2026-11-18T12:38:34Z"},
    ]
    assert _select_disposable_pim_preview(
        themes, "gid://shopify/OnlineStoreTheme/1"
    ) is None


def test_full_theme_library_without_old_pim_preview_stops_safely():
    class FullClient:
        def graphql(self, query, variables=None):
            assert "PimThemeLibrary" in query
            return {"themes": {"nodes": [
                {"id": f"gid://shopify/OnlineStoreTheme/{i}", "name": f"Theme {i}",
                 "role": "MAIN" if i == 1 else "UNPUBLISHED",
                 "createdAt": f"2026-11-{i:02d}T00:00:00Z"}
                for i in range(1, 21)
            ]}}

    with pytest.raises(RuntimeError, match="geen oude PIM-preview"):
        _ensure_theme_capacity(FullClient(), "gid://shopify/OnlineStoreTheme/1")


def test_update_preflight_reserves_slot_before_analysis(monkeypatch):
    calls = []
    main = {
        "id": "gid://shopify/OnlineStoreTheme/1",
        "name": "PIM Werkplaatsconfigurator update 17",
        "role": "MAIN",
    }
    removed = {
        "id": "gid://shopify/OnlineStoreTheme/2",
        "name": "PIM Werkplaatsconfigurator update 15",
        "role": "UNPUBLISHED",
    }

    monkeypatch.setattr(
        "app.workplace_configurator_update._main_theme",
        lambda client: calls.append("main") or main,
    )
    monkeypatch.setattr(
        "app.workplace_configurator_update._ensure_theme_capacity",
        lambda client, main_id: calls.append(("capacity", main_id)) or removed,
    )

    result = _theme_capacity_preflight(object())

    assert calls == ["main", ("capacity", main["id"])]
    assert result == {"main": main, "removed": removed}


def test_empty_duplicate_response_recovers_theme_by_unique_name(tmp_path: Path):
    class AsyncDuplicateClient:
        def graphql(self, query, variables=None):
            if "PimThemeLibrary" in query:
                nodes = []
                if getattr(self, "duplicate_called", False):
                    nodes.append({
                        "id": "gid://shopify/OnlineStoreTheme/999",
                        "name": "PIM Werkplaatsconfigurator update 1",
                        "role": "UNPUBLISHED", "processing": False,
                        "createdAt": "2026-11-19T12:00:00Z",
                    })
                return {"themes": {"nodes": nodes}}
            if "PimDuplicateConfiguratorTheme" in query:
                self.duplicate_called = True
                return {"themeDuplicate": {"newTheme": None, "userErrors": []}}
            if "PimThemeProcessing" in query:
                return {"theme": {
                    "id": "gid://shopify/OnlineStoreTheme/999",
                    "name": "PIM Werkplaatsconfigurator update 1",
                    "role": "UNPUBLISHED", "processing": False,
                }}
            raise AssertionError(query)

    db = tmp_path / "configurator.sqlite3"
    run_id = _start_run(db, "HANDMATIG")
    theme = _duplicate_theme(
        AsyncDuplicateClient(), "gid://shopify/OnlineStoreTheme/1", run_id, db
    )
    assert theme["id"].endswith("/999")
    with _connect(db) as conn:
        row = conn.execute(
            "SELECT preview_theme_id FROM sync_runs WHERE id=?", (run_id,)
        ).fetchone()
    assert row["preview_theme_id"] == "999"
