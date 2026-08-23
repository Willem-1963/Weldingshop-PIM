from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from app.suppliers.on_demand_import import import_official_website_product
from app.suppliers.quality import quality_policy_for


PROJECT = Path("/srv/ai-product-factory")
DB = PROJECT / "data/database/suppliers/kentie.sqlite"
STATE = PROJECT / "data/jobs/kentie-description-cleanup.json"
MAX_ATTEMPTS = 3
SUSPECT = re.compile(
    r"(?:ik kon op basis|op basis van de (?:beschikbare|doorzochte|gevonden)|"
    r"geen bevestigde match|niet bevestigd als exacte match|"
    r"exacte (?:leveranciercode|match)[^.]{0,160}(?:niet|kon niet)|"
    r"niet letterlijk (?:aangetroffen|bevestigd|geverifieerd|aantoonbaar)|"
    r"beschikbare offici[eë]le kentie-bron|"
    r"artikelnummer[^.]{0,180}niet letterlijk)",
    re.I,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    value = str(value).strip()
    return "" if value.casefold() in {"nan", "none", "null"} else value


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def init() -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS description_cleanup_items(
               sku TEXT PRIMARY KEY,status TEXT NOT NULL DEFAULT 'pending',
               attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,
               old_source_description TEXT,old_html_description TEXT,
               result TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"""
        )
        rows = connection.execute(
            """SELECT sku,source_description,html_description FROM products
               WHERE source_present=1"""
        ).fetchall()
        stamp = now()
        candidates = [row for row in rows if SUSPECT.search(
            f"{clean(row['source_description'])}\n{clean(row['html_description'])}"
        )]
        connection.executemany(
            """INSERT OR IGNORE INTO description_cleanup_items(
               sku,old_source_description,old_html_description,created_at,updated_at)
               VALUES(?,?,?,?,?)""",
            [(row["sku"], row["source_description"], row["html_description"], stamp, stamp)
             for row in candidates],
        )


def write_state(status: str, current_sku: str = "", message: str = "") -> None:
    with connect() as connection:
        counts = {row["status"]: row["n"] for row in connection.execute(
            "SELECT status,count(*) n FROM description_cleanup_items GROUP BY status"
        )}
    payload = {
        "status": status, "current_sku": current_sku, "message": message,
        "updated_at": now(), "counts": counts,
    }
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE)


def neutral_content(row: sqlite3.Row) -> tuple[str, str]:
    try:
        raw = json.loads(row["raw_data_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        raw = {}
    title = clean(raw.get("Omschrijving")) or clean(row["source_title"]) or clean(row["sku"])
    extras = []
    for key in ("Toevoeging 1", "Toevoeging 2"):
        value = clean(raw.get(key))
        if value and value.casefold() not in title.casefold() and value not in extras:
            extras.append(value)
    description = ". ".join([title, *extras]).rstrip(". ") + "."
    if len(description) < 80:
        description += (
            " Dit artikel komt uit het assortiment van Kentie. "
            "Controleer artikelnummer en uitvoering voor de juiste keuze."
        )
    table = [("Artikelnummer", clean(row["sku"]))]
    group = clean(row["product_group_name"] or row["product_type"])
    execution = clean(row["execution"])
    if group:
        table.append(("Productgroep", group))
    if execution and execution.casefold() not in title.casefold():
        table.append(("Uitvoering", execution))
    rows_html = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"
        for label, value in table
    )
    rendered = (
        f"<h2>{html.escape(title)}</h2><p>{html.escape(description)}</p>"
        f"<h3>Productgegevens</h3><table><tbody>{rows_html}</tbody></table>"
    )
    return description, rendered


def install_neutral_fallback(sku: str, reason: str) -> None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
        if not row:
            raise RuntimeError("Product is niet meer aanwezig")
        source, rendered = neutral_content(row)
        try:
            raw = json.loads(row["raw_data_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            raw = {}
        raw["description_cleanup"] = {
            "status": "manual_review", "reason": reason[:1000], "updated_at": now(),
            "method": "neutral_supplier_feed_fallback",
        }
        digest = hashlib.sha256(rendered.encode()).hexdigest()
        connection.execute(
            """UPDATE products SET source_description=?,html_description=?,
               raw_data_json=?,content_hash=?,updated_at=? WHERE sku=?""",
            (source, rendered, json.dumps(raw, ensure_ascii=False), digest, now(), sku),
        )


def process(sku: str) -> tuple[str, str]:
    with connect() as connection:
        before = connection.execute(
            "SELECT shopify_status FROM products WHERE sku=?", (sku,)
        ).fetchone()
    previous_status = clean(before["shopify_status"] if before else "draft") or "draft"
    try:
        result = import_official_website_product(
            "kentie", sku, execution_context="bulk_enrichment"
        )
        if result.get("enrichment_skipped"):
            install_neutral_fallback(sku, clean(result.get("enrichment_message")))
            return "fallback", clean(result.get("enrichment_message"))
        with connect() as connection:
            row = connection.execute(
                """SELECT source_description,html_description,raw_data_json
                   FROM products WHERE sku=?""", (sku,)
            ).fetchone()
        if not row:
            raise RuntimeError("Verrijkt product ontbreekt na opslag")
        try:
            enriched_raw = json.loads(row["raw_data_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            enriched_raw = {}
        summary = clean((enriched_raw.get("website_import") or {}).get("source_summary"))
        if SUSPECT.search(
            f"{clean(row['source_description'])}\n{clean(row['html_description'])}"
            f"\n{summary}"
        ):
            raise RuntimeError("Nieuwe verrijking bevat nog interne matchtekst")
        policy = quality_policy_for("Kentie", supplier_slug="kentie")
        if not policy.description_is_complete(clean(row["html_description"])):
            raise RuntimeError("Exacte zoekresultaat bevat geen volledige productbeschrijving")
        return "enriched", clean(result.get("source_url"))
    finally:
        # Een inhoudelijke herstelrun mag nooit een actief product op Concept zetten.
        with connect() as connection:
            connection.execute(
                "UPDATE products SET shopify_status=? WHERE sku=?",
                (previous_status, sku),
            )


def run(delay: float) -> int:
    init()
    write_state("running", message="Verdachte Kentie-beschrijvingen worden hersteld")
    while True:
        with connect() as connection:
            item = connection.execute(
                """SELECT * FROM description_cleanup_items
                   WHERE status='pending' AND attempts<? ORDER BY sku LIMIT 1""",
                (MAX_ATTEMPTS,),
            ).fetchone()
            if not item:
                break
            sku = str(item["sku"])
            attempt = int(item["attempts"]) + 1
            connection.execute(
                "UPDATE description_cleanup_items SET attempts=?,updated_at=? WHERE sku=?",
                (attempt, now(), sku),
            )
        write_state("running", sku, f"Poging {attempt} van {MAX_ATTEMPTS}")
        try:
            result, message = process(sku)
            with connect() as connection:
                connection.execute(
                    """UPDATE description_cleanup_items SET status='completed',result=?,
                       last_error=NULL,updated_at=? WHERE sku=?""",
                    (result, now(), sku),
                )
        except Exception as exc:
            message = str(exc)
            final = attempt >= MAX_ATTEMPTS
            if final:
                # Ook een definitief mislukte webverrijking wordt schoon en
                # geïsoleerd afgehandeld, zodat de run verder kan.
                install_neutral_fallback(sku, message)
            with connect() as connection:
                connection.execute(
                    """UPDATE description_cleanup_items SET status=?,result=?,last_error=?,
                       updated_at=? WHERE sku=?""",
                    ("completed" if final else "pending", "fallback" if final else None,
                     message[:2000], now(), sku),
                )
        write_state("running", sku, message[:1000])
        time.sleep(max(0.0, delay))
    write_state("complete", message="Alle verdachte Kentie-beschrijvingen zijn afgehandeld")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=2.0)
    raise SystemExit(run(parser.parse_args().delay))
