from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from app.suppliers.hub import (
    REGISTRY_PATH,
    _connect,
    init_registry,
    init_supplier_database,
    utc_now,
)
from app.suppliers.on_demand_import import import_official_website_product


ACTIVE = {"queued", "running"}
_SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def init_enrichment_recovery_jobs() -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS supplier_enrichment_recovery_jobs(
                id TEXT PRIMARY KEY,supplier_slug TEXT NOT NULL,status TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,
                found_images INTEGER NOT NULL DEFAULT 0,current_sku TEXT,message TEXT,
                error TEXT,pid INTEGER,delay_seconds REAL NOT NULL DEFAULT 5,
                source_job_id TEXT,created_at TEXT NOT NULL,started_at TEXT,
                finished_at TEXT,updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS supplier_enrichment_recovery_items(
                job_id TEXT NOT NULL,sku TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
                images INTEGER NOT NULL DEFAULT 0,message TEXT,updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id,sku),
                FOREIGN KEY(job_id) REFERENCES supplier_enrichment_recovery_jobs(id)
            )"""
        )


def _legacy_tables(connection: Any, slug: str) -> tuple[str, str] | None:
    if not _SAFE_SLUG.fullmatch(slug):
        return None
    jobs = f"{slug}_enrichment_jobs"
    items = f"{slug}_enrichment_items"
    present = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)",
            (jobs, items),
        )
    }
    return (jobs, items) if present == {jobs, items} else None


def enrichment_recovery_status(slug: str) -> dict[str, Any]:
    init_enrichment_recovery_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            """SELECT * FROM supplier_enrichment_recovery_jobs
               WHERE supplier_slug=? ORDER BY created_at DESC LIMIT 1""",
            (slug,),
        ).fetchone()
        tables = _legacy_tables(connection, slug)
        recoverable = 0
        source_job_id = None
        source_job_status = None
        if tables:
            jobs, items = tables
            source = connection.execute(
                f"SELECT id,status FROM {jobs} ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if source:
                source_job_id = str(source["id"])
                source_job_status = str(source["status"])
                recoverable = int(connection.execute(
                    f"""SELECT COUNT(*) FROM {items}
                         WHERE job_id=? AND status IN ('failed','pending')""",
                    (source_job_id,),
                ).fetchone()[0])
    return {
        "recoverable": recoverable,
        "source_job_id": source_job_id,
        "source_job_status": source_job_status,
        "job": dict(job) if job else None,
        "supported": tables is not None,
    }


def _spawn(job_id: str, slug: str) -> dict[str, Any]:
    project_dir = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_dir)
    process = subprocess.Popen(
        [sys.executable, "-m", "app.suppliers.enrichment_recovery", slug, job_id],
        cwd=project_dir,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """UPDATE supplier_enrichment_recovery_jobs
               SET pid=?,updated_at=? WHERE id=? AND supplier_slug=?""",
            (process.pid, utc_now(), job_id, slug),
        )
    return {"id": job_id, "supplier_slug": slug, "status": "queued", "pid": process.pid}


def start_enrichment_recovery(slug: str, delay_seconds: float = 5) -> dict[str, Any]:
    status = enrichment_recovery_status(slug)
    active = status.get("job") or {}
    if active.get("status") in ACTIVE:
        return active
    if not status["supported"]:
        raise ValueError(f"{slug} heeft nog geen herstelbare verrijkingshistorie.")
    source_job_id = status.get("source_job_id")
    if not source_job_id or not status["recoverable"]:
        raise ValueError("Er zijn geen mislukte of onafgeronde artikelen gevonden.")
    delay = min(60.0, max(2.0, float(delay_seconds)))
    job_id = uuid.uuid4().hex
    now = utc_now()
    with _connect(REGISTRY_PATH) as connection:
        tables = _legacy_tables(connection, slug)
        if not tables:
            raise ValueError(f"{slug} heeft geen verrijkingshistorie.")
        _, items = tables
        skus = [
            str(row[0]) for row in connection.execute(
                f"""SELECT sku FROM {items}
                     WHERE job_id=? AND status IN ('failed','pending') ORDER BY sku""",
                (source_job_id,),
            )
        ]
        connection.execute(
            """INSERT INTO supplier_enrichment_recovery_jobs(
               id,supplier_slug,status,total,delay_seconds,source_job_id,
               message,created_at,updated_at)
               VALUES(?,?,'queued',?,?,?,?,?,?)""",
            (
                job_id, slug, len(skus), delay, source_job_id,
                f"{len(skus)} mislukte of onafgeronde artikelen gevonden",
                now, now,
            ),
        )
        connection.executemany(
            """INSERT INTO supplier_enrichment_recovery_items(
               job_id,sku,status,updated_at) VALUES(?,?,'pending',?)""",
            [(job_id, sku, now) for sku in skus],
        )
    return _spawn(job_id, slug)


def stop_enrichment_recovery(slug: str) -> dict[str, Any]:
    init_enrichment_recovery_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            """SELECT * FROM supplier_enrichment_recovery_jobs
               WHERE supplier_slug=? ORDER BY created_at DESC LIMIT 1""",
            (slug,),
        ).fetchone()
        if not job or job["status"] not in ACTIVE:
            return dict(job) if job else {"status": "idle"}
        pid = int(job["pid"] or 0)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        connection.execute(
            """UPDATE supplier_enrichment_recovery_jobs SET status='paused',pid=NULL,
               current_sku=NULL,message='Herstel gepauzeerd',updated_at=?
               WHERE id=? AND supplier_slug=?""",
            (utc_now(), job["id"], slug),
        )
    return {**dict(job), "status": "paused", "pid": None}


def resume_enrichment_recovery(slug: str) -> dict[str, Any]:
    init_enrichment_recovery_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            """SELECT * FROM supplier_enrichment_recovery_jobs
               WHERE supplier_slug=? ORDER BY created_at DESC LIMIT 1""",
            (slug,),
        ).fetchone()
        if not job:
            return start_enrichment_recovery(slug)
        if job["status"] in ACTIVE:
            return dict(job)
        remaining = int(connection.execute(
            """SELECT COUNT(*) FROM supplier_enrichment_recovery_items
               WHERE job_id=? AND status='pending'""",
            (job["id"],),
        ).fetchone()[0])
        if not remaining:
            raise ValueError("Deze hersteljob heeft geen resterende artikelen.")
        connection.execute(
            """UPDATE supplier_enrichment_recovery_jobs SET status='queued',pid=NULL,
               message=?,error=NULL,finished_at=NULL,updated_at=?
               WHERE id=? AND supplier_slug=?""",
            (f"Herstel hervatten met {remaining} artikelen", utc_now(), job["id"], slug),
        )
        job_id = str(job["id"])
    return _spawn(job_id, slug)


def run_enrichment_recovery(slug: str, job_id: str) -> None:
    init_enrichment_recovery_jobs()
    init_supplier_database(slug)
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            """SELECT * FROM supplier_enrichment_recovery_jobs
               WHERE id=? AND supplier_slug=?""",
            (job_id, slug),
        ).fetchone()
        if not job:
            return
        connection.execute(
            """UPDATE supplier_enrichment_recovery_jobs SET status='running',
               started_at=COALESCE(started_at,?),message=?,updated_at=?
               WHERE id=? AND supplier_slug=?""",
            (utc_now(), f"{slug}: herstel wordt uitgevoerd", utc_now(), job_id, slug),
        )
        items = connection.execute(
            """SELECT sku FROM supplier_enrichment_recovery_items
               WHERE job_id=? AND status='pending' ORDER BY sku""",
            (job_id,),
        ).fetchall()
    completed = int(job["completed"] or 0)
    succeeded = int(job["succeeded"] or 0)
    failed = int(job["failed"] or 0)
    found_images = int(job["found_images"] or 0)
    delay = float(job["delay_seconds"] or 5)
    for item in items:
        sku = str(item["sku"])
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE supplier_enrichment_recovery_jobs
                   SET current_sku=?,message=?,updated_at=?
                   WHERE id=? AND supplier_slug=?""",
                (sku, f"{slug} {sku} opnieuw onderzoeken", utc_now(), job_id, slug),
            )
        try:
            result = import_official_website_product(
                slug, sku, execution_context="bulk_enrichment"
            )
            images = int(result.get("images") or 0)
            item_status = "succeeded"
            message = str(result.get("enrichment_message") or result.get("source_url") or "")
            succeeded += 1
            found_images += images
        except Exception as exc:
            images = 0
            item_status = "failed"
            message = str(exc)
            failed += 1
        completed += 1
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE supplier_enrichment_recovery_items
                   SET status=?,images=?,message=?,updated_at=? WHERE job_id=? AND sku=?""",
                (item_status, images, message[:2000], utc_now(), job_id, sku),
            )
            connection.execute(
                """UPDATE supplier_enrichment_recovery_jobs SET completed=?,succeeded=?,
                   failed=?,found_images=?,error=?,updated_at=?
                   WHERE id=? AND supplier_slug=?""",
                (
                    completed, succeeded, failed, found_images,
                    message[:2000] if item_status == "failed" else None,
                    utc_now(), job_id, slug,
                ),
            )
        time.sleep(delay)
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """UPDATE supplier_enrichment_recovery_jobs SET status=?,pid=NULL,
               current_sku=NULL,message=?,finished_at=?,updated_at=?
               WHERE id=? AND supplier_slug=?""",
            (
                "completed_with_errors" if failed else "completed",
                f"Herstel klaar: {succeeded} gelukt, {failed} mislukt, {found_images} foto's.",
                utc_now(), utc_now(), job_id, slug,
            ),
        )


if __name__ == "__main__" and len(sys.argv) == 3:
    run_enrichment_recovery(sys.argv[1], sys.argv[2])
