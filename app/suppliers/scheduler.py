from __future__ import annotations

import calendar
from email.message import EmailMessage
import json
import os
import re
import signal
import smtplib
import ssl
import subprocess
import sys
import time as time_module
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from app.suppliers.discounts import (
    apply_purchase_costs, apply_sales_prices, list_discount_rules,
    list_sales_price_rules,
)
from app.suppliers.tags import refresh_product_tags
from app.suppliers.complementary_products import synchronize_complementary_products
from app.shopify.client import get_shopify_settings
from app.shopify.sync import initialize_sync_baseline, sync_all_products
from app.suppliers.hub import (
    REGISTRY_PATH,
    _connect,
    get_supplier,
    import_records,
    init_registry,
    read_source,
    supplier_stats,
    utc_now,
)


DEFAULT_TIMEZONE = "Europe/Amsterdam"
FREQUENCIES = {
    "daily": "Dagelijks",
    "weekly": "Wekelijks",
    "monthly": "Maandelijks",
}
PROJECT_DIR = Path(__file__).resolve().parents[2]
ACTIVE_JOB_STATUSES = ("queued", "running", "cancel_requested")
SYNC_ERROR_RECIPIENT = "mail@weldingshop.nl"
SYNC_ERROR_SUBJECT = "PIM synchronisatie fout"
SMTP_HOST = "www49.totaalholding.nl"
SMTP_PORT = 465
SMTP_AUTHINFO_PATH = Path("/etc/mail/authinfo")


class SyncCancelled(RuntimeError):
    pass


def _smtp_credentials() -> tuple[str, str]:
    """Read the existing, access-controlled Sendmail credentials."""
    content = SMTP_AUTHINFO_PATH.read_text(encoding="utf-8")
    match = re.search(
        r'^AuthInfo:(?:\[)?www49\.totaalholding\.nl(?:\])?\s+'
        r'.*?"I:([^"]+)"\s+"P:([^"]+)"',
        content,
        flags=re.MULTILINE,
    )
    if not match:
        raise RuntimeError("SMTP-inloggegevens ontbreken in /etc/mail/authinfo")
    return match.group(1), match.group(2)


def _send_sync_failure_email(
    *,
    supplier: dict,
    job_id: str,
    error: Exception,
    progress: int,
    started_at: str,
    finished_at: str,
    scheduled: bool,
) -> None:
    """Send one best-effort notification for a sync job that ended as failed."""
    supplier_name = (
        supplier.get("name") or supplier.get("display_name")
        or supplier.get("slug") or "Onbekende leverancier"
    )
    run_type = "Gepland" if scheduled else "Handmatig"
    message = EmailMessage()
    message["From"] = f"Willem Bangma - PIM manager <{SYNC_ERROR_RECIPIENT}>"
    message["To"] = SYNC_ERROR_RECIPIENT
    message["Subject"] = SYNC_ERROR_SUBJECT
    message.set_content(
        f"""Beste,

De PIM-synchronisatie voor {supplier_name} is niet voltooid.

Leverancier: {supplier_name}
Type synchronisatie: {run_type}
Voortgang bij stoppen: {progress}%
Gestart: {started_at}
Mislukt: {finished_at}
Job-ID: {job_id}

Foutmelding:
{error}

De fout is vastgelegd in de synchronisatiehistorie. Controleer de oorzaak voordat de synchronisatie opnieuw wordt gestart.

Vriendelijke groet,

Willem Bangma
Uw PIM manager
"""
    )
    try:
        username, password = _smtp_credentials()
        with smtplib.SMTP_SSL(
            SMTP_HOST,
            SMTP_PORT,
            timeout=30,
            context=ssl.create_default_context(),
        ) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    except Exception as mail_error:
        print(
            f"Foutmail voor synchronisatie {job_id} kon niet worden verzonden: "
            f"{mail_error}",
            file=sys.stderr,
            flush=True,
        )


def init_schedule_columns() -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(suppliers)").fetchall()
        }
        additions = {
            "sync_enabled": "INTEGER NOT NULL DEFAULT 0",
            "sync_frequency": "TEXT NOT NULL DEFAULT 'daily'",
            "sync_time": "TEXT NOT NULL DEFAULT '02:00'",
            "sync_start_date": "TEXT",
            "sync_timezone": f"TEXT NOT NULL DEFAULT '{DEFAULT_TIMEZONE}'",
            "next_sync_at": "TEXT",
            "sync_product_status": "TEXT NOT NULL DEFAULT 'active'",
            "sync_publish_all": "INTEGER NOT NULL DEFAULT 1",
            "sync_new_product_policy": (
                "TEXT NOT NULL DEFAULT 'existing_only'"
            ),
            "sync_changed_only": "INTEGER NOT NULL DEFAULT 0",
            "shopify_location_id": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE suppliers ADD COLUMN {name} {definition}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_jobs (
                id TEXT PRIMARY KEY,
                supplier_slug TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                result_json TEXT,
                error TEXT,
                pid INTEGER,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                duration_seconds REAL,
                updated_at TEXT NOT NULL
            )
            """
        )
        job_columns = {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(sync_jobs)"
            ).fetchall()
        }
        if "duration_seconds" not in job_columns:
            conn.execute(
                "ALTER TABLE sync_jobs ADD COLUMN duration_seconds REAL"
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sync_jobs_supplier_created
            ON sync_jobs(supplier_slug, created_at DESC)
            """
        )
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).replace(microsecond=0).isoformat()
        conn.execute(
            "DELETE FROM sync_jobs WHERE created_at<?", (cutoff,)
        )


def _process_is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Het proces bestaat, maar draait onder een andere systeemgebruiker.
        # Dat is geen bewijs dat de achtergrondtaak gestopt is.
        return True
    return True


def sync_job_duration_seconds(
    job: dict, now: datetime | None = None,
) -> float | None:
    """Return persisted duration or calculate elapsed runtime for old/live jobs."""
    stored = job.get("duration_seconds")
    if stored is not None:
        return max(0.0, float(stored))
    started_at = job.get("started_at")
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(str(started_at))
        finished = (
            datetime.fromisoformat(str(job["finished_at"]))
            if job.get("finished_at") else (now or datetime.now(timezone.utc))
        )
    except (TypeError, ValueError):
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return max(0.0, (finished - started).total_seconds())


def format_sync_job_duration(job: dict) -> str:
    seconds = sync_job_duration_seconds(job)
    if seconds is None:
        return "—"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def _fail_stale_active_jobs(conn, slug: str) -> int:
    """Close dead active rows so they can never block a retry."""
    rows = conn.execute(
        """
        SELECT * FROM sync_jobs
        WHERE supplier_slug=? AND status IN ('queued','running','cancel_requested')
        ORDER BY created_at DESC
        """,
        (slug,),
    ).fetchall()
    failed = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        job = dict(row)
        created_at = datetime.fromisoformat(job["created_at"])
        queued_without_pid = (
            not job.get("pid")
            and (now - created_at).total_seconds() <= 30
        )
        if queued_without_pid or _process_is_running(job.get("pid")):
            continue
        message = (
            "De achtergrondtaak is onverwacht gestopt. "
            "De synchronisatie kan opnieuw worden gestart."
        )
        finished_at = utc_now()
        duration = sync_job_duration_seconds({
            **job, "finished_at": finished_at,
        })
        conn.execute(
            """
            UPDATE sync_jobs SET status='failed',error=?,message=?,
                finished_at=?,duration_seconds=?,updated_at=? WHERE id=?
            """,
            (
                message, message, finished_at, duration,
                finished_at, job["id"],
            ),
        )
        _send_sync_failure_email(
            supplier=get_supplier(slug) or {"slug": slug},
            job_id=job["id"],
            error=RuntimeError(message),
            progress=int(job.get("progress") or 0),
            started_at=str(job.get("started_at") or job.get("created_at") or ""),
            finished_at=finished_at,
            scheduled=str(job.get("message") or "").startswith("Geplande"),
        )
        failed += 1
    return failed


def get_background_sync(slug: str) -> dict | None:
    init_schedule_columns()
    with _connect(REGISTRY_PATH) as conn:
        _fail_stale_active_jobs(conn, slug)
        row = conn.execute(
            """
            SELECT * FROM sync_jobs
            WHERE supplier_slug=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (slug,),
        ).fetchone()
        if not row:
            return None
        job = dict(row)
        if job.get("result_json"):
            try:
                job["result"] = json.loads(job["result_json"])
            except json.JSONDecodeError:
                job["result"] = None
        return job


def list_sync_history(slug: str, days: int = 31) -> list[dict]:
    init_schedule_columns()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 31)))
    ).replace(microsecond=0).isoformat()
    with _connect(REGISTRY_PATH) as conn:
        rows = conn.execute(
            """
            SELECT * FROM sync_jobs
            WHERE supplier_slug=? AND created_at>=?
            ORDER BY created_at DESC
            """,
            (slug, cutoff),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["result"] = json.loads(item.get("result_json") or "{}")
        except json.JSONDecodeError:
            item["result"] = {}
        result.append(item)
    return result


def start_background_sync(slug: str) -> dict:
    if not get_supplier(slug):
        raise ValueError(f"Onbekende leverancier: {slug}")
    init_schedule_columns()
    with _connect(REGISTRY_PATH) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _fail_stale_active_jobs(conn, slug)
        active = conn.execute(
            """
            SELECT * FROM sync_jobs
            WHERE supplier_slug=? AND status IN ('queued','running','cancel_requested')
            ORDER BY created_at DESC LIMIT 1
            """,
            (slug,),
        ).fetchone()
        if active:
            return dict(active)
        job_id = uuid.uuid4().hex
        now = utc_now()
        conn.execute(
            """
            INSERT INTO sync_jobs(
                id,supplier_slug,status,progress,message,created_at,updated_at
            ) VALUES(?,?,'queued',0,?,?,?)
            """,
            (job_id, slug, "Synchronisatie staat in de wachtrij…", now, now),
        )
    environment = os.environ.copy()
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{PROJECT_DIR}{os.pathsep}{existing_path}"
        if existing_path else str(PROJECT_DIR)
    )
    try:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "app.suppliers.scheduler",
                "--background-job", job_id,
            ],
            cwd=PROJECT_DIR,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as exc:
        failed_at = utc_now()
        with _connect(REGISTRY_PATH) as conn:
            conn.execute(
                """
                UPDATE sync_jobs SET status='failed',error=?,message=?,
                    finished_at=?,updated_at=? WHERE id=?
                """,
                (str(exc), str(exc), failed_at, failed_at, job_id),
            )
        _send_sync_failure_email(
            supplier=get_supplier(slug) or {"slug": slug},
            job_id=job_id,
            error=exc,
            progress=0,
            started_at=now,
            finished_at=failed_at,
            scheduled=False,
        )
        raise
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            "UPDATE sync_jobs SET pid=?,updated_at=? WHERE id=?",
            (process.pid, utc_now(), job_id),
        )
    return get_background_sync(slug) or {"id": job_id, "status": "queued"}


def stop_background_sync(slug: str) -> dict:
    """Stop the latest active sync, escalating if cooperative stop stalls."""
    init_schedule_columns()
    job_id = None
    pid = None
    with _connect(REGISTRY_PATH) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM sync_jobs
            WHERE supplier_slug=? AND status IN ('queued','running','cancel_requested')
            ORDER BY created_at DESC LIMIT 1
            """,
            (slug,),
        ).fetchone()
        if not row:
            raise ValueError("Er draait geen synchronisatie om te stoppen.")
        job_id = row["id"]
        pid = int(row["pid"] or 0)
        now = utc_now()
        if row["status"] == "queued":
            conn.execute(
                """
                UPDATE sync_jobs SET status='cancelled',
                    message='Synchronisatie gestopt',finished_at=?,
                    duration_seconds=0,updated_at=?
                WHERE id=?
                """,
                (now, now, row["id"]),
            )
        elif row["status"] == "running":
            conn.execute(
                """
                UPDATE sync_jobs SET status='cancel_requested',
                    message='Synchronisatie wordt gestopt…',updated_at=?
                WHERE id=?
                """,
                (now, row["id"]),
            )

    # Workers are their own process group. Only signal a PID whose command line
    # still contains this exact job id, which protects against PID reuse.
    process_matches = False
    if pid > 0:
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            process_matches = job_id.encode() in cmdline
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            process_matches = False

    if process_matches:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            process_matches = False
        if process_matches:
            for _ in range(20):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time_module.sleep(0.1)
            else:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    finished = utc_now()
    with _connect(REGISTRY_PATH) as conn:
        current = conn.execute(
            "SELECT status,started_at FROM sync_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if current and current["status"] in ACTIVE_JOB_STATUSES:
            duration = sync_job_duration_seconds({
                "started_at": current["started_at"], "finished_at": finished,
            })
            conn.execute(
                """
                UPDATE sync_jobs SET status='cancelled',
                    message='Synchronisatie gestopt door gebruiker.',error=NULL,
                    finished_at=?,duration_seconds=?,updated_at=? WHERE id=?
                """,
                (finished, duration, finished, job_id),
            )
    return get_background_sync(slug) or dict(row)


def run_background_sync(job_id: str) -> None:
    init_schedule_columns()
    with _connect(REGISTRY_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM sync_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not row or row["status"] not in ACTIVE_JOB_STATUSES:
            return
        slug = row["supplier_slug"]
        now = utc_now()
        started_at = now
        conn.execute(
            """
            UPDATE sync_jobs SET status='running',pid=?,started_at=?,
                message='Synchronisatie wordt voorbereid…',updated_at=?
            WHERE id=?
            """,
            (os.getpid(), now, now, job_id),
        )

    def save_progress(percent: int, message: str) -> None:
        with _connect(REGISTRY_PATH) as conn:
            status_row = conn.execute(
                "SELECT status FROM sync_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if status_row and status_row["status"] == "cancel_requested":
                raise SyncCancelled("Synchronisatie gestopt door gebruiker.")
            conn.execute(
                """
                UPDATE sync_jobs SET progress=?,message=?,updated_at=?
                WHERE id=?
                """,
                (max(0, min(100, int(percent))), message, utc_now(), job_id),
            )

    try:
        result = sync_supplier_now(slug, progress_callback=save_progress)
        now = utc_now()
        duration = sync_job_duration_seconds({
            "started_at": started_at, "finished_at": now,
        })
        with _connect(REGISTRY_PATH) as conn:
            conn.execute(
                """
                UPDATE sync_jobs SET status='completed',progress=100,
                    message='Synchronisatie voltooid',result_json=?,
                    finished_at=?,duration_seconds=?,updated_at=? WHERE id=?
                """,
                (
                    json.dumps(result, ensure_ascii=False), now,
                    duration, now, job_id,
                ),
            )
    except SyncCancelled as exc:
        now = utc_now()
        duration = sync_job_duration_seconds({
            "started_at": started_at, "finished_at": now,
        })
        with _connect(REGISTRY_PATH) as conn:
            conn.execute(
                """
                UPDATE sync_jobs SET status='cancelled',message=?,error=NULL,
                    finished_at=?,duration_seconds=?,updated_at=? WHERE id=?
                """,
                (str(exc), now, duration, now, job_id),
            )
    except Exception as exc:
        now = utc_now()
        duration = sync_job_duration_seconds({
            "started_at": started_at, "finished_at": now,
        })
        with _connect(REGISTRY_PATH) as conn:
            conn.execute(
                """
                UPDATE sync_jobs SET status='failed',message=?,error=?,
                    finished_at=?,duration_seconds=?,updated_at=? WHERE id=?
                """,
                (
                    f"Synchronisatie mislukt: {exc}", str(exc), now,
                    duration, now, job_id,
                ),
            )
        supplier = get_supplier(slug) or {"slug": slug}
        _send_sync_failure_email(
            supplier=supplier,
            job_id=job_id,
            error=exc,
            progress=int((get_background_sync(slug) or {}).get("progress") or 0),
            started_at=started_at,
            finished_at=now,
            scheduled=False,
        )


def _add_month(value: datetime, target_day: int) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(target_day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def calculate_next_sync(
    *,
    frequency: str,
    start_date: str,
    sync_time: str,
    timezone_name: str = DEFAULT_TIMEZONE,
    after: datetime | None = None,
) -> str:
    if frequency not in FREQUENCIES:
        raise ValueError("Ongeldige synchronisatiefrequentie.")
    zone = ZoneInfo(timezone_name)
    after_utc = after or datetime.now(timezone.utc)
    after_local = after_utc.astimezone(zone)
    start = date.fromisoformat(start_date)
    hour, minute = [int(part) for part in sync_time.split(":", 1)]
    candidate = datetime.combine(start, time(hour, minute), tzinfo=zone)
    target_day = start.day

    while candidate <= after_local:
        if frequency == "daily":
            candidate += timedelta(days=1)
        elif frequency == "weekly":
            candidate += timedelta(days=7)
        else:
            candidate = _add_month(candidate, target_day)
    return candidate.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def save_sync_schedule(
    slug: str,
    *,
    enabled: bool,
    frequency: str,
    start_date: str,
    sync_time: str,
    timezone_name: str = DEFAULT_TIMEZONE,
    product_status: str = "active",
    publish_all: bool = True,
    new_product_policy: str = "existing_only",
    changed_only: bool = False,
    family_on_change: bool = False,
    dealer_pricelist_enabled: bool = False,
) -> str | None:
    if product_status not in ("draft", "active"):
        raise ValueError("Ongeldige Shopify-productstatus.")
    if new_product_policy not in ("existing_only", "add_complete"):
        raise ValueError("Ongeldig beleid voor nieuwe Shopify-producten.")
    init_schedule_columns()
    next_sync = (
        calculate_next_sync(
            frequency=frequency,
            start_date=start_date,
            sync_time=sync_time,
            timezone_name=timezone_name,
        )
        if enabled
        else None
    )
    with _connect(REGISTRY_PATH) as conn:
        previous = conn.execute(
            "SELECT sync_changed_only FROM suppliers WHERE slug=?",
            (slug,),
        ).fetchone()
        was_changed_only = bool(
            previous["sync_changed_only"] if previous else 0
        )
        supplier = get_supplier(slug) or {}
        request_options = dict(supplier.get("request_options") or {})
        request_options["dealer_pricelist_enabled"] = bool(
            dealer_pricelist_enabled
        )
        request_options["sync_family_on_change"] = bool(family_on_change)
        conn.execute(
            """
            UPDATE suppliers SET sync_enabled=?,sync_frequency=?,sync_time=?,
                sync_start_date=?,sync_timezone=?,next_sync_at=?,
                sync_product_status=?,sync_publish_all=?,
                sync_new_product_policy=?,sync_changed_only=?,
                request_options_json=?,updated_at=?
            WHERE slug=?
            """,
            (
                int(enabled), frequency, sync_time, start_date, timezone_name,
                next_sync, product_status, int(publish_all),
                new_product_policy, int(changed_only),
                json.dumps(request_options, ensure_ascii=False), utc_now(), slug,
            ),
        )
    if changed_only and not was_changed_only:
        initialize_sync_baseline(slug)
    return next_sync


def sync_supplier_now(
    slug: str,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict:
    from app.suppliers.routes import supplier_route

    route = supplier_route(slug)
    supplier = get_supplier(slug, include_credentials=True)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    try:
        upload_source = "upload" in supplier.get("source_type", "")
        dealer_download = None
        dealer_changed = False
        if (
            route.uses_certilas_dealer_pricelist
            and supplier.get("request_options", {}).get(
                "dealer_pricelist_enabled"
            )
        ):
            if progress_callback:
                progress_callback(5, "Actuele Certilas-dealerprijslijst downloaden…")
            from app.suppliers.certilas_portal import download_certilas_pricelist
            dealer_download = download_certilas_pricelist()
            previous_hash = supplier.get("request_options", {}).get(
                "dealer_pricelist_last_hash"
            )
            dealer_changed = dealer_download["sha256"] != previous_hash
        if upload_source and dealer_changed:
            payload = Path(dealer_download["path"]).read_bytes()
            analysis = read_source(
                supplier, payload, dealer_download["filename"]
            )
            import_result = import_records(
                slug, analysis, supplier.get("field_mapping") or None,
                progress_callback=(
                    (lambda percent, text: progress_callback(
                        10 + int(percent * 0.20), text
                    )) if progress_callback else None
                ),
            )
            analysis_format = analysis.format
            analysis_rows = analysis.row_count
            request_options = dict(supplier.get("request_options") or {})
            request_options.update({
                "dealer_pricelist_last_hash": dealer_download["sha256"],
                "dealer_pricelist_last_filename": dealer_download["filename"],
                "dealer_pricelist_last_downloaded_at": utc_now(),
            })
            with _connect(REGISTRY_PATH) as conn:
                conn.execute(
                    "UPDATE suppliers SET request_options_json=?,updated_at=? WHERE slug=?",
                    (json.dumps(request_options, ensure_ascii=False), utc_now(), slug),
                )
        elif upload_source:
            stats = supplier_stats(slug)
            if not stats.get("total"):
                raise ValueError(
                    "Er staan nog geen geïmporteerde producten in PIM. "
                    "Lees en importeer eerst het Excelbestand."
                )
            if progress_callback:
                progress_callback(
                    20,
                    f"{stats['total']} bestaande PIM-producten gereed",
                )
            analysis_format = "PIM-database"
            analysis_rows = stats["total"]
            import_result = {
                "seen": stats["total"],
                "inserted": 0,
                "updated": 0,
                "unchanged": stats["total"],
                "missing": stats.get("missing", 0),
            }
        else:
            source_progress = (
                (lambda percent, text: progress_callback(
                    int(percent * 0.30), text
                ))
                if progress_callback else None
            )
            if progress_callback:
                progress_callback(5, "Leveranciersbron ophalen…")
            analysis = read_source(supplier)
            if progress_callback:
                progress_callback(
                    20, f"{analysis.row_count} bronregels geanalyseerd"
                )
            import_result = import_records(
                slug,
                analysis,
                supplier.get("field_mapping") or None,
                progress_callback=source_progress,
            )
            analysis_format = analysis.format
            analysis_rows = analysis.row_count
        enrichment_result = None
        from app.suppliers.enrichment_profiles import enrichment_enabled, run_profile_enrichment
        enrichment_context = (
            "scheduled_sync" if enrichment_enabled(slug, "scheduled_sync")
            else "source_import" if enrichment_enabled(slug, "source_import")
            else None
        )
        if enrichment_context:
            if progress_callback:
                progress_callback(26, "Producten verrijken volgens tab 8…")
            enrichment_result = run_profile_enrichment(
                slug, enrichment_context,
                progress_callback=(
                    (lambda current, total, text: progress_callback(
                        20 + int(6 * current / max(total, 1)), text
                    )) if progress_callback else None
                ),
            )
        discount_result = None
        if list_discount_rules(slug, include_disabled=False):
            if progress_callback:
                progress_callback(27, "Kortingsregels toepassen…")
            discount_result = apply_purchase_costs(slug)
        sales_price_result = None
        refreshed_supplier = get_supplier(slug) or supplier
        if (
            list_sales_price_rules(slug, include_disabled=False)
            or (refreshed_supplier.get("request_options") or {}).get(
                "sales_price_rule_enabled"
            )
        ):
            if progress_callback:
                progress_callback(28, "Verkoopprijsregel toepassen…")
            sales_price_result = apply_sales_prices(slug)
        if progress_callback:
            progress_callback(29, "Relevante AI-tags bepalen…")
        tag_result = refresh_product_tags(slug)
        valkenpower_preparation = None
        if slug == "valkenpower":
            if progress_callback:
                progress_callback(
                    29, "Officiële breadcrumbs, productteksten en tags controleren…"
                )
            from app.suppliers.valkenpower_sync_preparation import (
                prepare_valkenpower_products_for_sync,
            )
            valkenpower_preparation = prepare_valkenpower_products_for_sync()
        shopify_result = None
        complementary_result = None
        if get_shopify_settings().get("enabled"):
            if progress_callback:
                progress_callback(30, "Shopify-synchronisatie starten…")
            shopify_result = sync_all_products(
                slug,
                progress_callback=(
                    (lambda percent, text: progress_callback(
                        30 + int(percent * 0.65), text
                    ))
                    if progress_callback else None
                ),
            )
            if progress_callback:
                progress_callback(96, "Aanverwante producten veilig synchroniseren…")
            complementary_result = synchronize_complementary_products(slug)
        if progress_callback:
            progress_callback(100, "Volledige synchronisatie voltooid")
        return {
            "ok": True,
            "format": analysis_format,
            "rows": analysis_rows,
            "import": import_result,
            "enrichment": enrichment_result,
            "discounts": discount_result,
            "sales_prices": sales_price_result,
            "tags": tag_result,
            "valkenpower_preparation": valkenpower_preparation,
            "shopify": shopify_result,
            "complementary_products": complementary_result,
            "dealer_pricelist": dealer_download,
            "dealer_pricelist_changed": dealer_changed,
        }
    except Exception as exc:
        with _connect(REGISTRY_PATH) as conn:
            conn.execute(
                """
                UPDATE suppliers SET last_run_at=?,last_run_status='error',
                    last_run_message=?,updated_at=? WHERE slug=?
                """,
                (utc_now(), str(exc), utc_now(), slug),
            )
        raise


def _schedule_next_after_run(supplier: dict) -> str:
    return calculate_next_sync(
        frequency=supplier["sync_frequency"],
        start_date=supplier["sync_start_date"],
        sync_time=supplier["sync_time"],
        timezone_name=supplier.get("sync_timezone") or DEFAULT_TIMEZONE,
    )


def run_due_syncs() -> list[dict]:
    init_schedule_columns()
    now = utc_now()
    with _connect(REGISTRY_PATH) as conn:
        due = [
            dict(row) for row in conn.execute(
                """
                SELECT * FROM suppliers
                WHERE enabled=1 AND sync_enabled=1
                  AND next_sync_at IS NOT NULL AND next_sync_at<=?
                ORDER BY next_sync_at
                """,
                (now,),
            ).fetchall()
        ]
    results = []
    for supplier in due:
        job_id = uuid.uuid4().hex
        started_at = utc_now()
        with _connect(REGISTRY_PATH) as conn:
            conn.execute(
                """
                INSERT INTO sync_jobs(
                    id,supplier_slug,status,progress,message,pid,created_at,
                    started_at,updated_at
                ) VALUES(?,?,'running',0,?,?,?,?,?)
                """,
                (
                    job_id, supplier["slug"],
                    "Geplande synchronisatie wordt voorbereid…", os.getpid(),
                    started_at, started_at, started_at,
                ),
            )

        def save_progress(percent: int, message: str) -> None:
            with _connect(REGISTRY_PATH) as conn:
                status_row = conn.execute(
                    "SELECT status FROM sync_jobs WHERE id=?", (job_id,)
                ).fetchone()
                if status_row and status_row["status"] == "cancel_requested":
                    raise SyncCancelled("Synchronisatie gestopt door gebruiker.")
                conn.execute(
                    """
                    UPDATE sync_jobs SET progress=?,message=?,updated_at=?
                    WHERE id=?
                    """,
                    (
                        max(0, min(100, int(percent))), message,
                        utc_now(), job_id,
                    ),
                )

        try:
            result = sync_supplier_now(
                supplier["slug"], progress_callback=save_progress
            )
            status = "success"
            finished_at = utc_now()
            with _connect(REGISTRY_PATH) as conn:
                conn.execute(
                    """
                    UPDATE sync_jobs SET status='completed',progress=100,
                        message='Geplande synchronisatie voltooid',result_json=?,
                        finished_at=?,updated_at=? WHERE id=?
                    """,
                    (
                        json.dumps(result, ensure_ascii=False), finished_at,
                        finished_at, job_id,
                    ),
                )
        except SyncCancelled as exc:
            result = {"ok": False, "cancelled": True}
            status = "cancelled"
            finished_at = utc_now()
            with _connect(REGISTRY_PATH) as conn:
                conn.execute(
                    """
                    UPDATE sync_jobs SET status='cancelled',message=?,error=NULL,
                        result_json=?,finished_at=?,updated_at=? WHERE id=?
                    """,
                    (
                        str(exc), json.dumps(result, ensure_ascii=False),
                        finished_at, finished_at, job_id,
                    ),
                )
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            status = "error"
            finished_at = utc_now()
            with _connect(REGISTRY_PATH) as conn:
                conn.execute(
                    """
                    UPDATE sync_jobs SET status='failed',message=?,error=?,
                        result_json=?,finished_at=?,updated_at=? WHERE id=?
                    """,
                    (
                        f"Geplande synchronisatie mislukt: {exc}", str(exc),
                        json.dumps(result, ensure_ascii=False), finished_at,
                        finished_at, job_id,
                    ),
                )
            with _connect(REGISTRY_PATH) as conn:
                progress_row = conn.execute(
                    "SELECT progress FROM sync_jobs WHERE id=?", (job_id,)
                ).fetchone()
            _send_sync_failure_email(
                supplier=supplier,
                job_id=job_id,
                error=exc,
                progress=int(progress_row["progress"] if progress_row else 0),
                started_at=started_at,
                finished_at=finished_at,
                scheduled=True,
            )
        next_sync = _schedule_next_after_run(supplier)
        with _connect(REGISTRY_PATH) as conn:
            conn.execute(
                "UPDATE suppliers SET next_sync_at=?,updated_at=? WHERE slug=?",
                (next_sync, utc_now(), supplier["slug"]),
            )
        results.append(
            {
                "supplier": supplier["slug"],
                "status": status,
                "next_sync_at": next_sync,
                "result": result,
            }
        )
    return results


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--background-job":
        run_background_sync(sys.argv[2])
    else:
        print(json.dumps(run_due_syncs(), ensure_ascii=False, default=str))
