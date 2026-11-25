import json
from pathlib import Path

import pytest

from app.workplace_configurator import (
    _connect, create_model_improvement_requests, current_version, latest_sync,
    save_live_snapshot,
)
from app.workplace_configurator_update import (
    _apply_open_model_improvements, _catalog_plan, _generate_missing_model,
    _parse_explicit_models, _product_dimensions_cm, _resumable_preview,
    _validate_catalog, run_update,
)


def _record():
    return {
        "variantId": "123", "title": "Bandzaagmachine oud", "handle": "bandzaag",
        "url": "https://www.weldingshop.nl/products/bandzaag", "price": 10000,
        "available": True, "code": "OLD", "series": "equipment",
        "modelKind": "bandsaw", "appearance": "bandsaw_test", "category": "Metaalbewerking",
        "width": 50, "depth": 60, "height": 120, "image": "https://example/old.jpg",
    }


def _model_row():
    raw = _record()
    return {
        "variant_id": "123", "sku": "OLD", "name": raw["title"], "product_url": raw["url"],
        "raw_json": json.dumps(raw),
    }


class Client:
    def graphql(self, query, variables=None):
        if "PimConfiguratorVariants" in query:
            return {"nodes": [{
                "id": "gid://shopify/ProductVariant/123", "sku": "NEW", "title": "Default Title",
                "price": "125.50", "inventoryQuantity": 2, "inventoryPolicy": "DENY",
                "image": {"url": "https://example/new.jpg"},
                "product": {"id": "gid://shopify/Product/9", "title": "Bandzaagmachine nieuw",
                            "handle": "bandzaag", "status": "ACTIVE",
                            "onlineStoreUrl": "https://www.weldingshop.nl/products/bandzaag",
                            "featuredMedia": None},
            }]}
        raise AssertionError(query)


def test_catalog_plan_syncs_safe_shopify_fields():
    plan = _catalog_plan(Client(), [_model_row()], [])
    row = plan["desired"][0]
    assert row["code"] == "NEW"
    assert row["price"] == 12550
    assert row["title"] == "Bandzaagmachine nieuw"
    assert row["image"] == "https://example/new.jpg"
    assert not plan["remove"]


def test_explicit_new_model_requires_complete_existing_model_kind():
    product = {
        "title": "Nieuwe machine", "handle": "nieuw", "status": "ACTIVE",
        "onlineStoreUrl": "https://example/new", "featuredMedia": None,
        "configurator": {"value": json.dumps({
            "modelKind": "bandsaw", "category": "Metaalbewerking",
            "width": 10, "depth": 20, "height": 30,
        })},
        "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/456", "sku": "X",
                                  "price": "10.00", "inventoryQuantity": 1,
                                  "inventoryPolicy": "DENY", "image": None}]},
    }
    rows, blocker = _parse_explicit_models(product, {"bandsaw"})
    assert blocker is None
    assert rows[0]["variantId"] == "456"
    _, blocker = _parse_explicit_models(product, {"cabinet"})
    assert "bestaat nog niet" in blocker


def test_dimensions_are_derived_from_shopify_metafields_and_description():
    product = {
        "prodLength": {"value": "530"}, "prodWidth": {"value": "620"},
        "prodHeight": {"value": "858"}, "descriptionHtml": "",
    }
    assert _product_dimensions_cm(product) == (62.0, 53.0, 85.8)
    product = {"descriptionHtml": "<p>Kast: 855B x 620D x 1000H mm</p>"}
    assert _product_dimensions_cm(product) == (85.5, 62.0, 100.0)


def test_missing_configurator_metafield_generates_model_from_shopify(monkeypatch):
    product = {
        "id": "gid://shopify/Product/55", "title": "Hoge werkplaatskast",
        "productType": "Werkplaatsuitrusting", "vendor": "SP Tools", "tags": [],
        "handle": "hoge-kast", "status": "ACTIVE",
        "onlineStoreUrl": "https://example/kast", "descriptionHtml": "",
        "featuredMedia": {"image": {"url": "https://example/kast.jpg"}},
        "prodLength": {"value": "622"}, "prodWidth": {"value": "700"},
        "prodHeight": {"value": "1954"}, "configurator": None,
        "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/456", "sku": "KAST",
                                  "price": "10.00", "inventoryQuantity": 1,
                                  "inventoryPolicy": "DENY", "image": None}]},
    }
    photo = {"schemaVersion": 1, "profile": "kast", "confidence": 0.9,
             "parts": [{"type": "box"}] * 8}
    monkeypatch.setattr(
        "app.workplace_configurator_update.generate_photo_model",
        lambda record, reason, notes: photo,
    )
    rows, blocker, config = _generate_missing_model(product, {"workbench"})
    assert blocker is None
    assert rows[0]["variantId"] == "456"
    assert rows[0]["width"] == 70.0
    assert rows[0]["photoModel"] == photo
    assert config["modelKind"] == "workbench"


def test_missing_source_data_blocks_only_that_product(monkeypatch):
    product = {
        "id": "gid://shopify/Product/56", "title": "Onbekend apparaat",
        "productType": "", "vendor": "", "tags": [], "descriptionHtml": "",
        "featuredMedia": {"image": {"url": "https://example/item.jpg"}},
        "variants": {"nodes": []},
    }
    rows, blocker, config = _generate_missing_model(product, {"workbench"})
    assert rows == [] and config is None
    assert "lengte, breedte en hoogte" in blocker


def test_static_regression_rejects_duplicate_variants():
    with pytest.raises(RuntimeError, match="Unieke variant"):
        _validate_catalog([_record(), _record()], [_record()])


def test_failed_update_is_visible_in_history(tmp_path: Path, monkeypatch):
    db = tmp_path / "configurator.sqlite3"
    monkeypatch.setattr(
        "app.workplace_configurator_update.parse_live_configurator",
        lambda: (_ for _ in ()).throw(RuntimeError("netwerkfout")),
    )
    with pytest.raises(RuntimeError, match="netwerkfout"):
        run_update(trigger_type="AUTOMATISCH", path=db)
    run = latest_sync(db)
    assert run["status"] == "FAILED"
    assert run["trigger_type"] == "AUTOMATISCH"
    assert "netwerkfout" in run["error"]


def test_failed_preview_can_be_resumed_for_same_live_theme(tmp_path: Path):
    db = tmp_path / "configurator.sqlite3"
    snapshot = {
        "version": {
            "signature": "sig", "theme_id": "191", "storefront_theme": "1",
            "engine_version": "v1", "model_version": "v1", "catalog_version": "v1",
            "model_count": 1, "page_url": "https://example/page",
            "catalog_url": "https://example/assets/catalog.json",
            "engine_url": "https://example/assets/engine.js", "catalog_sha256": "a" * 64,
        },
        "models": [_record()],
    }
    version_id = save_live_snapshot(snapshot, db)
    with _connect(db) as conn:
        conn.execute(
            """
            INSERT INTO sync_runs(
                started_at,status,version_id,preview_theme_id,trigger_type,phase,
                shopify_status,regression_status,details_json
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            ("2026-11-17T10:00:00+00:00", "FAILED", version_id, "999",
             "HANDMATIG", "GEFAALD", "GEFAALD", "GEFAALD", "{}"),
        )
    assert _resumable_preview(db, "gid://shopify/OnlineStoreTheme/191") == "999"
    assert _resumable_preview(db, "gid://shopify/OnlineStoreTheme/192") is None


def test_open_model_request_is_generated_inside_update_pipeline(tmp_path: Path, monkeypatch):
    db = tmp_path / "configurator.sqlite3"
    snapshot = {
        "version": {
            "signature": "sig", "theme_id": "191", "storefront_theme": "1",
            "engine_version": "v106", "model_version": "v83", "catalog_version": "v1",
            "model_count": 1, "page_url": "https://example/page",
            "catalog_url": "https://example/assets/catalog.json",
            "engine_url": "https://example/assets/engine.js", "catalog_sha256": "a" * 64,
        },
        "models": [_record()],
    }
    save_live_snapshot(snapshot, db)
    create_model_improvement_requests(
        scope="MODEL",
        targets=[{
            "target_key": "123", "target_label": "Bandzaagmachine",
            "variant_id": "123", "sku": "OLD", "category": "Metaalbewerking",
        }],
        reason="Lijkt niet op de foto",
        path=db,
    )
    photo_model = {
        "schemaVersion": 1, "profile": "photo-test", "confidence": 0.9,
        "parts": [{"type": "box"}] * 8,
    }
    monkeypatch.setattr(
        "app.workplace_configurator_update.generate_photo_model",
        lambda record, reason, notes: photo_model,
    )
    desired, improvement = _apply_open_model_improvements([_record()], db, 77)
    assert improvement["generated"] == 1
    assert improvement["variantIds"] == ["123"]
    assert desired[0]["photoModel"] == photo_model
    assert desired[0]["modelProfile"].startswith("auto-photo-123-")
