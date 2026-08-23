from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


PROJECT = Path("/srv/ai-product-factory")
PYTHON = Path("/opt/weldingshop-browser/venv/bin/python")
WORKER = PROJECT / "scripts/valkenpower_official_category_backfill.py"
STATE = PROJECT / "data/jobs/valkenpower-category-backfill.json"
DB = PROJECT / "data/database/suppliers/valkenpower.sqlite"
CHECK_SECONDS = 60
STALE_SECONDS = 15 * 60
RESTART_DELAY_SECONDS = 10
IDLE_CHECK_SECONDS = 5 * 60
BROWSER_SERVICE = "weldingshop-browser-chromium.service"

child: subprocess.Popen | None = None
stopping = False


def log(message: str) -> None:
    print(f"[valkenpower-category-watchdog] {message}", flush=True)


def remaining() -> int:
    with sqlite3.connect(DB, timeout=60) as connection:
        return int(connection.execute(
            """SELECT COUNT(*) FROM products p
               LEFT JOIN official_category_evidence e
                 ON e.supplier_sku=p.supplier_sku
               WHERE p.source_present=1 AND e.supplier_sku IS NULL"""
        ).fetchone()[0])


def state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def stop_child() -> None:
    global child
    if not child or child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=30)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=10)


def restart_browser() -> None:
    log(f"browserdienst {BROWSER_SERVICE} wordt herstart")
    result = subprocess.run(
        ["/usr/bin/systemctl", "restart", BROWSER_SERVICE],
        check=False, timeout=90,
    )
    if result.returncode:
        raise RuntimeError(
            f"browserdienst herstarten mislukte met code {result.returncode}"
        )
    time.sleep(5)


def handle_stop(signum: int, _frame: object) -> None:
    global stopping
    stopping = True
    log(f"stopsignaal {signum} ontvangen")
    stop_child()


def run_worker() -> str:
    global child
    started = time.time()
    child = subprocess.Popen(
        [str(PYTHON), str(WORKER), "--delay", "2.0"],
        cwd=PROJECT,
        env={**os.environ, "PYTHONPATH": str(PROJECT)},
        start_new_session=True,
    )
    log(f"worker gestart met pid {child.pid}; {remaining()} producten resterend")
    while not stopping:
        return_code = child.poll()
        if return_code is not None:
            current = state()
            log(
                f"worker beëindigd met code {return_code}; "
                f"status={current.get('status', 'onbekend')}; "
                f"resterend={remaining()}"
            )
            return (
                str(current.get("status") or "unknown")
                if return_code == 0 else "worker_failed"
            )
        try:
            state_mtime = STATE.stat().st_mtime
            # Een heartbeat van een vorige worker mag een nieuwe worker niet
            # onmiddellijk als vastgelopen markeren.
            age = time.time() - max(state_mtime, started)
        except OSError:
            age = time.time() - started
        if age > STALE_SECONDS:
            log(
                f"geen heartbeat gedurende {int(age)} seconden; "
                "worker wordt veilig herstart"
            )
            stop_child()
            return "stale"
        time.sleep(CHECK_SECONDS)
    return "stopped"


def main() -> int:
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    while not stopping:
        todo = remaining()
        if todo == 0:
            log(
                "alle officiële breadcrumbs zijn gecontroleerd; "
                f"nieuwe producten worden over {IDLE_CHECK_SECONDS} seconden gecontroleerd"
            )
            time.sleep(IDLE_CHECK_SECONDS)
            continue
        status = run_worker()
        if stopping:
            return 0
        if status == "complete" and remaining() == 0:
            continue
        if status in {"stale", "worker_failed"}:
            restart_browser()
        log(f"hervatten over {RESTART_DELAY_SECONDS} seconden")
        time.sleep(RESTART_DELAY_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
