"""Private outbound Windows bridge; only the authenticated PIM can enqueue jobs."""
from __future__ import annotations

import base64
import hmac
import io
import json
import secrets
import sqlite3
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data/database/label_print.sqlite"
PRINTER = "gprinter gp-1324d"


class PrintQueue:
    def __init__(self, path=DATABASE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bridge (
                    id INTEGER PRIMARY KEY CHECK(id=1), token TEXT NOT NULL,
                    seen REAL NOT NULL DEFAULT 0, printer TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL, image BLOB, copies INTEGER NOT NULL,
                    state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    claim TEXT, detail TEXT NOT NULL DEFAULT ''
                );
            """)
            db.execute("INSERT OR IGNORE INTO bridge(id,token) VALUES(1,?)", (secrets.token_urlsafe(32),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def authorized(self, token):
        with self.connect() as db:
            expected = db.execute("SELECT token FROM bridge WHERE id=1").fetchone()[0]
        return bool(token) and hmac.compare_digest(expected, token)

    def status(self):
        with self.connect() as db:
            row = dict(db.execute("SELECT seen,printer,error FROM bridge WHERE id=1").fetchone())
        row["online"] = time.time() - row["seen"] < 45 and row["printer"].casefold() == PRINTER.casefold() and not row["error"]
        return row

    def enqueue(self, image, copies, title, request_key):
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 5_000_000:
            raise ValueError("Ongeldige labelafbeelding")
        if not 1 <= int(copies) <= 500:
            raise ValueError("Aantal labels moet tussen 1 en 500 liggen")
        with self.connect() as db:
            existing = db.execute("SELECT id FROM jobs WHERE request_key=?", (request_key,)).fetchone()
            if existing:
                return existing[0]
            identifier = str(uuid.uuid4())
            now = time.time()
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,'queued',?,?,NULL,'')",
                       (identifier, request_key, title[:200], image, int(copies), now, now))
        return identifier

    def claim(self, printer, error=""):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE bridge SET seen=?,printer=?,error=? WHERE id=1", (now, printer[:200], error[:500]))
            db.execute("UPDATE jobs SET state='expired',image=NULL,updated=? WHERE state='queued' AND created<?", (now, now - 600))
            # A claimed job is never automatically requeued: it may already be on paper.
            db.execute("UPDATE jobs SET state='uncertain',updated=? WHERE state='claimed' AND updated<?", (now, now - 600))
            db.execute("DELETE FROM jobs WHERE updated<?", (now - 30 * 86400,))
            if printer.casefold() != PRINTER.casefold() or error:
                return None
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            claim = secrets.token_urlsafe(24)
            db.execute("UPDATE jobs SET state='claimed',claim=?,updated=? WHERE id=?", (claim, now, row["id"]))
            return {"id": row["id"], "claim": claim, "title": row["title"], "copies": row["copies"],
                    "printer": PRINTER, "image": base64.b64encode(row["image"]).decode("ascii")}

    def complete(self, identifier, claim, state, detail=""):
        if state not in {"submitted", "failed", "uncertain"}:
            raise ValueError("Ongeldige afdrukstatus")
        with self.connect() as db:
            row = db.execute("SELECT claim,state FROM jobs WHERE id=?", (identifier,)).fetchone()
            if not row or not row["claim"] or not hmac.compare_digest(row["claim"], claim):
                raise ValueError("Onbekende printopdracht")
            if row["state"] in {"claimed", "uncertain"}:
                db.execute("UPDATE jobs SET state=?,detail=?,image=NULL,updated=? WHERE id=?",
                           (state, detail[:500], time.time(), identifier))

    def job(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT id,state,detail,copies,title FROM jobs WHERE id=?", (identifier,)).fetchone()
        return dict(row) if row else None

    def installer(self):
        with self.connect() as db:
            token = db.execute("SELECT token FROM bridge WHERE id=1").fetchone()[0]
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in (ROOT / "scripts/pim-print-windows").iterdir():
                if path.is_file():
                    archive.writestr(path.name, path.read_bytes())
            archive.writestr("config.json", json.dumps({"url": "https://pim.weldingshop.nl/label-print", "token": token, "printer": PRINTER}))
        return output.getvalue()


def render_label_png(document):
    """Rasterize the existing label, at the GP-1324D's 203 dpi."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 800, "height": 600}, device_scale_factor=203 / 96)
            page.route("**/*", lambda route: route.abort())
            page.emulate_media(media="print")
            page.set_content(document, wait_until="load")
            return page.locator(".label").first.screenshot(type="png", timeout=15000)
        finally:
            browser.close()


def handle_print_request(handler):
    """Return True only for our narrowly scoped, bearer-authenticated API."""
    if not handler.path.startswith("/label-print/"):
        return False
    queue = PrintQueue()
    authorization = handler.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or not queue.authorized(authorization[7:]):
        handler._reply(401, {"error": "unauthorized"})
        return True
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 4096:
            raise ValueError("invalid body size")
        payload = json.loads(handler.rfile.read(length))
        if handler.path == "/label-print/claim":
            job = queue.claim(str(payload.get("printer", "")), str(payload.get("error", "")))
            handler._reply(200, {"job": job})
        elif handler.path == "/label-print/complete":
            queue.complete(str(payload["id"]), str(payload["claim"]), str(payload["state"]), str(payload.get("detail", "")))
            handler._reply(200, {"ok": True})
        else:
            handler._reply(404, {"error": "not found"})
    except (ValueError, TypeError, KeyError, AttributeError):
        handler._reply(400, {"error": "invalid request"})
    return True
