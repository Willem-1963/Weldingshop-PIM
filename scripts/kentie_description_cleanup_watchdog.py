from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path


PROJECT = Path("/root/weldingshop-pim")
PYTHON = PROJECT / ".venv/bin/python"
WORKER = PROJECT / "scripts/kentie_description_cleanup_worker.py"
STATE = PROJECT / "data/jobs/kentie-description-cleanup.json"
DB = PROJECT / "data/database/suppliers/kentie.sqlite"
CHECK_SECONDS = 30
STALE_SECONDS = 15 * 60
RESTART_DELAY_SECONDS = 10
IDLE_SECONDS = 5 * 60
MAX_CONSECUTIVE_RESTARTS = 3
child: subprocess.Popen | None = None
stopping = False


def log(message: str) -> None:
    print(f"[kentie-description-watchdog] {message}", flush=True)


def pending() -> int:
    with sqlite3.connect(DB, timeout=60) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='description_cleanup_items'"
        ).fetchone()
        if not table:
            return 1
        return int(connection.execute(
            "SELECT count(*) FROM description_cleanup_items WHERE status='pending' AND attempts<3"
        ).fetchone()[0])


def stop_child() -> None:
    global child
    if child and child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=30)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def stop(signum: int, _frame: object) -> None:
    global stopping
    stopping = True
    log(f"stopsignaal {signum} ontvangen")
    stop_child()


def run_worker() -> str:
    global child
    started = time.time()
    child = subprocess.Popen(
        [str(PYTHON), str(WORKER), "--delay", "2"], cwd=PROJECT,
        env={**os.environ, "PYTHONPATH": str(PROJECT)}, start_new_session=True,
    )
    log(f"worker gestart met pid {child.pid}")
    while not stopping:
        code = child.poll()
        if code is not None:
            return "complete" if code == 0 else f"exit-{code}"
        try:
            age = time.time() - STATE.stat().st_mtime
        except OSError:
            age = time.time() - started
        if age > STALE_SECONDS:
            log(f"heartbeat {int(age)} seconden oud; worker wordt herstart")
            stop_child()
            return "stale"
        time.sleep(CHECK_SECONDS)
    return "stopped"


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    failures = 0
    while not stopping:
        result = run_worker()
        if stopping:
            break
        if result == "complete":
            failures = 0
            log(f"cyclus voltooid; {pending()} items resterend")
            time.sleep(IDLE_SECONDS)
            continue
        failures += 1
        log(f"workerprobleem {result}; herstartpoging {failures}/{MAX_CONSECUTIVE_RESTARTS}")
        if failures >= MAX_CONSECUTIVE_RESTARTS:
            log("drie opeenvolgende crashes; vijf minuten afkoelen voor nieuwe cyclus")
            failures = 0
            time.sleep(IDLE_SECONDS)
        else:
            time.sleep(RESTART_DELAY_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
