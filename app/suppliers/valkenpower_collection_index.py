from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.suppliers.hub import (
    REGISTRY_PATH,
    _connect,
    get_supplier,
    init_registry,
    init_supplier_database,
    utc_now,
)
from app.suppliers.on_demand_import import (
    _official_hosts,
    _official_product_search_url,
)


ACTIVE = {"queued", "running"}
GROUPS = (
    "Werkplaatsuitrusting", "Gereedschap", "Lasapparatuur",
    "Hout- en metaalbewerking", "Hef- en hijsmateriaal",
    "Compressoren en toebehoren", "Generator, motor en pomp",
    "Elektra en accessoires", "Logistiek", "4x4", "Hefbruggen",
    "Straalapparatuur", "Scheeps benodigdheden", "Motorfiets uitrusting",
    "Ventilatie en afzuiging",
)


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(value).casefold()).strip()


GROUP_BY_NORMALIZED = {_normalized(group): group for group in GROUPS}


def init_valkenpower_collection_jobs() -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS valkenpower_collection_jobs(
               id TEXT PRIMARY KEY,mode TEXT NOT NULL,status TEXT NOT NULL,
               total INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,
               changed INTEGER NOT NULL DEFAULT 0,unchanged INTEGER NOT NULL DEFAULT 0,
               not_found INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,
               current_sku TEXT,message TEXT,error TEXT,pid INTEGER,
               created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,
               updated_at TEXT NOT NULL)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS valkenpower_collection_items(
               job_id TEXT NOT NULL,sku TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
               old_group TEXT,new_group TEXT,source_url TEXT,message TEXT,
               updated_at TEXT NOT NULL,PRIMARY KEY(job_id,sku),
               FOREIGN KEY(job_id) REFERENCES valkenpower_collection_jobs(id))"""
        )


def collection_counts() -> list[dict[str, Any]]:
    path = init_supplier_database("valkenpower")
    with _connect(path) as connection:
        return [dict(row) for row in connection.execute(
            """SELECT COALESCE(NULLIF(TRIM(product_group_name),''),
                      'Nog geen collectie') AS product_group,
                      COUNT(*) AS products
                 FROM products WHERE source_present=1
                GROUP BY COALESCE(NULLIF(TRIM(product_group_name),''),
                         'Nog geen collectie')
                ORDER BY product_group"""
        )]


def collection_products(product_group: str) -> list[dict[str, Any]]:
    path = init_supplier_database("valkenpower")
    group = str(product_group or "").strip()
    with _connect(path) as connection:
        if group == "Nog geen collectie":
            rows = connection.execute(
                """SELECT sku,source_title,execution,category_full
                     FROM products
                    WHERE source_present=1
                      AND TRIM(COALESCE(product_group_name,''))=''
                    ORDER BY source_title,sku"""
            )
        else:
            rows = connection.execute(
                """SELECT sku,source_title,execution,category_full
                     FROM products
                    WHERE source_present=1 AND product_group_name=?
                    ORDER BY source_title,sku""",
                (group,),
            )
        return [dict(row) for row in rows]


def collection_index_status() -> dict[str, Any] | None:
    init_valkenpower_collection_jobs()
    with _connect(REGISTRY_PATH) as connection:
        row = connection.execute(
            "SELECT * FROM valkenpower_collection_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def official_breadcrumb_status() -> dict[str, Any]:
    state_path = Path(
        "/root/weldingshop-pim/data/jobs/valkenpower-category-backfill.json"
    )
    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        heartbeat_age = max(0, int(time.time() - state_path.stat().st_mtime))
    except (OSError, json.JSONDecodeError):
        state_data, heartbeat_age = {}, -1
    total = int(state_data.get("total") or 0)
    processed = int(state_data.get("processed") or 0)
    status = str(state_data.get("status") or "onbekend")
    path = init_supplier_database("valkenpower")
    with _connect(path) as connection:
        evidence = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT status,COUNT(*) FROM official_category_evidence GROUP BY status"
            )
        }
    return {
        **state_data,
        "total": total,
        "processed": processed,
        "remaining": max(0, total - processed),
        "heartbeat_age_seconds": heartbeat_age,
        "stale": status == "running" and heartbeat_age > 15 * 60,
        "confirmed_total": evidence.get("confirmed", 0),
        "not_found_total": evidence.get("not_found", 0),
    }


def _spawn(job_id: str) -> int:
    project_dir = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_dir)
    process = subprocess.Popen(
        [sys.executable, "-m", "app.suppliers.valkenpower_collection_index", job_id],
        cwd=project_dir, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )
    return process.pid


def start_collection_index(mode: str) -> dict[str, Any]:
    if mode not in {"missing", "all"}:
        raise ValueError("Ongeldige indexeermodus.")
    active = collection_index_status() or {}
    if active.get("status") in ACTIVE:
        return active
    path = init_supplier_database("valkenpower")
    where = (
        "source_present=1 AND TRIM(COALESCE(product_group_name,''))=''"
        if mode == "missing" else "source_present=1"
    )
    with _connect(path) as connection:
        skus = [str(row[0]) for row in connection.execute(
            f"SELECT sku FROM products WHERE {where} ORDER BY sku"
        )]
    job_id, now = uuid.uuid4().hex, utc_now()
    init_valkenpower_collection_jobs()
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """INSERT INTO valkenpower_collection_jobs(
               id,mode,status,total,message,created_at,updated_at)
               VALUES(?,?,'queued',?,?,?,?)""",
            (job_id, mode, len(skus), "Indexering staat klaar", now, now),
        )
        connection.executemany(
            """INSERT INTO valkenpower_collection_items(
               job_id,sku,status,updated_at) VALUES(?,?,'pending',?)""",
            [(job_id, sku, now) for sku in skus],
        )
    pid = _spawn(job_id)
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            "UPDATE valkenpower_collection_jobs SET pid=?,updated_at=? WHERE id=?",
            (pid, utc_now(), job_id),
        )
    return {"id": job_id, "status": "queued", "total": len(skus), "pid": pid}


def _official_group(sku: str) -> tuple[str, str]:
    supplier = get_supplier("valkenpower") or {}
    website_url = str(supplier.get("website_url") or "").strip()
    hosts = _official_hosts(supplier)
    product_url = _official_product_search_url(website_url, sku, hosts)
    if not product_url:
        return "", ""
    request = urllib.request.Request(
        product_url,
        headers={"User-Agent": "Weldingshop-PIM/1.0 (+collection indexing)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read(4_000_000)
        final_host = (urlparse(response.geturl()).hostname or "").removeprefix("www.")
        if final_host not in hosts:
            raise ValueError("Productpagina valt buiten de officiële website")
    soup = BeautifulSoup(page, "html.parser")
    breadcrumb_texts = [
        node.get_text(" ", strip=True)
        for node in soup.select(
            ".breadcrumb li, .breadcrumb-item, [itemprop='itemListElement']"
        )
    ]
    normalized_crumbs = [_normalized(value) for value in breadcrumb_texts]
    matches = [
        canonical for normalized, canonical in GROUP_BY_NORMALIZED.items()
        if any(
            crumb == normalized or normalized in crumb
            for crumb in normalized_crumbs
        )
    ]
    matches = list(dict.fromkeys(matches))
    if len(matches) != 1:
        return "", product_url
    page_text = html.unescape(soup.get_text(" ", strip=True)).casefold()
    if sku.casefold() not in page_text:
        raise ValueError("SKU staat niet op de gevonden officiële productpagina")
    return matches[0], product_url


def run_collection_index(job_id: str) -> None:
    init_valkenpower_collection_jobs()
    with _connect(REGISTRY_PATH) as connection:
        job = connection.execute(
            "SELECT * FROM valkenpower_collection_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not job:
            return
        connection.execute(
            """UPDATE valkenpower_collection_jobs SET status='running',started_at=?,
               message='Officiële Valkenpower-pagina’s controleren',updated_at=?
               WHERE id=?""", (utc_now(), utc_now(), job_id),
        )
        items = connection.execute(
            """SELECT sku FROM valkenpower_collection_items
               WHERE job_id=? AND status='pending' ORDER BY sku""", (job_id,)
        ).fetchall()
    counters = {key: int(job[key] or 0) for key in (
        "completed", "changed", "unchanged", "not_found", "failed"
    )}
    product_path = init_supplier_database("valkenpower")
    for item in items:
        sku = str(item["sku"])
        with _connect(product_path) as connection:
            row = connection.execute(
                "SELECT product_group_name,raw_data_json FROM products WHERE sku=?", (sku,)
            ).fetchone()
        old_group = str(row["product_group_name"] or "") if row else ""
        source_url = ""
        try:
            new_group, source_url = _official_group(sku)
            if not new_group:
                status, message = "not_found", "Geen eenduidige officiële hoofdgroep gevonden"
            elif new_group == old_group:
                status, message = "unchanged", "Collectie is correct"
            else:
                raw = json.loads(row["raw_data_json"] or "{}") if row else {}
                raw["collection_index"] = {
                    "source_url": source_url, "verified_at": utc_now(),
                    "old_group": old_group, "new_group": new_group,
                }
                with _connect(product_path) as connection:
                    connection.execute(
                        """UPDATE products SET product_group_name=?,raw_data_json=?,
                           updated_at=? WHERE sku=?""",
                        (new_group, json.dumps(raw, ensure_ascii=False), utc_now(), sku),
                    )
                status, message = "changed", f"{old_group or 'Geen'} → {new_group}"
        except Exception as exc:
            new_group, status, message = "", "failed", str(exc)
        counters[status] += 1
        counters["completed"] += 1
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE valkenpower_collection_items SET status=?,old_group=?,
                   new_group=?,source_url=?,message=?,updated_at=?
                   WHERE job_id=? AND sku=?""",
                (status, old_group, new_group, source_url, message[:2000],
                 utc_now(), job_id, sku),
            )
            connection.execute(
                """UPDATE valkenpower_collection_jobs SET completed=?,changed=?,
                   unchanged=?,not_found=?,failed=?,current_sku=?,message=?,updated_at=?
                   WHERE id=?""",
                (counters["completed"], counters["changed"], counters["unchanged"],
                 counters["not_found"], counters["failed"], sku, message[:1000],
                 utc_now(), job_id),
            )
        time.sleep(1.0)
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """UPDATE valkenpower_collection_jobs SET status='completed',pid=NULL,
               current_sku=NULL,message='Collectie-indexering voltooid',finished_at=?,
               updated_at=? WHERE id=?""", (utc_now(), utc_now(), job_id),
        )


if __name__ == "__main__" and len(sys.argv) == 2:
    run_collection_index(sys.argv[1])
