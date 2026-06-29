from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path("data/database/product_factory.sqlite")


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                sku TEXT PRIMARY KEY,
                name TEXT,
                title_nl TEXT,
                description_html TEXT,
                price REAL,
                sale_price REAL,
                cost_price REAL,
                ean TEXT,
                weight_grams REAL,
                length_mm REAL,
                width_mm REAL,
                height_mm REAL,
                category TEXT,
                category_full TEXT,
                product_group TEXT,
                vendor TEXT DEFAULT 'SP Tools',
                source_csv INTEGER DEFAULT 0,
                source_excel INTEGER DEFAULT 0,
                status TEXT DEFAULT 'draft',
                inventory_policy TEXT DEFAULT 'continue',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                image_url TEXT NOT NULL,
                position INTEGER DEFAULT 1,
                UNIQUE(sku, image_url),
                FOREIGN KEY(sku) REFERENCES products(sku)
            );

            CREATE TABLE IF NOT EXISTS import_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def log(level: str, message: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO import_log(level, message) VALUES (?, ?)",
            (level.upper(), message),
        )
