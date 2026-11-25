from __future__ import annotations

import fcntl
import html
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from zoneinfo import ZoneInfo

import requests

import app.core.config  # laad config/settings.env voordat OAuth-instellingen worden gelezen
from app.shopify.client import ShopifyClient
from app.suppliers.hub import DATABASE_DIR, _decrypt, _encrypt

DB_PATH = DATABASE_DIR / "facebook_blog.sqlite"
PAGE_ID = "1107768209082479"
GRAPH_VERSION = "v26.0"
TZ = ZoneInfo("Europe/Amsterdam")
ARTICLE_QUERY = """
query PimFacebookArticles($after:String) {
  articles(first:50,after:$after) {
    nodes {
      id title handle summary isPublished publishedAt
      blog { handle } image { url }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""
POST_FIELDS = (
    "id,message,created_time,permalink_url,"
    "attachments{url,target,subattachments{url,target}}"
)
OAUTH_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts"


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
          id INTEGER PRIMARY KEY CHECK(id=1),
          page_id TEXT NOT NULL, page_name TEXT NOT NULL DEFAULT '',
          token_encrypted TEXT, enabled INTEGER NOT NULL DEFAULT 0,
          daily_time TEXT NOT NULL DEFAULT '10:00',
          backfill INTEGER NOT NULL DEFAULT 1, checked_at TEXT,
          last_attempt TEXT, last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS deliveries (
          page_id TEXT NOT NULL, article_id TEXT NOT NULL,
          title TEXT NOT NULL, url TEXT NOT NULL, message TEXT NOT NULL,
          status TEXT NOT NULL, post_id TEXT, permalink TEXT,
          started_at TEXT NOT NULL, finished_at TEXT, local_day TEXT,
          PRIMARY KEY(page_id,article_id)
        );
        CREATE TABLE IF NOT EXISTS daily_posts (
          page_id TEXT NOT NULL, local_day TEXT NOT NULL,
          article_id TEXT NOT NULL, PRIMARY KEY(page_id,local_day)
        );
        CREATE TABLE IF NOT EXISTS history (
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
          status TEXT NOT NULL, article_id TEXT, title TEXT, url TEXT,
          post_id TEXT, permalink TEXT, detail TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS oauth_states (
          state TEXT PRIMARY KEY, expires_at TEXT NOT NULL
        );
        """)
        db.execute("INSERT OR IGNORE INTO settings(id,page_id) VALUES(1,?)", (PAGE_ID,))


def settings():
    init_db()
    with connect() as db:
        row = dict(db.execute("SELECT * FROM settings WHERE id=1").fetchone())
    row["connected"] = bool(row.pop("token_encrypted"))
    return row


def history(limit=300):
    init_db()
    with connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
        )]


def record(status, detail, article=None, post=None):
    a, p = article or {}, post or {}
    with connect() as db:
        db.execute(
            """INSERT INTO history(created_at,status,article_id,title,url,
               post_id,permalink,detail) VALUES(?,?,?,?,?,?,?,?)""",
            (now_utc(), status, a.get("id"), a.get("title"), a.get("url"),
             p.get("id"), p.get("permalink_url"), detail),
        )


class MetaError(RuntimeError):
    pass


def _oauth_config():
    """Return the server-side Meta app settings, never exposing the secret to the UI."""
    app_id = os.getenv("FACEBOOK_APP_ID", "").strip()
    app_secret = os.getenv("FACEBOOK_APP_SECRET", "").strip()
    redirect_uri = os.getenv("FACEBOOK_OAUTH_REDIRECT_URI", "").strip()
    if not app_id or not app_secret or not redirect_uri:
        raise ValueError(
            "Facebook-koppelen is nog niet ingesteld. Voeg FACEBOOK_APP_ID, "
            "FACEBOOK_APP_SECRET en FACEBOOK_OAUTH_REDIRECT_URI toe aan config/settings.env."
        )
    if not redirect_uri.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ValueError("FACEBOOK_OAUTH_REDIRECT_URI moet een HTTPS-adres zijn.")
    return app_id, app_secret, redirect_uri


def begin_oauth_connection():
    """Create a short-lived, single-use OAuth state and return Meta's login URL."""
    app_id, _, redirect_uri = _oauth_config()
    init_db()
    state = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    with connect() as db:
        db.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now_utc(),))
        db.execute("INSERT INTO oauth_states(state,expires_at) VALUES(?,?)", (state, expires_at))
    return "https://www.facebook.com/" + GRAPH_VERSION + "/dialog/oauth?" + urlencode({
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": OAUTH_SCOPES,
        "response_type": "code",
    })


def _oauth_get(path, params):
    try:
        response = requests.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{path}", params=params,
            timeout=(10, 60),
        )
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise MetaError("Meta is niet bereikbaar tijdens het koppelen.") from None
    if not response.ok or payload.get("error"):
        raise MetaError("Meta kon de Facebook-aanmelding niet afronden. Probeer opnieuw.")
    return payload


def complete_oauth_connection(state, code):
    """Exchange an OAuth code for a long-lived user token and its fixed page token."""
    app_id, app_secret, redirect_uri = _oauth_config()
    init_db()
    with connect() as db:
        row = db.execute("SELECT expires_at FROM oauth_states WHERE state=?", (state,)).fetchone()
        # Consume before calling Meta: a callback URL must never be replayable.
        db.execute("DELETE FROM oauth_states WHERE state=?", (state,))
    if not row or datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        raise ValueError("Deze Facebook-aanmelding is verlopen. Kies opnieuw ‘Koppel met Facebook’.")

    short_lived = _oauth_get("oauth/access_token", {
        "client_id": app_id, "client_secret": app_secret,
        "redirect_uri": redirect_uri, "code": code,
    }).get("access_token")
    if not short_lived:
        raise MetaError("Meta gaf geen toegangstoken terug. Probeer opnieuw.")
    long_lived = _oauth_get("oauth/access_token", {
        "grant_type": "fb_exchange_token", "client_id": app_id,
        "client_secret": app_secret, "fb_exchange_token": short_lived,
    }).get("access_token")
    if not long_lived:
        raise MetaError("Meta kon het toegangstoken niet verlengen. Probeer opnieuw.")

    pages = MetaClient(long_lived).request("GET", "me/accounts", params={
        "fields": "id,name,access_token", "limit": 100,
    }).get("data") or []
    page = next((item for item in pages if str(item.get("id")) == PAGE_ID), None)
    page_token = (page or {}).get("access_token")
    if not page_token:
        raise ValueError(
            "Dit Meta-account heeft geen toegang tot Facebook-pagina Weldingshop blog post. "
            "Log in met een paginabeheerder en probeer opnieuw."
        )
    save_connection(page_token)
    return page.get("name") or "Weldingshop blog post"


class MetaClient:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, **kwargs):
        try:
            response = requests.request(
                method, f"https://graph.facebook.com/{GRAPH_VERSION}/{path}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=(10, 60), **kwargs,
            )
            payload = response.json()
        except (requests.RequestException, ValueError):
            raise MetaError("Meta is niet bereikbaar of gaf geen geldig antwoord.") from None
        error = payload.get("error")
        if error or not response.ok:
            code = str((error or {}).get("code", response.status_code))
            if code == "190":
                raise MetaError("Facebook-token verlopen of ongeldig (code 190). Koppel opnieuw.")
            raise MetaError(f"Meta weigert de aanvraag (code {code}). Controleer paginatoegang en rechten.")
        return payload

    def all_posts(self):
        rows, cursor, seen = [], None, set()
        for _ in range(2000):
            params = {"fields": POST_FIELDS, "limit": 100}
            if cursor:
                params["after"] = cursor
            data = self.request("GET", f"{PAGE_ID}/posts", params=params)
            if not isinstance(data.get("data"), list):
                raise MetaError("Facebook-geschiedenis is onvolledig. Er wordt niets geplaatst.")
            rows.extend(data["data"])
            paging = data.get("paging") or {}
            if not paging.get("next"):
                return rows
            cursor = (paging.get("cursors") or {}).get("after")
            if not cursor or cursor in seen:
                raise MetaError("Facebook-paginering is onvolledig. Er wordt niets geplaatst.")
            seen.add(cursor)
        raise MetaError("Facebook-geschiedenis is te groot voor één controle. Er wordt niets geplaatst.")

    def publish(self, article):
        image_url = ((article.get("image") or {}).get("url") or "").strip()
        if not image_url:
            raise MetaError(
                "Dit blogartikel heeft geen omslagafbeelding. Het wordt niet geplaatst, "
                "zodat alle Facebook-blogposts hetzelfde grote fotobericht behouden."
            )
        return self.request("POST", f"{PAGE_ID}/photos", data={
            "url": image_url,
            "caption": article["message"],
        })


def canonical_blog_url(value):
    value = html.unescape(str(value or "")).strip().rstrip(".,;!?)")
    if value.startswith("/blogs/"):
        value = "https://www.weldingshop.nl" + value
    try:
        parsed = urlsplit(value)
        for _ in range(3):
            if (parsed.hostname or "").endswith("facebook.com") and parsed.path.endswith("/l.php"):
                value = parse_qs(parsed.query).get("u", [""])[0]
                parsed = urlsplit(value)
            else:
                break
        if (parsed.hostname or "").lower() not in {"weldingshop.nl", "www.weldingshop.nl"}:
            return ""
        path = unquote(parsed.path).rstrip("/").casefold()
        if not re.fullmatch(r"/blogs/news/[^/]+", path):
            return ""
        return "https://www.weldingshop.nl" + path
    except ValueError:
        return ""


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def post_links(post):
    result = set()
    for text in _strings(post):
        for candidate in [text, *re.findall(r"https?://[^\s<>\"]+", text)]:
            url = canonical_blog_url(candidate)
            if url:
                result.add(url)
    return result


def find_existing(article, posts):
    url = canonical_blog_url(article["url"])
    title = re.sub(r"\s+", " ", article["title"]).strip().casefold()
    for post in posts:
        if url in post_links(post):
            return post
        message = re.sub(r"\s+", " ", post.get("message", "")).casefold()
        if len(title) >= 20 and title in message:
            return post
    return None


def fetch_articles(client=None, now=None):
    client = client or ShopifyClient.from_settings()
    now = now or datetime.now(timezone.utc)
    rows, cursor, seen = [], None, set()
    while True:
        payload = client.graphql(ARTICLE_QUERY, {"after": cursor})["articles"]
        for a in payload["nodes"]:
            if a["blog"]["handle"] != "news" or not a["isPublished"] or not a["publishedAt"]:
                continue
            if datetime.fromisoformat(a["publishedAt"].replace("Z", "+00:00")) > now:
                continue
            a = dict(a)
            a["url"] = "https://www.weldingshop.nl/blogs/news/" + a["handle"]
            excerpt = html.unescape(re.sub("<[^>]+>", " ", a.get("summary") or ""))
            excerpt = re.sub(r"\s+", " ", excerpt).strip()
            if len(excerpt) > 240:
                excerpt = excerpt[:237].rsplit(" ", 1)[0] + "…"
            a["message"] = (
                a["title"] + ("\n\n" + excerpt if excerpt else "")
                + "\n\nLees het volledige artikel: " + a["url"]
                + "\n\nWil je onze tips blijven volgen? Zet meldingen voor deze pagina aan."
            )
            rows.append(a)
        if not payload["pageInfo"]["hasNextPage"]:
            break
        cursor = payload["pageInfo"]["endCursor"]
        if not cursor or cursor in seen:
            raise RuntimeError("Shopify-paginering is onvolledig.")
        seen.add(cursor)
    return sorted(rows, key=lambda a: (a["publishedAt"], a["id"]), reverse=True)


@contextmanager
def processing_lock():
    init_db()
    with open(str(DB_PATH) + ".lock", "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Een Facebook-controle is al bezig. Probeer later opnieuw.") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def save_connection(token):
    token = token.strip()
    if not token or len(token) < 30 or any(c.isspace() for c in token):
        raise ValueError("Voer alleen het nieuwe paginatoegangstoken in.")
    with processing_lock():
        meta = MetaClient(token)
        identity = meta.request("GET", "me", params={"fields": "id,name"})
        if str(identity.get("id")) != PAGE_ID:
            raise ValueError("Dit is geen paginatoken voor Weldingshop blog post (1107768209082479).")
        posts = meta.all_posts()
        with connect() as db:
            db.execute(
                """UPDATE settings SET token_encrypted=?,page_name=?,enabled=0,
                   checked_at=?,last_error='' WHERE id=1""",
                (_encrypt(token), identity.get("name", ""), now_utc()),
            )
        record("verbonden", f"Paginatoegang gecontroleerd; {len(posts)} bestaande berichten uitgelezen. Planning staat uit.")


def save_planning(enabled, daily_time, backfill):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily_time):
        raise ValueError("Kies een geldige tijd.")
    init_db()
    with processing_lock(), connect() as db:
        row = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
        if enabled and (not row["token_encrypted"] or not row["checked_at"]):
            raise ValueError("Koppel en controleer eerst een nieuw paginatoken.")
        db.execute(
            "UPDATE settings SET enabled=?,daily_time=?,backfill=? WHERE id=1",
            (int(enabled), daily_time, int(backfill)),
        )
    record("planning", f"Dagelijks delen {'aan' if enabled else 'uit'}; {daily_time} Europe/Amsterdam; oudere artikelen {'aan' if backfill else 'uit'}.")


def _store_existing(article, post):
    timestamp = now_utc()
    with connect() as db:
        current = db.execute(
            "SELECT status FROM deliveries WHERE page_id=? AND article_id=?",
            (PAGE_ID, article["id"]),
        ).fetchone()
        db.execute(
            """INSERT INTO deliveries(page_id,article_id,title,url,message,status,
               post_id,permalink,started_at,finished_at)
               VALUES(?,?,?,?,?,'bestaand',?,?,?,?)
               ON CONFLICT(page_id,article_id) DO UPDATE SET
               status=CASE WHEN deliveries.status IN ('verzenden','onzeker') THEN 'geplaatst'
                 ELSE deliveries.status END,
               post_id=excluded.post_id,permalink=excluded.permalink,
               finished_at=excluded.finished_at""",
            (PAGE_ID, article["id"], article["title"], article["url"], article["message"],
             post.get("id"), post.get("permalink_url"), timestamp, timestamp),
        )
    if not current or current["status"] in ("verzenden", "onzeker"):
        record("overgeslagen" if not current else "hersteld",
               "Artikel al op Facebook gevonden; niet opnieuw geplaatst.", article, post)


def _prepare(meta, now):
    posts = meta.all_posts()
    articles = fetch_articles(now=now)
    for article in articles:
        existing = find_existing(article, posts)
        if existing:
            _store_existing(article, existing)
    with connect() as db:
        sent = {r["article_id"] for r in db.execute(
            "SELECT article_id FROM deliveries WHERE page_id=?", (PAGE_ID,)
        )}
        pending = db.execute(
            "SELECT title FROM deliveries WHERE status IN ('verzenden','onzeker') AND page_id=?",
            (PAGE_ID,),
        ).fetchall()
    return articles, [a for a in articles if a["id"] not in sent], pending, len(posts)


def preview():
    with processing_lock():
        with connect() as db:
            row = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
        if not row["token_encrypted"]:
            raise ValueError("Koppel eerst Facebook.")
        now = datetime.now(timezone.utc)
        articles, candidates, pending, count = _prepare(MetaClient(_decrypt(row["token_encrypted"])), now)
        if not row["backfill"]:
            candidates = [a for a in candidates if datetime.fromisoformat(
                a["publishedAt"].replace("Z", "+00:00")).astimezone(TZ).date() == now.astimezone(TZ).date()]
        return {"article": candidates[0] if candidates else None, "blocked": bool(pending),
                "articles": len(articles), "posts": count, "remaining": len(candidates)}


def publish_preview(now=None):
    """Publish the currently eligible next article after a fresh safety check."""
    now = now or datetime.now(timezone.utc)
    with processing_lock():
        with connect() as db:
            row = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
        if not row["token_encrypted"]:
            raise ValueError("Koppel eerst Facebook.")
        local = now.astimezone(TZ)
        day = local.date().isoformat()
        meta = MetaClient(_decrypt(row["token_encrypted"]))
        try:
            _, candidates, pending, _ = _prepare(meta, now)
            if pending:
                detail = "Eerdere plaatsing heeft een onzekere uitkomst. Controleer Historie; er wordt niets geplaatst."
                with connect() as db:
                    db.execute("UPDATE settings SET enabled=0,last_error=? WHERE id=1", (detail,))
                record("onzeker", detail)
                return {"status": "onzeker"}
            if not row["backfill"]:
                candidates = [a for a in candidates if datetime.fromisoformat(
                    a["publishedAt"].replace("Z", "+00:00")).astimezone(TZ).date() == local.date()]
            if not candidates:
                return {"status": "geen_artikel"}
            a = candidates[0]
            with connect() as db:
                # A handmatige plaatsing mag vaker op één dag. Bewaar wel de eerste
                # plaatsing, zodat de automatische 15-minutenplanning die dag stopt.
                db.execute("INSERT OR IGNORE INTO daily_posts(page_id,local_day,article_id) VALUES(?,?,?)",
                           (PAGE_ID, day, a["id"]))
                db.execute(
                    """INSERT INTO deliveries(page_id,article_id,title,url,message,
                       status,started_at,local_day) VALUES(?,?,?,?,?,'verzenden',?,?)""",
                    (PAGE_ID, a["id"], a["title"], a["url"], a["message"], now_utc(), day),
                )
            try:
                result = meta.publish(a)
                if not result.get("id"):
                    raise MetaError("Meta bevestigde de plaatsing niet.")
            except Exception:
                with connect() as db:
                    db.execute("UPDATE deliveries SET status='onzeker' WHERE page_id=? AND article_id=?",
                               (PAGE_ID, a["id"]))
                    db.execute("UPDATE settings SET enabled=0,last_error=? WHERE id=1",
                               ("Plaatsing niet bevestigd. Controleer Facebook voordat opnieuw wordt geplaatst.",))
                record("onzeker", "Meta bevestigde de plaatsing niet. Planning uitgezet om dubbelplaatsen te voorkomen.", a)
                return {"status": "onzeker"}
            post_id = str(result["id"])
            permalink = "https://www.facebook.com/" + post_id
            with connect() as db:
                db.execute("""UPDATE deliveries SET status='geplaatst',post_id=?,permalink=?,
                           finished_at=? WHERE page_id=? AND article_id=?""",
                           (post_id, permalink, now_utc(), PAGE_ID, a["id"]))
                db.execute("UPDATE settings SET last_error='' WHERE id=1")
            record("geplaatst", "Handmatig vanuit het voorbeeld gedeeld met introductie en klikbare artikellink.", a,
                   {"id": post_id, "permalink_url": permalink})
            return {"status": "geplaatst", "article_id": a["id"], "post_id": post_id,
                    "permalink": permalink}
        except Exception as exc:
            detail = str(exc) if isinstance(exc, MetaError) else "Controle niet voltooid. Er is niets geplaatst; controleer verbinding en historie."
            with connect() as db:
                db.execute("UPDATE settings SET last_error=? WHERE id=1", (detail,))
                if isinstance(exc, MetaError) and "190" in detail:
                    db.execute("UPDATE settings SET enabled=0 WHERE id=1")
            record("fout", detail)
            return {"status": "fout", "detail": detail}


def run_daily(now=None):
    now = now or datetime.now(timezone.utc)
    with processing_lock():
        with connect() as db:
            row = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
        if not row["enabled"] or not row["token_encrypted"]:
            return {"status": "uitgeschakeld"}
        local = now.astimezone(TZ)
        day = local.date().isoformat()
        if local.strftime("%H:%M") < row["daily_time"]:
            return {"status": "nog_niet_gepland"}
        with connect() as db:
            if db.execute("SELECT 1 FROM daily_posts WHERE page_id=? AND local_day=?",
                          (PAGE_ID, day)).fetchone():
                return {"status": "vandaag_verwerkt"}
            if row["last_attempt"] and (now - datetime.fromisoformat(row["last_attempt"])).total_seconds() < 1800:
                return {"status": "wacht_op_volgende_controle"}
            db.execute("UPDATE settings SET last_attempt=? WHERE id=1", (now.isoformat(),))
        meta = MetaClient(_decrypt(row["token_encrypted"]))
        try:
            _, candidates, pending, count = _prepare(meta, now)
            if pending:
                detail = "Eerdere plaatsing heeft een onzekere uitkomst. Controleer Historie; automatisch delen is gepauzeerd."
                with connect() as db:
                    db.execute("UPDATE settings SET enabled=0,last_error=? WHERE id=1", (detail,))
                record("onzeker", detail)
                return {"status": "onzeker"}
            if not row["backfill"]:
                candidates = [a for a in candidates if datetime.fromisoformat(
                    a["publishedAt"].replace("Z", "+00:00")).astimezone(TZ).date() == local.date()]
            if not candidates:
                with connect() as db:
                    db.execute("UPDATE settings SET last_error='' WHERE id=1")
                    start_of_day = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
                    already_logged = db.execute(
                        "SELECT 1 FROM history WHERE status='geen_artikel' AND created_at>=? LIMIT 1",
                        (start_of_day,),
                    ).fetchone()
                if not already_logged:
                    record("geen_artikel", f"{count} Facebook-berichten gecontroleerd; geen ontbrekend artikel beschikbaar.")
                return {"status": "geen_artikel"}
            a = candidates[0]
            with connect() as db:
                db.execute("INSERT INTO daily_posts(page_id,local_day,article_id) VALUES(?,?,?)",
                           (PAGE_ID, day, a["id"]))
                db.execute(
                    """INSERT INTO deliveries(page_id,article_id,title,url,message,
                       status,started_at,local_day) VALUES(?,?,?,?,?,'verzenden',?,?)""",
                    (PAGE_ID, a["id"], a["title"], a["url"], a["message"], now_utc(), day),
                )
            # Reserve before the external write: an ambiguous response must never be retried blindly.
            try:
                result = meta.publish(a)
                if not result.get("id"):
                    raise MetaError("Meta bevestigde de plaatsing niet.")
            except Exception:
                with connect() as db:
                    db.execute(
                        "UPDATE deliveries SET status='onzeker' WHERE page_id=? AND article_id=?",
                        (PAGE_ID, a["id"]),
                    )
                    db.execute("UPDATE settings SET enabled=0,last_error=? WHERE id=1",
                               ("Plaatsing niet bevestigd. Controleer Facebook voordat opnieuw wordt geplaatst.",))
                record("onzeker", "Meta bevestigde de plaatsing niet. Planning uitgezet om dubbelplaatsen te voorkomen.", a)
                return {"status": "onzeker"}
            post_id = str(result["id"])
            permalink = "https://www.facebook.com/" + post_id
            with connect() as db:
                db.execute(
                    """UPDATE deliveries SET status='geplaatst',post_id=?,permalink=?,
                       finished_at=? WHERE page_id=? AND article_id=?""",
                    (post_id, permalink, now_utc(), PAGE_ID, a["id"]),
                )
                db.execute("UPDATE settings SET last_error='' WHERE id=1")
            record("geplaatst", "Blog gedeeld met introductie en klikbare artikellink.", a,
                   {"id": post_id, "permalink_url": permalink})
            return {"status": "geplaatst", "article_id": a["id"], "post_id": post_id}
        except Exception as exc:
            # Never persist request exceptions or API payloads: they can contain credentials.
            detail = str(exc) if isinstance(exc, MetaError) else "Controle niet voltooid. Er is niets nieuws geplaatst; controleer verbinding en historie."
            with connect() as db:
                db.execute("UPDATE settings SET last_error=? WHERE id=1", (detail,))
                if isinstance(exc, MetaError) and "190" in detail:
                    db.execute("UPDATE settings SET enabled=0 WHERE id=1")
            record("fout", detail)
            return {"status": "fout", "detail": detail}


if __name__ == "__main__":
    print(json.dumps(run_daily(), ensure_ascii=False))
