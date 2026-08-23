from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from app.suppliers.hub import (
    REGISTRY_PATH, _connect, init_registry, init_supplier_database, utc_now,
)
from app.suppliers.on_demand_import import import_official_website_product


ACTIVE = {"queued", "running"}


def init_kentie_enrichment_jobs() -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS kentie_enrichment_jobs(
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
            """CREATE TABLE IF NOT EXISTS kentie_enrichment_items(
                job_id TEXT NOT NULL,sku TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
                images INTEGER NOT NULL DEFAULT 0,message TEXT,updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id,sku),
                FOREIGN KEY(job_id) REFERENCES kentie_enrichment_jobs(id)
            )"""
        )


def kentie_enrichment_status() -> dict[str, Any]:
    init_kentie_enrichment_jobs()
    path = init_supplier_database("kentie")
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
            "SELECT * FROM kentie_enrichment_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return {"total": total, "enriched": enriched, "job": dict(row) if row else None}


def start_kentie_enrichment(delay_seconds: float = 5) -> dict[str, Any]:
    status = kentie_enrichment_status()
    active = status.get("job") or {}
    if active.get("status") in ACTIVE:
        return active
    delay = min(60.0, max(2.0, float(delay_seconds)))
    job_id = uuid.uuid4().hex
    now = utc_now()
    path = init_supplier_database("kentie")
    with _connect(path) as connection:
        skus = [str(row[0]) for row in connection.execute(
            "SELECT sku FROM products WHERE source_present=1 ORDER BY sku"
        )]
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """INSERT INTO kentie_enrichment_jobs(
               id,status,total,delay_seconds,created_at,updated_at
               ) VALUES(?,'queued',?,?,?,?)""",
            (job_id, len(skus), delay, now, now),
        )
        connection.executemany(
            """INSERT INTO kentie_enrichment_items(job_id,sku,status,updated_at)
               VALUES(?,?,'pending',?)""",
            [(job_id, sku, now) for sku in skus],
        )
    project_dir = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_dir)
    process = subprocess.Popen(
        [sys.executable, "-m", "app.suppliers.kentie_bulk_enrichment", job_id],
        cwd=project_dir, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            "UPDATE kentie_enrichment_jobs SET pid=?,updated_at=? WHERE id=?",
            (process.pid, utc_now(), job_id),
        )
    return {"id": job_id, "status": "queued", "pid": process.pid}


def resume_kentie_enrichment() -> dict[str, Any]:
    """Resume the latest interrupted job without rebuilding its item list."""
    init_kentie_enrichment_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            "SELECT * FROM kentie_enrichment_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not job:
            return start_kentie_enrichment(5)
        old_pid = int(job["pid"] or 0)
        process_alive = False
        if old_pid > 0:
            try:
                os.kill(old_pid, 0)
                process_alive = True
            except ProcessLookupError:
                pass
        if job["status"] in ACTIVE and process_alive:
            return dict(job)
        remaining = int(connection.execute(
            """SELECT COUNT(*) FROM kentie_enrichment_items
               WHERE job_id=? AND status='pending'""", (job["id"],)
        ).fetchone()[0])
        if remaining == 0:
            return dict(job)
        connection.execute(
            """UPDATE kentie_enrichment_jobs SET status='queued',pid=NULL,
               current_sku=NULL,message=?,error=NULL,finished_at=NULL,updated_at=?
               WHERE id=?""",
            (f"Hervatten met {remaining} resterende artikelen", utc_now(), job["id"]),
        )
        job_id = str(job["id"])
    project_dir = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_dir)
    process = subprocess.Popen(
        [sys.executable, "-m", "app.suppliers.kentie_bulk_enrichment", job_id],
        cwd=project_dir, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            "UPDATE kentie_enrichment_jobs SET pid=?,updated_at=? WHERE id=?",
            (process.pid, utc_now(), job_id),
        )
    return {"id": job_id, "status": "queued", "pid": process.pid,
            "remaining": remaining}


def stop_kentie_enrichment() -> dict[str, Any]:
    init_kentie_enrichment_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            "SELECT * FROM kentie_enrichment_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not job or job["status"] not in ACTIVE:
            return dict(job) if job else {"status": "idle"}
        pid = int(job["pid"] or 0)
        if pid > 0:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        connection.execute(
            """UPDATE kentie_enrichment_jobs SET status='paused',pid=NULL,
               current_sku=NULL,message='Gepauzeerd door gebruiker',
               finished_at=NULL,updated_at=? WHERE id=?""",
            (utc_now(), job["id"]),
        )
        return {**dict(job), "status": "paused", "pid": None}


def restart_kentie_enrichment(delay_seconds: float = 5) -> dict[str, Any]:
    status = kentie_enrichment_status().get("job") or {}
    if status.get("status") in ACTIVE:
        raise ValueError("Stop de actieve Kentie-run eerst.")
    return start_kentie_enrichment(delay_seconds)


def run_kentie_enrichment(job_id: str) -> None:
    init_kentie_enrichment_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            "SELECT * FROM kentie_enrichment_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not job:
            return
        delay = float(job["delay_seconds"] or 5)
        connection.execute(
            """UPDATE kentie_enrichment_jobs SET status='running',started_at=?,
               message='Kentie-catalogus wordt rustig verrijkt',updated_at=? WHERE id=?""",
            (utc_now(), utc_now(), job_id),
        )
        items = connection.execute(
            """SELECT sku FROM kentie_enrichment_items
               WHERE job_id=? AND status='pending' ORDER BY sku""", (job_id,)
        ).fetchall()
    completed = int(job["completed"] or 0)
    enriched = int(job["enriched"] or 0)
    not_found = int(job["not_found"] or 0)
    failed = int(job["failed"] or 0)
    images = int(job["found_images"] or 0)
    for item in items:
        sku = str(item["sku"])
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE kentie_enrichment_jobs SET current_sku=?,message=?,updated_at=?
                   WHERE id=?""", (sku, f"Kentie {sku} onderzoeken", utc_now(), job_id)
            )
        try:
            result = import_official_website_product(
                "kentie", sku, execution_context="bulk_enrichment"
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
                """UPDATE kentie_enrichment_items SET status=?,images=?,message=?,updated_at=?
                   WHERE job_id=? AND sku=?""",
                (item_status, item_images, message[:2000], utc_now(), job_id, sku),
            )
            connection.execute(
                """UPDATE kentie_enrichment_jobs SET completed=?,enriched=?,not_found=?,
                   failed=?,found_images=?,error=?,updated_at=? WHERE id=?""",
                (completed, enriched, not_found, failed, images,
                 message[:2000] if item_status == "failed" else None, utc_now(), job_id),
            )
        time.sleep(delay)
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """UPDATE kentie_enrichment_jobs SET status=?,pid=NULL,current_sku=NULL,message=?,
               finished_at=?,updated_at=? WHERE id=?""",
            ("completed_with_errors" if failed else "completed",
             f"Klaar: {enriched} verrijkt, {not_found} niet gevonden, {failed} fouten, {images} foto's.",
             utc_now(), utc_now(), job_id),
        )


if __name__ == "__main__" and len(sys.argv) == 2:
    run_kentie_enrichment(sys.argv[1])
