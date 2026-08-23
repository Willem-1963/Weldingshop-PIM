from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, "/opt/weldingshop-browser/venv/lib/python3.12/site-packages")
from playwright.async_api import async_playwright

DB = Path("/srv/ai-product-factory/data/database/suppliers/valkenpower.sqlite")
STATE = Path("/srv/ai-product-factory/data/jobs/valkenpower-category-backfill.json")
CDP_URL = "http://127.0.0.1:9222"
MAIN_GROUPS = {
    "Werkplaatsuitrusting", "Gereedschap", "Lasapparatuur",
    "Hout- en metaalbewerking", "Hef- en hijsmateriaal",
    "Compressoren en toebehoren", "Generator, motor en pomp",
    "Elektra en accessoires", "Logistiek", "4x4", "Hefbruggen",
    "Straalapparatuur", "Scheeps benodigdheden", "Motorfiets uitrusting",
    "Ventilatie en afzuiging",
}
# Door Valkenpower zelf gebruikte officiële paginacodes die afwijken van de
# leverancierscode in de feed. Deze uitzonderingen zijn uitsluitend geldig
# nadat de officiële productpagina handmatig is bevestigd.
OFFICIAL_SKU_ALIASES = {
    "210631": "210631S",
    "210633": "210633S",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_state(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE)


def ensure_evidence_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS official_category_evidence (
        supplier_sku TEXT PRIMARY KEY,
        pim_sku TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('confirmed','not_found','not_listed')),
        official_product_code TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        breadcrumb_json TEXT NOT NULL DEFAULT '[]',
        hierarchy_json TEXT NOT NULL DEFAULT '[]',
        matched_by TEXT NOT NULL DEFAULT '',
        policy_key TEXT NOT NULL,
        verified_at TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT ''
    )""")
    conn.commit()


def normalize_crumbs(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        value = re.sub(r"\s+", " ", value).strip(" /\n\t")
        if value and value.casefold() not in {"home", "producten"}:
            cleaned.append(value)
    return cleaned


def persist(
    conn: sqlite3.Connection, row: sqlite3.Row, url: str, crumbs: list[str],
    official_product_code: str,
) -> None:
    # Breadcrumb: main group, category/filter, execution/subcategory, optional
    # deeper levels, followed by the product title (which is not stored as a category).
    hierarchy = crumbs[:-1]
    if not hierarchy or hierarchy[0] not in MAIN_GROUPS:
        raise ValueError(f"ongeldig hoofdpad: {crumbs!r}")
    levels = (hierarchy + [""] * 6)[:6]
    raw = json.loads(row["raw_data_json"] or "{}")
    raw["valkenpower_category_evidence"] = {
        "method": "official_product_page_breadcrumb",
        "source_url": url,
        "breadcrumb": crumbs,
        "hierarchy": hierarchy,
        "source_supplier_sku": row["supplier_sku"],
        "official_product_code": official_product_code,
        "matched_by": (
            "confirmed_official_sku_alias"
            if official_product_code.casefold() != row["supplier_sku"].casefold()
            else "exact_supplier_sku"
        ),
        "verified_at": now(),
        "policy": "valkenpower-official-breadcrumb-v1",
    }
    conn.execute(
        """UPDATE products SET product_group_name=?,filter_values_json=?,execution=?,
           subcategory_3=?,subcategory_4=?,subcategory_5=?,raw_data_json=?,updated_at=?
           WHERE sku=?""",
        (levels[0], json.dumps([levels[1]] if levels[1] else [], ensure_ascii=False),
         levels[2], levels[3], levels[4], levels[5],
         json.dumps(raw, ensure_ascii=False), now(), row["sku"]),
    )
    conn.execute(
        """INSERT INTO official_category_evidence
           (supplier_sku,pim_sku,status,official_product_code,source_url,
            breadcrumb_json,hierarchy_json,matched_by,policy_key,verified_at,note)
           VALUES (?,?, 'confirmed',?,?,?,?,?,?,?, '')
           ON CONFLICT(supplier_sku) DO UPDATE SET
             pim_sku=excluded.pim_sku,status=excluded.status,
             official_product_code=excluded.official_product_code,
             source_url=excluded.source_url,breadcrumb_json=excluded.breadcrumb_json,
             hierarchy_json=excluded.hierarchy_json,matched_by=excluded.matched_by,
             policy_key=excluded.policy_key,verified_at=excluded.verified_at,note=''""",
        (row["supplier_sku"], row["sku"], official_product_code, url,
         json.dumps(crumbs, ensure_ascii=False), json.dumps(hierarchy, ensure_ascii=False),
         ("confirmed_official_sku_alias" if official_product_code.casefold()
          != row["supplier_sku"].casefold() else "exact_supplier_sku"),
         "valkenpower-official-breadcrumb-v1", now()),
    )


async def main(delay: float, limit: int | None, only_sku: str | None) -> None:
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    ensure_evidence_table(conn)
    rows = conn.execute(
        """SELECT p.sku,p.supplier_sku,p.source_title,p.raw_data_json FROM products p
           LEFT JOIN official_category_evidence e ON e.supplier_sku=p.supplier_sku
           WHERE p.source_present=1 AND e.supplier_sku IS NULL ORDER BY p.sku"""
    ).fetchall()
    if only_sku:
        wanted = only_sku.strip().casefold().removeprefix("vp-")
        rows = [row for row in rows if row["supplier_sku"].casefold() == wanted]
    if limit:
        rows = rows[:limit]
    state = {
        "supplier": "valkenpower", "policy": "valkenpower-official-breadcrumb-v1",
        "status": "running", "started_at": now(), "total": len(rows),
        "processed": 0, "updated": 0, "not_found": 0, "errors": 0,
        "last_sku": "", "note": "Alleen officiële Valkenpower-breadcrumbs; geen AI/SKU-inferentie.",
    }
    save_state(state)
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        for row in rows:
            sku = row["supplier_sku"].strip()
            official_sku = OFFICIAL_SKU_ALIASES.get(sku.upper(), sku)
            state["last_sku"] = row["sku"]
            try:
                search_url = "https://www.valkenpower.com/nl_NL/shop?search=" + official_sku
                await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1200)
                links = await page.locator("a[href*='/shop/']").evaluate_all(
                    "els => els.map(e => e.href)"
                )
                sku_token = official_sku.casefold()
                candidates = [u for u in dict.fromkeys(links) if re.search(
                    rf"/shop/{re.escape(sku_token)}(?:-|$)", urlparse(u).path.casefold()
                )]
                if not candidates:
                    state["not_found"] += 1
                    conn.execute(
                        """INSERT OR REPLACE INTO official_category_evidence
                           (supplier_sku,pim_sku,status,policy_key,verified_at,note)
                           VALUES (?,?,'not_found','valkenpower-official-breadcrumb-v1',?,?)""",
                        (sku, row["sku"], now(), "Geen exacte officiële productpagina gevonden"),
                    )
                    conn.commit()
                    continue
                url = candidates[0]
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(800)
                body = await page.locator("body").inner_text()
                if not re.search(
                    rf"Productcode:\s*{re.escape(official_sku)}(?:\s|$)", body, re.I
                ):
                    raise ValueError("productcode op pagina komt niet exact overeen")
                crumbs = normalize_crumbs(await page.locator(
                    "ol.breadcrumb li, .breadcrumb-item"
                ).all_inner_texts())
                if len(crumbs) < 2:
                    # Odoo also renders the hierarchy as a slash-separated line.
                    match = re.search(r"^(.+?\s/\s.+?)\s+Merk:", body, re.M)
                    crumbs = normalize_crumbs(match.group(1).split("/") if match else [])
                    crumbs.append(row["source_title"])
                persist(conn, row, url, crumbs, official_sku)
                conn.commit()
                state["updated"] += 1
            except Exception as exc:
                state["errors"] += 1
                state["last_error"] = {"sku": row["sku"], "message": str(exc)[:500]}
                conn.rollback()
                # A Chromium renderer can crash during a long supplier crawl.
                # Never keep using that dead page for every following product.
                try:
                    await page.close()
                except Exception:
                    pass
                page = await context.new_page()
            finally:
                state["processed"] += 1
                if state["processed"] % 10 == 0:
                    save_state(state)
                await asyncio.sleep(delay)
        try:
            await page.close()
        except Exception:
            pass
    state["status"] = "complete" if state["errors"] == 0 else "needs_retry"
    state["completed_at"] = now()
    save_state(state)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sku")
    args = parser.parse_args()
    asyncio.run(main(args.delay, args.limit, args.sku))
