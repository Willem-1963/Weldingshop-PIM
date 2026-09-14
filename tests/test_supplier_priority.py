import json
import sqlite3

import pytest

from app.shopify.sync import _priority_matches
from app.suppliers import hub


def match(sku, pid, status="ACTIVE"):
    return {"variant": {"sku": sku}, "product": {"id": pid, "status": status}}


@pytest.mark.parametrize("status", ["DRAFT", "ARCHIVED"])
def test_priority_blocks_existing_unchanged_and_new_products(status):
    existing = {"VP-210632": match("VP-210632", "vp"),
                "VP-SIBLING": match("VP-SIBLING", "vp"),
                "VP-OTHER": match("VP-OTHER", "other")}
    preferred = {"210632": match("210632", "rh"), "210633": match("210633", "rh2")}
    blocked, rows, details = _priority_matches(
        "valkenpower", [{"sku": "VP-210633", "supplier_sku": "210633"}],
        existing, preferred, "Rhodius", status)
    assert blocked == {"VP-210632", "VP-SIBLING", "VP-210633"}
    assert rows == [{"input": {"id": "vp", "status": status}}]
    assert len(details) == 2


def test_priority_does_not_touch_preferred_product_or_repeat_status():
    preferred = {"210632": match("210632", "rh")}
    assert _priority_matches("valkenpower", [], {"VP-210632": match("VP-210632", "rh")},
                             preferred, "Rhodius", "DRAFT")[0] == set()
    blocked, rows, _ = _priority_matches("valkenpower", [],
        {"VP-210632": match("VP-210632", "vp", "DRAFT")}, preferred, "Rhodius", "DRAFT")
    assert blocked == {"VP-210632"}
    assert rows == []


def test_save_priority_preserves_options_and_can_disable(tmp_path, monkeypatch):
    path = tmp_path / "registry.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("create table suppliers(slug text, request_options_json text, updated_at text)")
        db.execute("insert into suppliers values ('valkenpower', ?, '')", (json.dumps({"keep": True}),))
    monkeypatch.setattr(hub, "REGISTRY_PATH", path)
    monkeypatch.setattr(hub, "init_registry", lambda: None)
    monkeypatch.setattr(hub, "get_supplier", lambda slug: {"slug": slug} if slug == "rhodius" else None)
    hub.save_supplier_priority("valkenpower", "rhodius", "ARCHIVED")
    with sqlite3.connect(path) as db:
        options = json.loads(db.execute("select request_options_json from suppliers").fetchone()[0])
    assert options == {"keep": True, "supplier_priority": {"supplier_slug": "rhodius", "status": "ARCHIVED"}}
    hub.save_supplier_priority("valkenpower", "", "DRAFT")
    with pytest.raises(ValueError):
        hub.save_supplier_priority("valkenpower", "valkenpower", "DRAFT")
    assert hub.supplier_priority_rule({"slug": "valkenpower"})["supplier_slug"] == "rhodius-abrasives-gmbh"
    assert hub.supplier_priority_rule({"slug": "valkenpower", "request_options": {
        "supplier_priority": {"supplier_slug": "", "status": "DRAFT"}}})["supplier_slug"] == ""
