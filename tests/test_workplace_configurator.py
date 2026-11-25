from pathlib import Path

from app.workplace_configurator import (
    _candidate_score, create_model_improvement_requests, current_version,
    model_improvement_bundle, model_improvement_bundle_bytes,
    model_export_frame, model_export_xlsx, model_improvement_requests,
    model_json_bytes, models_json_bytes, save_live_snapshot, summary_counts,
    validate_model_improvement_bundle,
    version_history, version_models,
)


def _snapshot(signature: str = "sig-1"):
    return {
        "version": {
            "signature": signature,
            "theme_id": "191698305353",
            "storefront_theme": "199",
            "engine_version": "v104",
            "model_version": "v81",
            "catalog_version": "v1",
            "model_count": 1,
            "page_url": "https://www.weldingshop.nl/pages/3d-werkplaatsconfigurator",
            "catalog_url": "https://cdn.example/catalog-v1.json",
            "engine_url": "https://cdn.example/engine-v104.js",
            "catalog_sha256": "a" * 64,
        },
        "models": [{
            "variantId": "123", "code": "TEST-1", "title": "Bandzaagmachine",
            "handle": "bandzaagmachine", "url": "https://example/products/bandzaagmachine",
            "image": "https://example/image.jpg", "series": "equipment",
            "category": "Metaalbewerking", "modelKind": "bandsaw",
            "appearance": "bandsaw_test", "mount": "floor", "width": 50,
            "depth": 60, "height": 120, "available": True,
        }],
    }


def test_snapshot_is_versioned_and_models_are_exportable(tmp_path: Path, monkeypatch):
    db = tmp_path / "configurator.sqlite3"
    monkeypatch.setattr("app.workplace_configurator._theme_live_date", lambda _theme: "2026-11-16T12:00:00+01:00")
    first = save_live_snapshot(_snapshot(), db)
    second = save_live_snapshot(_snapshot(), db)
    assert first == second
    version = current_version(db)
    models = version_models(first, db)
    assert version["model_count"] == 1
    assert models[0]["sku"] == "TEST-1"
    assert len(version_history(db)) == 1
    frame = model_export_frame(models, version)
    assert frame.iloc[0]["3D-modeltype"] == "bandsaw"
    assert "v104" in frame.iloc[0]["Productieversie"]
    assert model_export_xlsx(frame).startswith(b"PK")
    assert b'"variantId": "123"' in model_json_bytes(models[0])
    assert b'"variantId": "123"' in models_json_bytes(models)
    assert summary_counts(models)["all_categories"] == [("Metaalbewerking", 1)]


def test_candidate_scoring_explains_placeable_and_accessory_products():
    strong, reasons = _candidate_score({
        "title": "Industriële bandzaagmachine", "productType": "Metaalbewerking", "tags": []
    })
    accessory, accessory_reasons = _candidate_score({
        "title": "Bandzaagmachine zaagblad", "productType": "Accessoire", "tags": []
    })
    assert strong >= 5
    assert reasons
    assert accessory < strong
    assert any("accessoire" in reason.casefold() for reason in accessory_reasons)


def test_mounting_parts_are_not_reported_as_new_models():
    score, reasons = _candidate_score({
        "title": "Verbindings- en eindstuk werkplaatsmodule STARX",
        "productType": "Werkplaatskast",
        "tags": [],
    })
    assert score < 2
    assert any("montagemateriaal" in reason.casefold() for reason in reasons)


def test_model_improvement_requests_are_durable_and_not_duplicated(tmp_path: Path, monkeypatch):
    db = tmp_path / "configurator.sqlite3"
    monkeypatch.setattr(
        "app.workplace_configurator._theme_live_date",
        lambda _theme: "2026-11-18T12:00:00+01:00",
    )
    version_id = save_live_snapshot(_snapshot(), db)
    version = current_version(db)

    created = create_model_improvement_requests(
        scope="MODEL",
        targets=[{
            "target_key": "123",
            "target_label": "Bandzaagmachine",
            "variant_id": "123",
            "sku": "TEST-1",
            "category": "Metaalbewerking",
        }],
        reason="3D-model lijkt niet op de productfoto",
        notes="Zaagarm en onderstel wijken af.",
        version=version,
        path=db,
    )
    assert len(created) == 1
    assert created[0]["version_id"] == version_id
    assert created[0]["status"] == "OPEN"
    assert created[0]["theme_id"] == "191698305353"

    duplicate = create_model_improvement_requests(
        scope="MODEL",
        targets=[{
            "target_key": "123",
            "target_label": "Bandzaagmachine",
            "variant_id": "123",
            "sku": "TEST-1",
            "category": "Metaalbewerking",
        }],
        reason="Kleur of materiaal klopt niet",
        version=version,
        path=db,
    )
    assert duplicate == []
    open_rows = model_improvement_requests(status="OPEN", path=db)
    assert len(open_rows) == 1
    assert open_rows[0]["sku"] == "TEST-1"

    group = create_model_improvement_requests(
        scope="GROUP",
        targets=[{
            "target_key": "Metaalbewerking",
            "target_label": "Productgroep Metaalbewerking (1 modellen)",
            "category": "Metaalbewerking",
        }],
        reason="Onderdelen of details ontbreken",
        version=version,
        path=db,
    )
    assert len(group) == 1
    assert len(model_improvement_requests(status="OPEN", path=db)) == 2


def test_model_improvement_bundle_is_downloadable_and_strictly_validated(tmp_path: Path, monkeypatch):
    db = tmp_path / "configurator.sqlite3"
    monkeypatch.setattr(
        "app.workplace_configurator._theme_live_date",
        lambda _theme: "2026-11-18T12:00:00+01:00",
    )
    save_live_snapshot(_snapshot(), db)
    request = create_model_improvement_requests(
        scope="MODEL",
        targets=[{
            "target_key": "123", "target_label": "Bandzaagmachine",
            "variant_id": "123", "sku": "TEST-1", "category": "Metaalbewerking",
        }],
        reason="3D-model lijkt niet op de productfoto",
        path=db,
    )[0]
    bundle = model_improvement_bundle(request["id"], path=db)
    assert bundle["requestId"] == request["id"]
    assert bundle["models"][0]["variantId"] == "123"
    assert b'"requestId"' in model_improvement_bundle_bytes(request["id"], path=db)

    bundle["models"][0]["appearance"] = "bandsaw_individual_v2"
    bundle["models"][0]["title"] = "Mag niet worden overschreven"
    validated = validate_model_improvement_bundle(request["id"], bundle, path=db)
    assert validated["changedVariantIds"] == ["123"]
    assert validated["models"][0]["appearance"] == "bandsaw_individual_v2"
    assert validated["models"][0]["title"] == "Bandzaagmachine"

    bundle["models"][0]["width"] = 0
    try:
        validate_model_improvement_bundle(request["id"], bundle, path=db)
    except ValueError as exc:
        assert "niet-positieve width" in str(exc)
    else:
        raise AssertionError("Een nulmaat had moeten worden geweigerd.")
