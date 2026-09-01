from __future__ import annotations

import html
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.ai.providers.openai_provider import OpenAIProvider
from app.suppliers.hub import (
    REGISTRY_PATH,
    _connect,
    get_supplier,
    init_registry,
    init_supplier_database,
    utc_now,
)


SLUG = "rhodius-abrasives-gmbh"
PAGE_FIELD = "Pag. (Catalogus 2026/2027)"
ENRICHMENT_VERSION = 1
ACTIVE = {"queued", "running"}


def init_rhodius_catalogue_jobs() -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS rhodius_catalogue_jobs(
                id TEXT PRIMARY KEY,status TEXT NOT NULL,total INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,enriched INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,current_page INTEGER,message TEXT,
                error TEXT,pid INTEGER,delay_seconds REAL NOT NULL DEFAULT 2,
                created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS rhodius_catalogue_items(
                job_id TEXT NOT NULL,page_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',products INTEGER NOT NULL DEFAULT 0,
                message TEXT,updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id,page_number),
                FOREIGN KEY(job_id) REFERENCES rhodius_catalogue_jobs(id)
            )"""
        )


def _catalogue_path() -> Path:
    supplier = get_supplier(SLUG) or {}
    source = (supplier.get("request_options") or {}).get("catalogue_source") or {}
    path = Path(str(source.get("path") or supplier.get("catalogue_url") or ""))
    if not path.is_file():
        raise ValueError("De geregistreerde Rhodius-catalogus is niet beschikbaar.")
    return path


def _pending_pages() -> list[int]:
    path = init_supplier_database(SLUG)
    pages: set[int] = set()
    with _connect(path) as connection:
        for row in connection.execute("SELECT raw_data_json FROM products"):
            try:
                raw = json.loads(row["raw_data_json"] or "{}")
            except json.JSONDecodeError:
                continue
            page = raw.get(PAGE_FIELD)
            enrichment = raw.get("catalogue_enrichment") or {}
            if page and int(enrichment.get("version") or 0) < ENRICHMENT_VERSION:
                pages.add(int(page))
    return sorted(pages)


def rhodius_catalogue_status() -> dict[str, Any]:
    init_rhodius_catalogue_jobs()
    pending = _pending_pages()
    with _connect(REGISTRY_PATH) as connection:
        row = connection.execute(
            "SELECT * FROM rhodius_catalogue_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return {
        "pages": 169,
        "pending": len(pending),
        "enriched_pages": 169 - len(pending),
        "job": dict(row) if row else None,
    }


def _spawn(job_id: str) -> dict[str, Any]:
    project_dir = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_dir)
    process = subprocess.Popen(
        [sys.executable, "-m", "app.suppliers.rhodius_catalogue_enrichment", job_id],
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
            "UPDATE rhodius_catalogue_jobs SET pid=?,updated_at=? WHERE id=?",
            (process.pid, utc_now(), job_id),
        )
    return {"id": job_id, "status": "queued", "pid": process.pid}


def start_rhodius_catalogue_enrichment(delay_seconds: float = 2) -> dict[str, Any]:
    _catalogue_path()
    status = rhodius_catalogue_status()
    active = status.get("job") or {}
    if active.get("status") in ACTIVE:
        return active
    pages = _pending_pages()
    if not pages:
        return {"status": "completed", "total": 0, "message": "Alles is verrijkt."}
    job_id = uuid.uuid4().hex
    now = utc_now()
    delay = min(60.0, max(0.0, float(delay_seconds)))
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """INSERT INTO rhodius_catalogue_jobs(
                id,status,total,delay_seconds,created_at,updated_at
            ) VALUES(?,'queued',?,?,?,?)""",
            (job_id, len(pages), delay, now, now),
        )
        connection.executemany(
            """INSERT INTO rhodius_catalogue_items(
                job_id,page_number,status,updated_at
            ) VALUES(?,?,'pending',?)""",
            [(job_id, page, now) for page in pages],
        )
    return _spawn(job_id)


def stop_rhodius_catalogue_enrichment() -> dict[str, Any]:
    init_rhodius_catalogue_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            "SELECT * FROM rhodius_catalogue_jobs ORDER BY created_at DESC LIMIT 1"
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
            """UPDATE rhodius_catalogue_jobs SET status='paused',pid=NULL,
                current_page=NULL,message='Gepauzeerd door gebruiker',updated_at=?
                WHERE id=?""",
            (utc_now(), job["id"]),
        )
        return {**dict(job), "status": "paused", "pid": None}


def resume_rhodius_catalogue_enrichment() -> dict[str, Any]:
    init_rhodius_catalogue_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            "SELECT * FROM rhodius_catalogue_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not job:
            return start_rhodius_catalogue_enrichment()
        if job["status"] in ACTIVE:
            return dict(job)
        remaining = connection.execute(
            """SELECT COUNT(*) FROM rhodius_catalogue_items
               WHERE job_id=? AND status='pending'""",
            (job["id"],),
        ).fetchone()[0]
        if not remaining:
            return dict(job)
        connection.execute(
            """UPDATE rhodius_catalogue_jobs SET status='queued',pid=NULL,
               message=?,error=NULL,finished_at=NULL,updated_at=? WHERE id=?""",
            (f"Hervatten met {remaining} pagina's", utc_now(), job["id"]),
        )
        job_id = str(job["id"])
    return _spawn(job_id)


def _page_products(page_number: int) -> list[dict[str, Any]]:
    result = []
    with _connect(init_supplier_database(SLUG)) as connection:
        rows = connection.execute(
            """SELECT sku,source_title,raw_data_json,html_description,content_locked
               FROM products ORDER BY sku"""
        ).fetchall()
    for row in rows:
        raw = json.loads(row["raw_data_json"] or "{}")
        if int(raw.get(PAGE_FIELD) or 0) == page_number:
            result.append({**dict(row), "raw_data": raw})
    return result


def _prompt(page_number: int, page_text: str, products: list[dict[str, Any]]) -> str:
    product_context = [
        {
            "sku": item["sku"],
            "productnaam": item["raw_data"].get("Productnaam"),
            "productsoort": item["raw_data"].get("Productsoort"),
            "afmetingen": item["raw_data"].get("Afmetingen"),
            "korrel": item["raw_data"].get("Korrel / Draad Ø"),
        }
        for item in products
    ]
    return f"""Structureer uitsluitend de aantoonbare Nederlandse productinformatie uit
pagina {page_number} van de officiële RHODIUS-catalogus 2026/2027. Verzin niets,
neem geen prijzen over en combineer geen verschillende productfamilies.

De PIM-producten die exact op deze pagina zijn gevonden:
{json.dumps(product_context, ensure_ascii=False)}

Geef JSON met `groups`: een array. Ieder object bevat:
- skus: uitsluitend SKU's uit bovenstaande lijst die bij dezelfde producttekst horen;
- title: officiële korte productnaam;
- description: volledige zakelijke Nederlandse omschrijving uit de bron;
- benefits: array met aantoonbare voordelen;
- applications: array met toepassingen;
- materials: array met geschikte materialen;
- machine_suitability: array met geschikte machines;
- properties: object met overige technische eigenschappen.

Ken iedere SKU precies één keer toe. Laat ontbrekende gegevens leeg. Bewaar normen,
maten, eenheden, materiaalcodes en productnamen exact.

CATALOGUSTEKST:
{page_text[:24000]}"""


def _render_html(group: dict[str, Any]) -> str:
    parts = [f"<h2>{html.escape(str(group.get('title') or 'RHODIUS product'))}</h2>"]
    description = str(group.get("description") or "").strip()
    if description:
        parts.append(f"<p>{html.escape(description)}</p>")
    for heading, key in (
        ("Voordelen", "benefits"),
        ("Toepassingen", "applications"),
        ("Geschikte materialen", "materials"),
        ("Geschikte machines", "machine_suitability"),
    ):
        values = [str(value).strip() for value in group.get(key) or [] if str(value).strip()]
        if values:
            parts.append(f"<h3>{heading}</h3><ul>" + "".join(
                f"<li>{html.escape(value)}</li>" for value in values
            ) + "</ul>")
    properties = group.get("properties") or {}
    if isinstance(properties, dict) and properties:
        parts.append("<h3>Technische eigenschappen</h3><dl>" + "".join(
            f"<dt>{html.escape(str(key))}</dt><dd>{html.escape(str(value))}</dd>"
            for key, value in properties.items() if value not in (None, "", [])
        ) + "</dl>")
    return "".join(parts)


def enrich_page(
    page_number: int, page_text: str, provider: OpenAIProvider | None = None,
) -> int:
    products = _page_products(page_number)
    if not products:
        return 0
    provider = provider or OpenAIProvider()
    response = provider.client.with_options(timeout=180.0, max_retries=1).responses.create(
        model=os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-5.6-terra"),
        input=_prompt(page_number, page_text, products),
        text={"format": {"type": "json_object"}},
    )
    data = json.loads(response.output_text)
    allowed = {str(item["sku"]) for item in products}
    groups_by_sku: dict[str, dict[str, Any]] = {}
    for group in data.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for sku in group.get("skus") or []:
            sku = str(sku).strip()
            if sku in allowed and sku not in groups_by_sku:
                groups_by_sku[sku] = group
    if not groups_by_sku:
        raise ValueError("AI-uitvoer bevat geen exact gekoppelde SKU's.")
    updated = 0
    with _connect(init_supplier_database(SLUG)) as connection:
        for product in products:
            sku = str(product["sku"])
            group = groups_by_sku.get(sku)
            if not group:
                continue
            raw = product["raw_data"]
            raw["catalogue_enrichment"] = {
                "version": ENRICHMENT_VERSION,
                "language": "nl",
                "catalogue": "RHODIUS 2026/2027 NL",
                "page": page_number,
                "match_method": "exact_sku_in_pdf_text",
                "source_path": str(_catalogue_path()),
                "title": group.get("title") or "",
                "description": group.get("description") or "",
                "benefits": group.get("benefits") or [],
                "applications": group.get("applications") or [],
                "materials": group.get("materials") or [],
                "machine_suitability": group.get("machine_suitability") or [],
                "properties": group.get("properties") or {},
                "updated_at": utc_now(),
            }
            generated_html = _render_html(group)
            connection.execute(
                """UPDATE products SET raw_data_json=?,
                   html_description=CASE
                     WHEN content_locked=0 AND TRIM(COALESCE(html_description,''))=''
                     THEN ? ELSE html_description END,
                   updated_at=? WHERE sku=?""",
                (json.dumps(raw, ensure_ascii=False), generated_html, utc_now(), sku),
            )
            updated += 1
    return updated


def run_rhodius_catalogue_enrichment(job_id: str) -> None:
    init_rhodius_catalogue_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            "SELECT * FROM rhodius_catalogue_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not job:
            return
        connection.execute(
            """UPDATE rhodius_catalogue_jobs SET status='running',started_at=?,
               message='RHODIUS-catalogus per pagina verrijken',updated_at=? WHERE id=?""",
            (job["started_at"] or utc_now(), utc_now(), job_id),
        )
        items = connection.execute(
            """SELECT page_number FROM rhodius_catalogue_items
               WHERE job_id=? AND status='pending' ORDER BY page_number""",
            (job_id,),
        ).fetchall()
    reader = PdfReader(_catalogue_path())
    completed = int(job["completed"] or 0)
    enriched = int(job["enriched"] or 0)
    failed = int(job["failed"] or 0)
    delay = float(job["delay_seconds"] or 0)
    for item in items:
        page_number = int(item["page_number"])
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE rhodius_catalogue_jobs SET current_page=?,message=?,updated_at=?
                   WHERE id=?""",
                (page_number, f"Cataloguspagina {page_number} verwerken", utc_now(), job_id),
            )
        try:
            count = enrich_page(page_number, reader.pages[page_number - 1].extract_text() or "")
            item_status, message = "enriched", f"{count} producten verrijkt"
            enriched += count
        except Exception as exc:
            count, item_status, message = 0, "failed", str(exc)
            failed += 1
        completed += 1
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE rhodius_catalogue_items SET status=?,products=?,message=?,updated_at=?
                   WHERE job_id=? AND page_number=?""",
                (item_status, count, message[:2000], utc_now(), job_id, page_number),
            )
            connection.execute(
                """UPDATE rhodius_catalogue_jobs SET completed=?,enriched=?,failed=?,
                   error=?,updated_at=? WHERE id=?""",
                (completed, enriched, failed, message[:2000] if item_status == "failed" else None,
                 utc_now(), job_id),
            )
        if delay:
            time.sleep(delay)
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """UPDATE rhodius_catalogue_jobs SET status=?,pid=NULL,current_page=NULL,
               message=?,finished_at=?,updated_at=? WHERE id=?""",
            (
                "completed_with_errors" if failed else "completed",
                f"Klaar: {enriched} producten verrijkt; {failed} paginafouten.",
                utc_now(), utc_now(), job_id,
            ),
        )


if __name__ == "__main__" and len(sys.argv) == 2:
    run_rhodius_catalogue_enrichment(sys.argv[1])
