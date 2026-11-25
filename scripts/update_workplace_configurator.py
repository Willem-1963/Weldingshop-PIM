#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path

from app.workplace_configurator_update import run_update


LOCK_PATH = Path("/tmp/weldingshop-workplace-configurator-update.lock")


def main() -> int:
    parser = argparse.ArgumentParser(description="Werk de Shopify-werkplaatsconfigurator veilig bij.")
    parser.add_argument("--trigger", choices=("automatic", "manual"), default="automatic")
    args = parser.parse_args()
    LOCK_PATH.touch(exist_ok=True)
    with LOCK_PATH.open("r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "SKIPPED", "reason": "update already running"}))
            return 0
        result = run_update(
            trigger_type="AUTOMATISCH" if args.trigger == "automatic" else "HANDMATIG"
        )
        print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
