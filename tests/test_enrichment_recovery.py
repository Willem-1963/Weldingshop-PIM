import sqlite3

from app.suppliers import enrichment_recovery as recovery


def _connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_recovery_copies_only_latest_failed_and_pending(monkeypatch, tmp_path):
    registry = tmp_path / "registry.sqlite"
    monkeypatch.setattr(recovery, "REGISTRY_PATH", registry)
    monkeypatch.setattr(recovery, "init_registry", lambda: None)
    monkeypatch.setattr(recovery, "_connect", _connect)
    monkeypatch.setattr(
        recovery, "_spawn",
        lambda job_id, slug: {"id": job_id, "supplier_slug": slug, "status": "queued"},
    )
    with _connect(registry) as connection:
        connection.executescript(
            """
            CREATE TABLE kentie_enrichment_jobs(
                id TEXT PRIMARY KEY,status TEXT,created_at TEXT
            );
            CREATE TABLE kentie_enrichment_items(
                job_id TEXT,sku TEXT,status TEXT
            );
            CREATE TABLE tecweld_enrichment_jobs(
                id TEXT PRIMARY KEY,status TEXT,created_at TEXT
            );
            CREATE TABLE tecweld_enrichment_items(
                job_id TEXT,sku TEXT,status TEXT
            );
            INSERT INTO kentie_enrichment_jobs VALUES('old','completed','2026-01-01');
            INSERT INTO kentie_enrichment_jobs VALUES('latest','completed_with_errors','2026-01-02');
            INSERT INTO kentie_enrichment_items VALUES('old','OLD-FAILED','failed');
            INSERT INTO kentie_enrichment_items VALUES('latest','OK','enriched');
            INSERT INTO kentie_enrichment_items VALUES('latest','RETRY-1','failed');
            INSERT INTO kentie_enrichment_items VALUES('latest','RETRY-2','pending');
            INSERT INTO kentie_enrichment_items VALUES('latest','NOT-FOUND','not_found');
            INSERT INTO tecweld_enrichment_jobs VALUES('tec','completed_with_errors','2026-01-03');
            INSERT INTO tecweld_enrichment_items VALUES('tec','TEC-FAILED','failed');
            """
        )

    status = recovery.enrichment_recovery_status("kentie")
    assert status["recoverable"] == 2
    assert status["source_job_id"] == "latest"

    result = recovery.start_enrichment_recovery("kentie", 5)
    assert result["supplier_slug"] == "kentie"
    with _connect(registry) as connection:
        job = connection.execute(
            "SELECT * FROM supplier_enrichment_recovery_jobs WHERE id=?",
            (result["id"],),
        ).fetchone()
        skus = [
            row[0] for row in connection.execute(
                "SELECT sku FROM supplier_enrichment_recovery_items WHERE job_id=? ORDER BY sku",
                (result["id"],),
            )
        ]
        tecweld = connection.execute(
            "SELECT status FROM tecweld_enrichment_items WHERE sku='TEC-FAILED'"
        ).fetchone()[0]
    assert job["supplier_slug"] == "kentie"
    assert job["source_job_id"] == "latest"
    assert skus == ["RETRY-1", "RETRY-2"]
    assert tecweld == "failed"


def test_recovery_is_unavailable_without_supplier_item_history(monkeypatch, tmp_path):
    registry = tmp_path / "registry.sqlite"
    monkeypatch.setattr(recovery, "REGISTRY_PATH", registry)
    monkeypatch.setattr(recovery, "init_registry", lambda: None)
    monkeypatch.setattr(recovery, "_connect", _connect)

    status = recovery.enrichment_recovery_status("supplier-without-history")

    assert status["supported"] is False
    assert status["recoverable"] == 0
