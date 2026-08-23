from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PATH = PROJECT_DIR / "data" / "database" / "label_settings.sqlite"


def _connect(path: Path = DEFAULT_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS label_templates (
            name TEXT PRIMARY KEY COLLATE NOCASE,
            settings_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS label_workstations (
            name TEXT PRIMARY KEY COLLATE NOCASE,
            printer_name TEXT NOT NULL DEFAULT '',
            settings_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS label_state (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_templates(path: Path = DEFAULT_PATH) -> list[str]:
    with _connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM label_templates ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [str(row["name"]) for row in rows]


def get_template(name: str, path: Path = DEFAULT_PATH) -> dict[str, Any] | None:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT settings_json FROM label_templates WHERE name=?", (name.strip(),)
        ).fetchone()
    return json.loads(row["settings_json"]) if row else None


def save_template(name: str, settings: dict[str, Any], path: Path = DEFAULT_PATH) -> None:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Geef het labelontwerp een naam.")
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO label_templates(name,settings_json,updated_at) VALUES(?,?,?)
            ON CONFLICT(name) DO UPDATE SET
              settings_json=excluded.settings_json,updated_at=excluded.updated_at
            """,
            (clean_name, json.dumps(settings, ensure_ascii=False), _now()),
        )


def delete_template(name: str, path: Path = DEFAULT_PATH) -> None:
    with _connect(path) as connection:
        connection.execute("DELETE FROM label_templates WHERE name=?", (name.strip(),))


def list_workstations(path: Path = DEFAULT_PATH) -> list[str]:
    with _connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM label_workstations ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [str(row["name"]) for row in rows]


def get_workstation(name: str, path: Path = DEFAULT_PATH) -> dict[str, Any] | None:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT printer_name,settings_json FROM label_workstations WHERE name=?",
            (name.strip(),),
        ).fetchone()
    if not row:
        return None
    return {
        "printer_name": str(row["printer_name"] or ""),
        "settings": json.loads(row["settings_json"] or "{}"),
    }


def save_workstation(
    name: str,
    printer_name: str,
    settings: dict[str, Any],
    path: Path = DEFAULT_PATH,
) -> None:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Geef deze werkplek een naam.")
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO label_workstations(name,printer_name,settings_json,updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET printer_name=excluded.printer_name,
              settings_json=excluded.settings_json,updated_at=excluded.updated_at
            """,
            (clean_name, printer_name.strip(), json.dumps(settings, ensure_ascii=False), _now()),
        )


def get_last_settings(path: Path = DEFAULT_PATH) -> dict[str, Any] | None:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT value_json FROM label_state WHERE key='last_used_settings'"
        ).fetchone()
    return json.loads(row["value_json"]) if row else None


def save_last_settings(settings: dict[str, Any], path: Path = DEFAULT_PATH) -> None:
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO label_state(key,value_json,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET
              value_json=excluded.value_json,updated_at=excluded.updated_at
            """,
            ("last_used_settings", json.dumps(settings, ensure_ascii=False), _now()),
        )


def get_active_template(path: Path = DEFAULT_PATH) -> str:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT value_json FROM label_state WHERE key='active_template'"
        ).fetchone()
    if not row:
        return ""
    try:
        return str(json.loads(row["value_json"]) or "").strip()
    except (json.JSONDecodeError, TypeError):
        return ""


def save_active_template(name: str, path: Path = DEFAULT_PATH) -> None:
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO label_state(key,value_json,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET
              value_json=excluded.value_json,updated_at=excluded.updated_at
            """,
            ("active_template", json.dumps(name.strip(), ensure_ascii=False), _now()),
        )
