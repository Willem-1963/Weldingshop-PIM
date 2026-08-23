from __future__ import annotations

import os
import json
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from app.suppliers.hub import (
    REGISTRY_PATH, _connect, get_supplier, import_records, init_registry,
    init_supplier_database, read_source, utc_now,
)
from app.suppliers.on_demand_import import import_official_website_product


ACTIVE = {"queued", "running"}
PAUSABLE = {"queued", "running"}


def init_tecweld_enrichment_jobs() -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS tecweld_enrichment_jobs(
                id TEXT PRIMARY KEY,status TEXT NOT NULL,total INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,enriched INTEGER NOT NULL DEFAULT 0,
                not_found INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,
                found_images INTEGER NOT NULL DEFAULT 0,current_sku TEXT,message TEXT,
                error TEXT,pid INTEGER,delay_seconds REAL NOT NULL DEFAULT 5,
                created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS tecweld_enrichment_items(
                job_id TEXT NOT NULL,sku TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
                images INTEGER NOT NULL DEFAULT 0,message TEXT,updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id,sku),
                FOREIGN KEY(job_id) REFERENCES tecweld_enrichment_jobs(id)
            )"""
        )


def tecweld_enrichment_status() -> dict[str, Any]:
    init_tecweld_enrichment_jobs()
    path = init_supplier_database("tecweld")
    with _connect(path) as connection:
        total = int(connection.execute(
            "SELECT COUNT(*) FROM products WHERE source_present=1"
        ).fetchone()[0])
        enriched = int(connection.execute(
            """SELECT COUNT(DISTINCT p.sku) FROM products p
               JOIN product_images i ON i.sku=p.sku
               WHERE p.source_present=1"""
        ).fetchone()[0])
    with _connect(REGISTRY_PATH) as connection:
        row = connection.execute(
            "SELECT * FROM tecweld_enrichment_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return {"total": total, "enriched": enriched, "job": dict(row) if row else None}


def _spawn_tecweld_job(job_id: str) -> dict[str, Any]:
    project_dir = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_dir)
    process = subprocess.Popen(
        [sys.executable, "-m", "app.suppliers.tecweld_bulk_enrichment", job_id],
        cwd=project_dir, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            "UPDATE tecweld_enrichment_jobs SET pid=?,updated_at=? WHERE id=?",
            (process.pid, utc_now(), job_id),
        )
    return {"id": job_id, "status": "queued", "pid": process.pid}


def start_tecweld_enrichment(delay_seconds: float = 5) -> dict[str, Any]:
    from app.suppliers.enrichment_profiles import enrichment_enabled
    if not enrichment_enabled("tecweld", "bulk_enrichment"):
        raise ValueError(
            "Catalogusverrijking staat uit in tab 8 Verrijkingsregels."
        )
    status = tecweld_enrichment_status()
    active = status.get("job") or {}
    if active.get("status") in ACTIVE:
        return active
    delay = min(60.0, max(2.0, float(delay_seconds)))
    job_id = uuid.uuid4().hex
    now = utc_now()
    path = init_supplier_database("tecweld")
    with _connect(path) as connection:
        skus = [str(row[0]) for row in connection.execute(
            "SELECT sku FROM products WHERE source_present=1 ORDER BY sku"
        )]
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """INSERT INTO tecweld_enrichment_jobs(
               id,status,total,delay_seconds,created_at,updated_at
               ) VALUES(?,'queued',?,?,?,?)""",
            (job_id, len(skus), delay, now, now),
        )
        connection.executemany(
            """INSERT INTO tecweld_enrichment_items(job_id,sku,status,updated_at)
               VALUES(?,?,'pending',?)""",
            [(job_id, sku, now) for sku in skus],
        )
    return _spawn_tecweld_job(job_id)


def stop_tecweld_enrichment() -> dict[str, Any]:
    init_tecweld_enrichment_jobs()
    with _connect(REGISTRY_PATH) as connection:
        row = connection.execute(
            "SELECT * FROM tecweld_enrichment_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row or row["status"] not in PAUSABLE:
            return dict(row) if row else {"status": "idle"}
        pid = int(row["pid"] or 0)
        if pid > 0:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        now = utc_now()
        connection.execute(
            """UPDATE tecweld_enrichment_jobs SET status='paused',pid=NULL,
               current_sku=NULL,message='Gepauzeerd door gebruiker',
               finished_at=NULL,updated_at=? WHERE id=?""",
            (now, row["id"]),
        )
        return {**dict(row), "status": "paused", "pid": None}


def resume_tecweld_enrichment() -> dict[str, Any]:
    init_tecweld_enrichment_jobs()
    with _connect(REGISTRY_PATH) as connection:
        row = connection.execute(
            "SELECT * FROM tecweld_enrichment_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return start_tecweld_enrichment(5)
        if row["status"] in ACTIVE:
            return dict(row)
        if row["status"] != "paused":
            raise ValueError("Alleen een gepauzeerde Tecweld-run kan worden hervat.")
        connection.execute(
            """UPDATE tecweld_enrichment_jobs SET status='queued',pid=NULL,
               message='Hervatten vanaf opgeslagen voortgang',error=NULL,
               finished_at=NULL,updated_at=? WHERE id=?""",
            (utc_now(), row["id"]),
        )
        job_id = str(row["id"])
    return _spawn_tecweld_job(job_id)


def restart_tecweld_enrichment(delay_seconds: float = 5) -> dict[str, Any]:
    status = tecweld_enrichment_status().get("job") or {}
    if status.get("status") in ACTIVE:
        raise ValueError("Stop de actieve Tecweld-run eerst.")
    return start_tecweld_enrichment(delay_seconds)


def _process_tecweld_items(
    job_id: str, items: list[Any], delay: float, *,
    completed: int = 0, enriched: int = 0, not_found: int = 0,
    failed: int = 0, images: int = 0,
) -> None:
    for item in items:
        sku = str(item["sku"])
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE tecweld_enrichment_jobs SET current_sku=?,message=?,updated_at=?
                   WHERE id=?""", (sku, f"Tecweld {sku} onderzoeken", utc_now(), job_id)
            )
        try:
            result = import_official_website_product(
                "tecweld", sku, execution_context="bulk_enrichment"
            )
            item_images = int(result.get("images") or 0)
            if result.get("enrichment_skipped"):
                item_status = "not_found"
                not_found += 1
            else:
                item_status = "enriched"
                enriched += 1
                images += item_images
            message = str(result.get("enrichment_message") or result.get("source_url") or "")
        except Exception as exc:
            item_status, item_images, message = "failed", 0, str(exc)
            failed += 1
        completed += 1
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE tecweld_enrichment_items SET status=?,images=?,message=?,updated_at=?
                   WHERE job_id=? AND sku=?""",
                (item_status, item_images, message[:2000], utc_now(), job_id, sku),
            )
            connection.execute(
                """UPDATE tecweld_enrichment_jobs SET completed=?,enriched=?,not_found=?,
                   failed=?,found_images=?,error=?,updated_at=? WHERE id=?""",
                (completed, enriched, not_found, failed, images,
                 message[:2000] if item_status == "failed" else None, utc_now(), job_id),
            )
        time.sleep(delay)
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """UPDATE tecweld_enrichment_jobs SET status=?,pid=NULL,current_sku=NULL,message=?,
               finished_at=?,updated_at=? WHERE id=?""",
            ("completed_with_errors" if failed else "completed",
             f"Klaar: {enriched} verrijkt, {not_found} niet gevonden, {failed} fouten, {images} foto's.",
             utc_now(), utc_now(), job_id),
        )


def run_tecweld_enrichment(job_id: str) -> None:
    init_tecweld_enrichment_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            "SELECT * FROM tecweld_enrichment_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not job:
            return
        resuming = str(job["message"] or "").startswith("Hervatten")
        delay = float(job["delay_seconds"] or 5)
        connection.execute(
            """UPDATE tecweld_enrichment_jobs SET status='running',started_at=?,
               message='Tecweld-catalogus wordt rustig verrijkt',updated_at=? WHERE id=?""",
            (utc_now(), utc_now(), job_id),
        )
        if resuming:
            items = connection.execute(
                """SELECT sku FROM tecweld_enrichment_items
                   WHERE job_id=? AND status='pending' ORDER BY sku""", (job_id,)
            ).fetchall()
            counters = {
                "completed": int(job["completed"] or 0),
                "enriched": int(job["enriched"] or 0),
                "not_found": int(job["not_found"] or 0),
                "failed": int(job["failed"] or 0),
                "images": int(job["found_images"] or 0),
            }
    if resuming:
        _process_tecweld_items(job_id, items, delay, **counters)
        return
    # De analyseknop bewaart een URL-bron bewust alleen in de sessie. Haal de
    # actuele Tecweld-prijslijst daarom hier opnieuw op en importeer hem vóór
    # het verrijken, zodat de werklijst alle officiële artikelnummers bevat.
    path = init_supplier_database("tecweld")
    supplier = get_supplier("tecweld", include_credentials=True)
    if not supplier:
        raise ValueError("Tecweld is niet geconfigureerd.")
    with _connect(path) as connection:
        retained_raw = {
            str(row["sku"]): json.loads(row["raw_data_json"] or "{}")
            for row in connection.execute(
                "SELECT sku,raw_data_json FROM products"
            ).fetchall()
        }
    try:
        analysis = read_source(supplier)
        imported = import_records(
            "tecweld", analysis, supplier.get("field_mapping") or None
        )
    except Exception as exc:
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE tecweld_enrichment_jobs SET status='failed',error=?,
                   message=?,finished_at=?,updated_at=? WHERE id=?""",
                (str(exc)[:2000], f"Bronimport mislukt: {exc}"[:2000],
                 utc_now(), utc_now(), job_id),
            )
        return
    # Bronimport mag eerder gemaakte NL-documenten, brononderzoek en
    # publicatie-audits van een bestaand artikel niet verwijderen.
    retained_keys = {
        "website_import", "publication", "localized_documents",
        "wholesale_pricing",
    }
    with _connect(path) as connection:
        for sku, old_raw in retained_raw.items():
            row = connection.execute(
                "SELECT raw_data_json FROM products WHERE sku=?", (sku,)
            ).fetchone()
            if not row:
                continue
            new_raw = json.loads(row["raw_data_json"] or "{}")
            changed = False
            for key in retained_keys:
                if key in old_raw:
                    new_raw[key] = old_raw[key]
                    changed = True
            if changed:
                connection.execute(
                    "UPDATE products SET raw_data_json=?,updated_at=? WHERE sku=?",
                    (json.dumps(new_raw, ensure_ascii=False), utc_now(), sku),
                )
        skus = [str(row[0]) for row in connection.execute(
            "SELECT sku FROM products WHERE source_present=1 ORDER BY sku"
        )]
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            "DELETE FROM tecweld_enrichment_items WHERE job_id=?", (job_id,)
        )
        connection.executemany(
            """INSERT INTO tecweld_enrichment_items(job_id,sku,status,updated_at)
               VALUES(?,?,'pending',?)""",
            [(job_id, sku, utc_now()) for sku in skus],
        )
        connection.execute(
            """UPDATE tecweld_enrichment_jobs SET total=?,completed=0,
               message=?,updated_at=? WHERE id=?""",
            (
                len(skus),
                f"{imported['seen']} Tecweld-bronartikelen ingelezen; verrijking start",
                utc_now(), job_id,
            ),
        )
        items = connection.execute(
            """SELECT sku FROM tecweld_enrichment_items
               WHERE job_id=? AND status='pending' ORDER BY sku""", (job_id,)
        ).fetchall()
    _process_tecweld_items(job_id, items, delay)


if __name__ == "__main__" and len(sys.argv) == 2:
    run_tecweld_enrichment(sys.argv[1])
