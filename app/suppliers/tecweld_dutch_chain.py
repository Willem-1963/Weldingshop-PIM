from __future__ import annotations

import json
import os
import hashlib
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from app.ai.providers.openai_provider import OpenAIProvider
from app.suppliers.dutch_content import (
    PROFILE_VERSION,
    assert_dutch_product_content,
    init_content_localization_tables,
    localize_pdf_document,
    polish_score,
    record_product_localization,
    repair_product_dutch,
    weldingshop_product_html,
)
from app.suppliers.hub import REGISTRY_PATH, _connect, init_supplier_database, utc_now
from app.suppliers.on_demand_import import _official_hosts, _page_documents
from app.suppliers.hub import get_supplier
from app.suppliers.routes import supplier_route


SLUG = "tecweld"
OUTPUT_ROOT = Path("/srv/weldingshop-pim/data/content/tecweld")
ACTIVE_STATUSES = {"queued", "running"}


def _assert_tecweld_translation_route() -> None:
    route = supplier_route(SLUG)
    if (
        route.slug != "tecweld"
        or not route.localizes_customer_content_to_dutch
    ):
        raise RuntimeError(
            "Nederlandse productvertaling is uitsluitend toegestaan voor Tecweld"
        )


def _needs_product_translation(
    localization_status: str | None, localization_version: int | None,
    data: dict,
) -> bool:
    if (
        localization_status != "passed"
        or localization_version != PROFILE_VERSION
    ):
        return True
    try:
        assert_dutch_product_content(data)
    except ValueError:
        return True
    return False


def init_tables(connection: sqlite3.Connection) -> None:
    init_content_localization_tables(connection)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS dutch_content_jobs(
           id TEXT PRIMARY KEY,status TEXT NOT NULL,total_products INTEGER NOT NULL DEFAULT 0,
           completed_products INTEGER NOT NULL DEFAULT 0,total_documents INTEGER NOT NULL DEFAULT 0,
           completed_documents INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,
           current_sku TEXT,message TEXT,error TEXT,created_at TEXT NOT NULL,
           started_at TEXT,finished_at TEXT,updated_at TEXT NOT NULL)"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS dutch_content_job_items(
           job_id TEXT NOT NULL,sku TEXT NOT NULL,item_type TEXT NOT NULL,status TEXT NOT NULL,
           source_url TEXT NOT NULL DEFAULT '',error TEXT,updated_at TEXT NOT NULL,
           PRIMARY KEY(job_id,sku,item_type,source_url))"""
    )


def translation_status() -> dict:
    path = init_supplier_database(SLUG)
    with _connect(path) as connection:
        init_tables(connection)
        job = connection.execute(
            "SELECT * FROM dutch_content_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        eligible = connection.execute(
            """SELECT COUNT(*) FROM products p
               LEFT JOIN product_content_localization l ON l.sku=p.sku
               WHERE p.source_present=1 AND (
                   l.status IS NULL OR l.status<>'passed'
                   OR l.profile_version IS NULL OR l.profile_version<>?
               )""",
            (PROFILE_VERSION,),
        ).fetchone()[0]
        approved = connection.execute(
            """SELECT COUNT(*) FROM product_content_localization
               WHERE status='passed' AND profile_version=?""",
            (PROFILE_VERSION,),
        ).fetchone()[0]
    return {
        "job": dict(job) if job else None,
        "eligible": int(eligible),
        "approved": int(approved),
    }


def _spawn_translation_job(job_id: str) -> dict:
    project_dir = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_dir)
    process = subprocess.Popen(
        [sys.executable, "-m", "app.suppliers.tecweld_dutch_chain", job_id],
        cwd=project_dir,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return {"id": job_id, "status": "queued", "pid": process.pid}


def start_product_translation() -> dict:
    status = translation_status()
    active = status.get("job") or {}
    if active.get("status") in ACTIVE_STATUSES:
        return active
    job_id = create_job(revalidate_all=False, include_documents=False)
    return _spawn_translation_job(job_id)


def create_job(
    *, revalidate_all: bool = False, include_documents: bool = True,
) -> str:
    _assert_tecweld_translation_route()
    path = init_supplier_database(SLUG)
    now = utc_now()
    job_id = uuid.uuid4().hex
    with _connect(path) as connection:
        init_tables(connection)
        products = connection.execute(
            """SELECT p.sku,p.ai_title,p.product_group_name,p.html_description,
                      p.raw_data_json,l.status localization_status,
                      l.profile_version localization_version
               FROM products p LEFT JOIN product_content_localization l ON l.sku=p.sku
               WHERE p.source_present=1 ORDER BY p.sku"""
        ).fetchall()
        product_skus = []
        for row in products:
            raw = json.loads(row["raw_data_json"] or "{}")
            website = raw.get("website_import") or {}
            customer = " ".join([
                str(row["ai_title"] or ""),
                str(row["product_group_name"] or ""),
                str(row["html_description"] or ""),
                json.dumps(website.get("technical_specifications") or {}, ensure_ascii=False),
            ])
            if (
                revalidate_all
                or row["localization_status"] != "passed"
                or row["localization_version"] != PROFILE_VERSION
                or polish_score(customer)
            ):
                product_skus.append(str(row["sku"]))
        documents = connection.execute(
            (
                """SELECT sku,source_url FROM source_product_documents
                   ORDER BY sku,id"""
                if revalidate_all else
                """SELECT sku,source_url FROM source_product_documents
                   WHERE localization_status IN ('pending','failed') ORDER BY sku,id"""
            )
        ).fetchall() if include_documents else []
        connection.execute(
            """INSERT INTO dutch_content_jobs(
               id,status,total_products,total_documents,message,created_at,updated_at)
               VALUES(?,'queued',?,?,?,?,?)""",
            (job_id, len(product_skus), len(documents),
             "Nederlandse Tecweld-productvertaling voorbereid"
             if not include_documents else
             "Nederlandse Tecweld-contentketen voorbereid",
             now, now),
        )
        connection.executemany(
            """INSERT INTO dutch_content_job_items(
               job_id,sku,item_type,status,updated_at) VALUES(?,?,'product','pending',?)""",
            [(job_id, sku, now) for sku in product_skus],
        )
        connection.executemany(
            """INSERT INTO dutch_content_job_items(
               job_id,sku,item_type,status,source_url,updated_at)
               VALUES(?,?,'document','pending',?,?)""",
            [(job_id, str(row["sku"]), str(row["source_url"]), now) for row in documents],
        )
    return job_id


def _translate_product(connection: sqlite3.Connection, provider: OpenAIProvider, model: str, sku: str) -> None:
    row = connection.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
    localization = connection.execute(
        "SELECT status,profile_version FROM product_content_localization WHERE sku=?",
        (sku,),
    ).fetchone()
    raw = json.loads(row["raw_data_json"] or "{}")
    website = raw.get("website_import") or {}
    data = {
        "title": row["source_title"] or sku,
        "product_group_name": row["product_group_name"] or "",
        "description": row["source_description"] or "",
        "technical_specifications": website.get("technical_specifications") or {},
        "accessories": website.get("accessories") or [],
        "feature_icons": website.get("feature_icons") or [],
    }
    if _needs_product_translation(
        localization["status"] if localization else None,
        localization["profile_version"] if localization else None,
        data,
    ):
        data = repair_product_dutch(provider, model, data)
    now = utc_now()
    title = str(data["title"]).strip()
    product_group_name = str(data.get("product_group_name") or "").strip()
    description = str(data["description"]).strip()
    specifications = data.get("technical_specifications") or {}
    website["technical_specifications"] = specifications
    website["accessories"] = data.get("accessories") or []
    website["feature_icons"] = data.get("feature_icons") or []
    website["language"] = "nl-NL"
    website["localized_at"] = now
    raw["website_import"] = website
    connection.execute(
        """UPDATE products SET ai_title=?,product_group_name=?,
           html_description=?,raw_data_json=?,updated_at=? WHERE sku=?""",
        (title, product_group_name, weldingshop_product_html(
            title, description, specifications,
            feature_icons=data.get("feature_icons") or [],
            accessories=data.get("accessories") or [],
        ),
         json.dumps(raw, ensure_ascii=False), now, sku),
    )
    record_product_localization(
        connection, sku=sku, title=title, description=description,
        specifications=specifications, now=now,
    )
    source_url = str(website.get("source_url") or "")
    if source_url:
        supplier = get_supplier(SLUG) or {}
        for document in _page_documents(source_url, _official_hosts(supplier)):
            connection.execute(
                """INSERT INTO source_product_documents(
                   sku,document_type,source_url,source_title,source_language,
                   localization_status,discovered_at,updated_at)
                   VALUES(?,?,?,?,'pl-PL','pending',?,?)
                   ON CONFLICT(sku,source_url) DO UPDATE SET updated_at=excluded.updated_at""",
                (sku, document["document_type"], document["source_url"],
                 document["source_title"], now, now),
            )


def _reuse_document_from_current_job(
    connection: sqlite3.Connection, *, job_id: str, sku: str, source: sqlite3.Row,
) -> bool:
    """Reuse only a document already regenerated and passed in this same audit."""
    previous = connection.execute(
        """SELECT pd.* FROM dutch_content_job_items ji
           JOIN product_documents pd ON pd.sku=ji.sku
             AND pd.source_url=ji.source_url AND pd.language='nl-NL'
           WHERE ji.job_id=? AND ji.item_type='document' AND ji.status='passed'
             AND ji.source_url=? AND ji.sku<>? AND pd.quality_status='passed'
           ORDER BY ji.updated_at DESC LIMIT 1""",
        (job_id, source["source_url"], sku),
    ).fetchone()
    if not previous:
        return False
    path = Path(str(previous["local_path"] or ""))
    if not path.is_file() or path.stat().st_size == 0:
        return False
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != str(previous["sha256"] or ""):
        return False
    connection.execute(
        """INSERT INTO product_documents(
           sku,document_type,title,language,local_path,filename,mime_type,size_bytes,
           sha256,source_url,source_revision,shopify_file_id,shopify_cdn_url,
           translation_status,updated_at,profile_key,profile_version,quality_status)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(sku,document_type,language) DO UPDATE SET
           title=excluded.title,local_path=excluded.local_path,filename=excluded.filename,
           mime_type=excluded.mime_type,size_bytes=excluded.size_bytes,sha256=excluded.sha256,
           source_url=excluded.source_url,source_revision=excluded.source_revision,
           shopify_file_id=excluded.shopify_file_id,shopify_cdn_url=excluded.shopify_cdn_url,
           translation_status=excluded.translation_status,updated_at=excluded.updated_at,
           profile_key=excluded.profile_key,profile_version=excluded.profile_version,
           quality_status=excluded.quality_status""",
        (
            sku, source["document_type"], previous["title"], "nl-NL",
            str(path), previous["filename"], previous["mime_type"], path.stat().st_size,
            digest, source["source_url"], previous["source_revision"],
            previous["shopify_file_id"], previous["shopify_cdn_url"],
            previous["translation_status"], utc_now(), previous["profile_key"],
            previous["profile_version"], "passed",
        ),
    )
    connection.execute(
        """UPDATE source_product_documents SET localization_status='passed',
           source_sha256=?,error=NULL,updated_at=? WHERE sku=? AND source_url=?""",
        (previous["source_revision"], utc_now(), sku, source["source_url"]),
    )
    return True


def run(job_id: str) -> None:
    _assert_tecweld_translation_route()
    path = init_supplier_database(SLUG)
    provider = OpenAIProvider()
    model = os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-5.6-terra")
    with _connect(path) as connection:
        init_tables(connection)
        job = connection.execute("SELECT * FROM dutch_content_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError("Onbekende Nederlandse contentjob")
        connection.execute(
            "UPDATE dutch_content_jobs SET status='running',started_at=COALESCE(started_at,?),updated_at=? WHERE id=?",
            (utc_now(), utc_now(), job_id),
        )
    failed = int(job["failed"] or 0)
    for item_type in ("product", "document"):
        with _connect(path) as connection:
            items = connection.execute(
                """SELECT * FROM dutch_content_job_items
                   WHERE job_id=? AND item_type=? AND status='pending' ORDER BY sku""",
                (job_id, item_type),
            ).fetchall()
        for item in items:
            sku = str(item["sku"])
            error = None
            try:
                with _connect(path) as connection:
                    connection.execute(
                        "UPDATE dutch_content_jobs SET current_sku=?,message=?,updated_at=? WHERE id=?",
                        (sku, f"{sku}: Nederlandse {item_type} verwerken", utc_now(), job_id),
                    )
                    if item_type == "product":
                        _translate_product(connection, provider, model, sku)
                    else:
                        source = connection.execute(
                            "SELECT * FROM source_product_documents WHERE sku=? AND source_url=?",
                            (sku, item["source_url"]),
                        ).fetchone()
                        if not _reuse_document_from_current_job(
                            connection, job_id=job_id, sku=sku, source=source,
                        ):
                            localize_pdf_document(
                                connection, provider=provider, model=model, sku=sku,
                                document_type=source["document_type"], source_url=source["source_url"],
                                source_title=source["source_title"] or sku,
                                output_root=OUTPUT_ROOT, now=utc_now(),
                            )
            except Exception as exc:
                error = str(exc)[:2000]
                failed += 1
            with _connect(path) as connection:
                connection.execute(
                    """UPDATE dutch_content_job_items SET status=?,error=?,updated_at=?
                       WHERE job_id=? AND sku=? AND item_type=? AND source_url=?""",
                    ("failed" if error else "passed", error, utc_now(), job_id,
                     sku, item_type, item["source_url"]),
                )
                counter = "completed_products" if item_type == "product" else "completed_documents"
                connection.execute(
                    f"UPDATE dutch_content_jobs SET {counter}={counter}+1,failed=?,error=?,updated_at=? WHERE id=?",
                    (failed, error, utc_now(), job_id),
                )
            time.sleep(2)
        if (
            item_type == "product"
            and not str(job["message"] or "").startswith(
                "Nederlandse Tecweld-productvertaling"
            )
        ):
            # Newly discovered documents join the same isolated job.
            with _connect(path) as connection:
                known = {(r[0], r[1]) for r in connection.execute(
                    "SELECT sku,source_url FROM dutch_content_job_items WHERE job_id=? AND item_type='document'",
                    (job_id,),
                )}
                documents = connection.execute(
                    "SELECT sku,source_url FROM source_product_documents WHERE localization_status IN ('pending','failed')"
                ).fetchall()
                new = [(str(r[0]), str(r[1])) for r in documents if (str(r[0]), str(r[1])) not in known]
                connection.executemany(
                    """INSERT INTO dutch_content_job_items(job_id,sku,item_type,status,source_url,updated_at)
                       VALUES(?,?,'document','pending',?,?)""",
                    [(job_id, sku, url, utc_now()) for sku, url in new],
                )
                connection.execute(
                    "UPDATE dutch_content_jobs SET total_documents=total_documents+?,updated_at=? WHERE id=?",
                    (len(new), utc_now(), job_id),
                )
    with _connect(path) as connection:
        connection.execute(
            """UPDATE dutch_content_jobs SET status=?,current_sku=NULL,message=?,
               finished_at=?,updated_at=? WHERE id=?""",
            ("completed_with_errors" if failed else "completed",
             f"Nederlandse contentketen klaar; {failed} onderdelen vragen herstel.",
             utc_now(), utc_now(), job_id),
        )


def wait_for_supplier_recovery() -> None:
    """Avoid concurrent writes; other suppliers are never inspected or changed."""
    while True:
        try:
            with _connect(REGISTRY_PATH) as connection:
                row = connection.execute(
                    """SELECT status FROM supplier_enrichment_recovery_jobs
                       WHERE supplier_slug='tecweld' ORDER BY created_at DESC LIMIT 1"""
                ).fetchone()
            if not row or row["status"] not in {"queued", "running"}:
                return
        except sqlite3.OperationalError:
            return
        time.sleep(30)


if __name__ == "__main__":
    if "--wait-for-recovery" in sys.argv:
        wait_for_supplier_recovery()
        sys.argv.remove("--wait-for-recovery")
    revalidate_all = "--revalidate-all" in sys.argv
    if revalidate_all:
        sys.argv.remove("--revalidate-all")
    selected_job = (
        sys.argv[1] if len(sys.argv) > 1
        else create_job(revalidate_all=revalidate_all)
    )
    print(json.dumps({"job_id": selected_job}, ensure_ascii=False), flush=True)
    run(selected_job)
