from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import pandas as pd
import requests

from app.shopify.client import ShopifyClient


PROJECT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_DIR / "data" / "workplace_configurator.sqlite3"
LIVE_URL = "https://www.weldingshop.nl/pages/3d-werkplaatsconfigurator"
DEFAULT_COLLECTION_HANDLES = ("werkplaats",)
VALKENPOWER_CONFIGURATORS = {
    "EXPERT": "https://www.valkenpower.com/nl_NL/configurator/expert",
    "STARX": "https://www.valkenpower.com/nl_NL/configurator/starx",
    "STARX Deep": "https://www.valkenpower.com/nl_NL/configurator/starx-deep",
}
REQUEST_HEADERS = {
    "User-Agent": "Weldingshop-PIM-ConfiguratorSync/1.0 (+https://pim.weldingshop.nl)"
}

POSITIVE_TERMS = {
    "airco vulstation", "afzuig", "bandzaag", "bandendemonteer", "bandenmachine",
    "bandenwisselaar", "boormachine", "bouwstofzuiger", "compressor", "draaibank",
    "freesmachine", "fleswagen", "generator", "gereedschapswagen", "haspel",
    "hefbrug", "heftafel", "kolomboor", "krik", "lasmachine", "lastafel",
    "metaalbewerking", "onderdelenreiniger", "palletwagen", "pers", "plasma",
    "slijpmachine", "straalcabine", "tafel", "tiglasser", "waterkoeler",
    "werkbank", "werkkast", "werkplaatskast", "werkplaatsmodule",
}
NEGATIVE_TERMS = {
    "accessoire", "adapter", "borstel", "dop", "draad", "elektrode", "filter",
    "kabel", "koppeling", "lasglas", "mondstuk", "olie", "onderdeel", "plaat",
    "reparatie", "reserve", "schijf", "slang", "spray", "toorts", "zaagblad",
}
EXCLUDED_CANDIDATE_TERMS = {
    "foam band", "frame voor", "gasveer", "keramische straalkop", "lamp voor",
    "motor voor", "raamvenster", "ringenset", "schakelaar", "stainless steel board",
    "stelpoten", "verbindings- en eindstuk", "voetpedaal", "werkblad",
}
VALKENPOWER_FEATURE_TERMS = {
    "3d": "3D-weergave",
    "accessoire": "Accessoires",
    "botsing": "Botsingscontrole",
    "delen": "Ontwerp delen",
    "deur": "Deuren en openingen",
    "kleur": "Kleurkeuze",
    "offerte": "Offerte-uitvoer",
    "opslaan": "Ontwerp opslaan",
    "pdf": "PDF-uitvoer",
    "ruimte": "Ruimte-instellingen",
    "stekkerdoos": "Elektra en stekkerdozen",
    "verlichting": "Verlichting",
    "wand": "Wandplaatsing",
    "winkelwagen": "Winkelwagenkoppeling",
    "zoom": "Zoombediening",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_database(path: Path = DB_PATH) -> None:
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS configurator_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signature TEXT NOT NULL UNIQUE,
                theme_id TEXT NOT NULL,
                storefront_theme TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                model_version TEXT NOT NULL,
                catalog_version TEXT NOT NULL,
                model_count INTEGER NOT NULL,
                page_url TEXT NOT NULL,
                catalog_url TEXT NOT NULL,
                engine_url TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                published_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                catalog_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS configurator_models (
                version_id INTEGER NOT NULL REFERENCES configurator_versions(id) ON DELETE CASCADE,
                variant_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                name TEXT NOT NULL,
                handle TEXT NOT NULL,
                product_url TEXT NOT NULL,
                image_url TEXT NOT NULL,
                series TEXT NOT NULL,
                category TEXT NOT NULL,
                model_kind TEXT NOT NULL,
                appearance TEXT NOT NULL,
                mount TEXT NOT NULL,
                width_cm REAL,
                depth_cm REAL,
                height_cm REAL,
                available INTEGER NOT NULL,
                raw_json TEXT NOT NULL,
                PRIMARY KEY(version_id, variant_id)
            );
            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                version_id INTEGER REFERENCES configurator_versions(id),
                new_count INTEGER NOT NULL DEFAULT 0,
                removal_count INTEGER NOT NULL DEFAULT 0,
                valkenpower_count INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS sync_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                finding_key TEXT NOT NULL,
                name TEXT NOT NULL,
                sku TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                url TEXT NOT NULL,
                details_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS valkenpower_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                url TEXT NOT NULL,
                signature TEXT NOT NULL,
                features_json TEXT NOT NULL,
                assets_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_improvement_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                scope TEXT NOT NULL,
                target_key TEXT NOT NULL,
                target_label TEXT NOT NULL,
                variant_id TEXT NOT NULL DEFAULT '',
                sku TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                version_id INTEGER REFERENCES configurator_versions(id),
                theme_id TEXT NOT NULL DEFAULT '',
                engine_version TEXT NOT NULL DEFAULT '',
                model_version TEXT NOT NULL DEFAULT '',
                completed_at TEXT,
                completion_note TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_models_version ON configurator_models(version_id);
            CREATE INDEX IF NOT EXISTS idx_findings_run_kind ON sync_findings(run_id, kind);
            CREATE INDEX IF NOT EXISTS idx_vp_series ON valkenpower_snapshots(series, id DESC);
            CREATE INDEX IF NOT EXISTS idx_model_requests_status
                ON model_improvement_requests(status, created_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_model_requests_open_target
                ON model_improvement_requests(scope, target_key)
                WHERE status='OPEN';
            """
        )
        sync_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(sync_runs)").fetchall()
        }
        migrations = {
            "trigger_type": "TEXT NOT NULL DEFAULT 'HANDMATIG'",
            "phase": "TEXT NOT NULL DEFAULT 'ANALYSE'",
            "shopify_status": "TEXT NOT NULL DEFAULT 'NIET_GESTART'",
            "shopify_changed_count": "INTEGER NOT NULL DEFAULT 0",
            "regression_status": "TEXT NOT NULL DEFAULT 'NIET_GESTART'",
            "preview_theme_id": "TEXT",
            "published_theme_id": "TEXT",
            "details_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, definition in migrations.items():
            if name not in sync_columns:
                conn.execute(f"ALTER TABLE sync_runs ADD COLUMN {name} {definition}")
        request_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(model_improvement_requests)"
            ).fetchall()
        }
        request_migrations = {
            "execution_status": "TEXT NOT NULL DEFAULT 'NIET_GESTART'",
            "validation_status": "TEXT NOT NULL DEFAULT 'NIET_GEVALIDEERD'",
            "payload_json": "TEXT NOT NULL DEFAULT ''",
            "last_error": "TEXT NOT NULL DEFAULT ''",
            "update_run_id": "INTEGER",
            "started_at": "TEXT",
        }
        for name, definition in request_migrations.items():
            if name not in request_columns:
                conn.execute(
                    f"ALTER TABLE model_improvement_requests ADD COLUMN {name} {definition}"
                )


def _absolute_url(value: str, base: str = LIVE_URL) -> str:
    value = (value or "").replace("\\/", "/")
    if value.startswith("//"):
        return "https:" + value
    return urljoin(base, value)


def _script_json(soup: BeautifulSoup, class_name: str) -> Any:
    node = soup.select_one(f"script.{class_name}")
    if not node:
        raise RuntimeError(f"Live configurator mist gegevensblok {class_name}.")
    return json.loads(node.string or node.get_text())


def parse_live_configurator(
    *, session: requests.Session | None = None, page_url: str = LIVE_URL,
) -> dict[str, Any]:
    http = session or requests.Session()
    response = http.get(page_url, headers=REQUEST_HEADERS, timeout=45)
    response.raise_for_status()
    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    catalog_url = _absolute_url(str(_script_json(soup, "ws-configurator__equipment-url")), page_url)
    catalog_response = http.get(catalog_url, headers=REQUEST_HEADERS, timeout=60)
    catalog_response.raise_for_status()
    catalog = catalog_response.json()
    if not isinstance(catalog, list) or not catalog:
        raise RuntimeError("Het live equipmentcatalogusbestand bevat geen modellen.")

    engine_match = re.search(
        r'import\s*\{\s*WorkplaceConfigurator3D\s*\}\s*from\s*"([^"]+)"', html
    )
    if not engine_match:
        raise RuntimeError("De live 3D-engine kon niet in de productiepagina worden gevonden.")
    engine_url = _absolute_url(engine_match.group(1), page_url)
    engine_response = http.get(engine_url, headers=REQUEST_HEADERS, timeout=45)
    engine_response.raise_for_status()
    engine_js = engine_response.text

    theme_id_match = re.search(r'"themeId"\s*:\s*"(\d+)"', html)
    storefront_match = re.search(r'/cdn/shop/t/(\d+)/assets/', engine_url)
    engine_version_match = re.search(r'workplace-configurator-3d-v(\d+)\.js', engine_url)
    model_version_match = re.search(r'workplace-3d-models-v(\d+)\.js', engine_js)
    catalog_version_match = re.search(r'workplace-equipment-catalog-v([\w.-]+)\.json', catalog_url)
    if not all((theme_id_match, storefront_match, engine_version_match, model_version_match)):
        raise RuntimeError("De productieversie is onvolledig: thema-, engine- of modelversie ontbreekt.")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in catalog:
        variant_id = str(item.get("variantId") or "").strip()
        if not variant_id or variant_id in seen:
            continue
        seen.add(variant_id)
        normalized.append(
            {
                **item,
                "variantId": variant_id,
                "code": str(item.get("code") or "").strip(),
                "title": str(item.get("title") or "Onbenoemd artikel").strip(),
                "handle": str(item.get("handle") or "").strip(),
                "url": _absolute_url(str(item.get("url") or ""), page_url),
                "image": _absolute_url(str(item.get("image") or ""), page_url),
            }
        )
    catalog_hash = sha256(catalog_response.content).hexdigest()
    version = {
        "theme_id": theme_id_match.group(1),
        "storefront_theme": storefront_match.group(1),
        "engine_version": "v" + engine_version_match.group(1),
        "model_version": "v" + model_version_match.group(1),
        "catalog_version": "v" + (catalog_version_match.group(1) if catalog_version_match else "onbekend"),
        "model_count": len(normalized),
        "page_url": page_url,
        "catalog_url": catalog_url,
        "engine_url": engine_url,
        "catalog_sha256": catalog_hash,
    }
    version["signature"] = sha256(
        "|".join(
            [version["theme_id"], version["storefront_theme"], version["engine_version"],
             version["model_version"], version["catalog_version"], catalog_hash]
        ).encode("utf-8")
    ).hexdigest()
    return {"version": version, "models": normalized}


def _theme_live_date(theme_id: str) -> str:
    try:
        client = ShopifyClient.from_settings()
        response = requests.get(
            f"https://{client.shop_domain}/admin/api/{client.api_version}/themes.json",
            headers={"X-Shopify-Access-Token": client.access_token},
            timeout=45,
        )
        response.raise_for_status()
        theme = next(
            (item for item in response.json().get("themes", []) if str(item.get("id")) == theme_id),
            None,
        )
        if theme and theme.get("updated_at"):
            return str(theme["updated_at"])
    except Exception:
        pass
    return _now()


def save_live_snapshot(snapshot: dict[str, Any], path: Path = DB_PATH) -> int:
    init_database(path)
    version = snapshot["version"]
    seen_at = _now()
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT id FROM configurator_versions WHERE signature=?", (version["signature"],)
        ).fetchone()
        if row:
            version_id = int(row["id"])
            conn.execute(
                "UPDATE configurator_versions SET last_seen_at=? WHERE id=?", (seen_at, version_id)
            )
        else:
            published_at = _theme_live_date(version["theme_id"])
            cur = conn.execute(
                """
                INSERT INTO configurator_versions(
                    signature,theme_id,storefront_theme,engine_version,model_version,
                    catalog_version,model_count,page_url,catalog_url,engine_url,
                    first_seen_at,published_at,last_seen_at,catalog_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    version["signature"], version["theme_id"], version["storefront_theme"],
                    version["engine_version"], version["model_version"],
                    version["catalog_version"], version["model_count"], version["page_url"],
                    version["catalog_url"], version["engine_url"], seen_at, published_at,
                    seen_at, version["catalog_sha256"],
                ),
            )
            version_id = int(cur.lastrowid)
        conn.execute("DELETE FROM configurator_models WHERE version_id=?", (version_id,))
        conn.executemany(
            """
            INSERT INTO configurator_models(
                version_id,variant_id,sku,name,handle,product_url,image_url,series,
                category,model_kind,appearance,mount,width_cm,depth_cm,height_cm,
                available,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    version_id, item["variantId"], item.get("code", ""), item.get("title", ""),
                    item.get("handle", ""), item.get("url", ""), item.get("image", ""),
                    item.get("series", ""), item.get("category", ""),
                    item.get("modelKind", ""), item.get("appearance", ""),
                    item.get("mount", ""), item.get("width"), item.get("depth"),
                    item.get("height"), int(bool(item.get("available"))),
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                )
                for item in snapshot["models"]
            ],
        )
    return version_id


def refresh_live_snapshot(path: Path = DB_PATH) -> dict[str, Any]:
    snapshot = parse_live_configurator()
    version_id = save_live_snapshot(snapshot, path)
    return {**snapshot, "version_id": version_id}


def current_version(path: Path = DB_PATH) -> dict[str, Any] | None:
    init_database(path)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM configurator_versions ORDER BY last_seen_at DESC,id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def version_models(version_id: int, path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_database(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM configurator_models WHERE version_id=? ORDER BY series,category,name,sku",
            (version_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def version_history(path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_database(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM configurator_versions ORDER BY published_at DESC,id DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def create_model_improvement_requests(
    *,
    scope: str,
    targets: Iterable[dict[str, Any]],
    reason: str,
    notes: str = "",
    version: dict[str, Any] | None = None,
    path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Store one durable improvement request per selected model or product group.

    An already-open target is not duplicated. Requests are deliberately separate from
    the Shopify updater: a 3D model is never changed or published without a preview and
    regression test.
    """
    init_database(path)
    normalized_scope = str(scope or "").strip().upper()
    if normalized_scope not in {"MODEL", "GROUP"}:
        raise ValueError("Kies een los model of een volledige productgroep.")
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("Kies een reden voor het verbeterverzoek.")
    rows = list(targets)
    if not rows:
        raise ValueError("Selecteer minimaal één model of productgroep.")
    active_version = version or current_version(path)
    if not active_version:
        raise RuntimeError("De actieve productieversie ontbreekt.")

    created_at = _now()
    batch_seed = json.dumps(
        [created_at, normalized_scope, [row.get("target_key") for row in rows]],
        ensure_ascii=False,
    )
    batch_id = sha256(batch_seed.encode("utf-8")).hexdigest()[:20]
    inserted_ids: list[int] = []
    with _connect(path) as conn:
        for target in rows:
            target_key = str(target.get("target_key") or "").strip()
            target_label = str(target.get("target_label") or "").strip()
            if not target_key or not target_label:
                raise ValueError("Een geselecteerd doel mist een geldige sleutel of naam.")
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO model_improvement_requests(
                    batch_id,created_at,updated_at,status,scope,target_key,target_label,
                    variant_id,sku,category,reason,notes,version_id,theme_id,
                    engine_version,model_version
                ) VALUES(?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    batch_id,
                    created_at,
                    created_at,
                    normalized_scope,
                    target_key,
                    target_label,
                    str(target.get("variant_id") or ""),
                    str(target.get("sku") or ""),
                    str(target.get("category") or ""),
                    clean_reason,
                    str(notes or "").strip(),
                    int(active_version["id"]),
                    str(active_version.get("theme_id") or ""),
                    str(active_version.get("engine_version") or ""),
                    str(active_version.get("model_version") or ""),
                ),
            )
            if cursor.rowcount:
                inserted_ids.append(int(cursor.lastrowid))
        if not inserted_ids:
            return []
        placeholders = ",".join("?" for _ in inserted_ids)
        result = conn.execute(
            f"SELECT * FROM model_improvement_requests WHERE id IN ({placeholders}) ORDER BY id",
            inserted_ids,
        ).fetchall()
        return [dict(row) for row in result]


def model_improvement_requests(
    *, status: str | None = None, path: Path = DB_PATH
) -> list[dict[str, Any]]:
    init_database(path)
    sql = "SELECT * FROM model_improvement_requests"
    params: tuple[Any, ...] = ()
    if status:
        sql += " WHERE status=?"
        params = (str(status).upper(),)
    sql += " ORDER BY created_at DESC,id DESC"
    with _connect(path) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def model_improvement_bundle(
    request_id: int, *, path: Path = DB_PATH
) -> dict[str, Any]:
    """Return an editable JSON bundle for exactly one open improvement request."""
    init_database(path)
    with _connect(path) as conn:
        request = conn.execute(
            "SELECT * FROM model_improvement_requests WHERE id=?", (int(request_id),)
        ).fetchone()
    if not request:
        raise ValueError("Het verbeterverzoek bestaat niet meer.")
    request_row = dict(request)
    version = current_version(path)
    if not version:
        raise RuntimeError("De actieve configuratorversie ontbreekt.")
    models = version_models(int(version["id"]), path)
    if request_row["scope"] == "GROUP":
        selected = [row for row in models if row["category"] == request_row["category"]]
    else:
        selected = [row for row in models if row["variant_id"] == request_row["variant_id"]]
    if not selected:
        raise RuntimeError("De gekozen modellen staan niet meer in de live configurator.")
    return {
        "requestId": int(request_row["id"]),
        "target": request_row["target_label"],
        "reason": request_row["reason"],
        "instructions": (
            "Pas uitsluitend de 3D-eigenschappen aan. Shopify-identiteit, SKU, titel, "
            "productlink en productfoto worden tijdens validatie beschermd."
        ),
        "production": {
            "themeId": version["theme_id"],
            "engineVersion": version["engine_version"],
            "modelVersion": version["model_version"],
        },
        "models": [json.loads(row["raw_json"]) for row in selected],
    }


def model_improvement_bundle_bytes(
    request_id: int, *, path: Path = DB_PATH
) -> bytes:
    return json.dumps(
        model_improvement_bundle(request_id, path=path),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def validate_model_improvement_bundle(
    request_id: int, payload: dict[str, Any], *, path: Path = DB_PATH
) -> dict[str, Any]:
    """Validate and normalize an uploaded bundle without changing Shopify."""
    current = model_improvement_bundle(request_id, path=path)
    if int(payload.get("requestId") or 0) != int(request_id):
        raise ValueError("Het JSON-bestand hoort niet bij het gekozen verbeterverzoek.")
    uploaded = payload.get("models")
    if not isinstance(uploaded, list) or not uploaded:
        raise ValueError("Het JSON-bestand bevat geen modellen.")
    current_by_id = {
        str(row.get("variantId") or ""): row for row in current["models"]
    }
    uploaded_by_id: dict[str, dict[str, Any]] = {}
    for row in uploaded:
        if not isinstance(row, dict):
            raise ValueError("Ieder model in JSON moet een object zijn.")
        variant_id = str(row.get("variantId") or "")
        if not variant_id or variant_id in uploaded_by_id:
            raise ValueError("JSON bevat een lege of dubbele Shopify variant-ID.")
        uploaded_by_id[variant_id] = row
    if set(uploaded_by_id) != set(current_by_id):
        missing = sorted(set(current_by_id) - set(uploaded_by_id))
        extra = sorted(set(uploaded_by_id) - set(current_by_id))
        detail = []
        if missing:
            detail.append("ontbreekt: " + ", ".join(missing))
        if extra:
            detail.append("niet geselecteerd: " + ", ".join(extra))
        raise ValueError("De modelselectie in JSON wijkt af (" + "; ".join(detail) + ").")

    all_models = version_models(int(current_version(path)["id"]), path)
    proven_kinds = {str(row["model_kind"]) for row in all_models}
    protected = {
        "variantId", "title", "handle", "url", "price", "available",
        "code", "image", "series", "category", "categoryId",
    }
    normalized: list[dict[str, Any]] = []
    changed: list[str] = []
    for variant_id, original in current_by_id.items():
        proposed = dict(original)
        proposed.update(uploaded_by_id[variant_id])
        for key in protected:
            if key in original:
                proposed[key] = original[key]
        required = ("variantId", "title", "code", "image", "category", "modelKind")
        missing = [key for key in required if not proposed.get(key)]
        if missing:
            raise ValueError(f"Model {variant_id} mist: {', '.join(missing)}.")
        if str(proposed["modelKind"]) not in proven_kinds:
            raise ValueError(
                f"Model {variant_id} gebruikt een onbekend 3D-modeltype: {proposed['modelKind']}."
            )
        for key in ("width", "depth", "height"):
            try:
                value = float(proposed.get(key) or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Model {variant_id} heeft geen geldige {key}.") from exc
            if value <= 0:
                raise ValueError(f"Model {variant_id} heeft een niet-positieve {key}.")
            proposed[key] = value
        if proposed != original:
            changed.append(variant_id)
        normalized.append(proposed)
    if not changed:
        raise ValueError("Er zijn geen 3D-wijzigingen in het geüploade modelpakket gevonden.")
    return {
        "request": current,
        "models": normalized,
        "changedVariantIds": changed,
        "modelCount": len(normalized),
    }


def set_model_improvement_state(
    request_id: int, *, path: Path = DB_PATH, **values: Any
) -> None:
    allowed = {
        "status", "updated_at", "execution_status", "validation_status",
        "payload_json", "last_error", "update_run_id", "started_at",
        "completed_at", "completion_note", "theme_id", "engine_version",
        "model_version", "version_id",
    }
    invalid = set(values) - allowed
    if invalid:
        raise ValueError("Ongeldige verbeterstatusvelden: " + ", ".join(sorted(invalid)))
    if not values:
        return
    values.setdefault("updated_at", _now())
    columns = ",".join(f"{name}=?" for name in values)
    with _connect(path) as conn:
        cursor = conn.execute(
            f"UPDATE model_improvement_requests SET {columns} WHERE id=?",
            [*values.values(), int(request_id)],
        )
        if not cursor.rowcount:
            raise ValueError("Het verbeterverzoek bestaat niet meer.")


def model_export_frame(models: Iterable[dict[str, Any]], version: dict[str, Any]) -> pd.DataFrame:
    rows = []
    version_label = (
        f"thema {version['theme_id']} · engine {version['engine_version']} · "
        f"modellen {version['model_version']}"
    )
    for item in models:
        rows.append(
            {
                "Productieversie": version_label,
                "Productielivegang": version["published_at"],
                "Artikelnaam": item["name"],
                "SKU": item["sku"],
                "Shopify variant-ID": item["variant_id"],
                "Serie": item["series"],
                "Categorie": item["category"],
                "3D-modeltype": item["model_kind"],
                "Vormprofiel": item["appearance"],
                "Montage": item["mount"],
                "Breedte (cm)": item["width_cm"],
                "Diepte (cm)": item["depth_cm"],
                "Hoogte (cm)": item["height_cm"],
                "Beschikbaar": "Ja" if item["available"] else "Nee",
                "Productlink": item["product_url"],
                "Afbeelding": item["image_url"],
            }
        )
    return pd.DataFrame(rows)


def model_export_xlsx(frame: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Live modellen")
        sheet = writer.book["Live modellen"]
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            values = [str(cell.value or "") for cell in column[:200]]
            sheet.column_dimensions[column[0].column_letter].width = min(55, max(12, max(map(len, values)) + 2))
    return output.getvalue()


def model_json_bytes(model: dict[str, Any]) -> bytes:
    """Return the original production catalog record for one model."""
    raw = model.get("raw_json")
    payload = json.loads(raw) if isinstance(raw, str) and raw else dict(model)
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def models_json_bytes(models: Iterable[dict[str, Any]]) -> bytes:
    """Return selected production catalog records as one portable JSON file."""
    payload = []
    for model in models:
        raw = model.get("raw_json")
        payload.append(json.loads(raw) if isinstance(raw, str) and raw else dict(model))
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _chunks(values: list[str], size: int = 200) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _live_model_statuses(client: ShopifyClient, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query = """
    query ConfiguratorVariantStatus($ids: [ID!]!) {
      nodes(ids: $ids) {
        ... on ProductVariant {
          id sku title
          product { id title handle status onlineStoreUrl }
        }
      }
    }
    """
    by_variant: dict[str, dict[str, Any] | None] = {}
    ids = [f"gid://shopify/ProductVariant/{item['variant_id']}" for item in models]
    for batch in _chunks(ids):
        data = client.graphql(query, {"ids": batch})
        for requested, node in zip(batch, data.get("nodes") or []):
            by_variant[requested.rsplit("/", 1)[-1]] = node
    findings = []
    for item in models:
        node = by_variant.get(item["variant_id"])
        product = (node or {}).get("product") or {}
        reasons = []
        if not node:
            reasons.append("Deze Shopify-variant bestaat niet meer.")
        elif product.get("status") != "ACTIVE":
            reasons.append(f"Productstatus is {product.get('status') or 'onbekend'}.")
        elif not product.get("onlineStoreUrl"):
            reasons.append("Het product staat niet meer in het online verkoopkanaal.")
        if reasons:
            findings.append(
                {
                    "key": item["variant_id"], "name": item["name"], "sku": item["sku"],
                    "status": "Controleren voor verwijdering", "reason": " ".join(reasons),
                    "url": item["product_url"], "details": {"shopify": node, "model": item},
                }
            )
    return findings


def _candidate_score(product: dict[str, Any]) -> tuple[int, list[str]]:
    haystack = " ".join(
        [str(product.get("title") or ""), str(product.get("productType") or ""),
         " ".join(product.get("tags") or [])]
    ).casefold()
    positives = sorted(term for term in POSITIVE_TERMS if term in haystack)
    negatives = sorted(term for term in NEGATIVE_TERMS if term in haystack)
    excluded = sorted(term for term in EXCLUDED_CANDIDATE_TERMS if term in haystack)
    if excluded:
        return -100, ["Onderdeel of montagemateriaal: " + ", ".join(excluded[:3]) + "."]
    score = len(positives) * 3 - len(negatives) * 2
    reasons = []
    if positives:
        reasons.append("Plaatsbaar producttype: " + ", ".join(positives[:4]) + ".")
    if negatives:
        reasons.append("Mogelijk accessoire/verbruiksartikel: " + ", ".join(negatives[:4]) + ".")
    return score, reasons


def _collection_products(client: ShopifyClient, handles: Iterable[str]) -> list[dict[str, Any]]:
    query = """
    query ConfiguratorCollectionProducts($handle: String!, $after: String) {
      collectionByIdentifier(identifier: {handle: $handle}) {
        id title
        products(first: 100, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id title handle status onlineStoreUrl productType vendor tags
            variants(first: 100) { nodes { id title sku } }
          }
        }
      }
    }
    """
    products: dict[str, dict[str, Any]] = {}
    for handle in handles:
        cursor = None
        while True:
            data = client.graphql(query, {"handle": handle, "after": cursor})
            collection = data.get("collectionByIdentifier")
            if not collection:
                break
            connection = collection.get("products") or {}
            for product in connection.get("nodes") or []:
                product.setdefault("collections", []).append(collection.get("title") or handle)
                products[str(product["id"])] = product
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
    return list(products.values())


def _new_candidates(client: ShopifyClient, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live_ids = {item["variant_id"] for item in models}
    configured = os.getenv("CONFIGURATOR_COLLECTION_HANDLES", "")
    handles = tuple(x.strip() for x in configured.split(",") if x.strip()) or DEFAULT_COLLECTION_HANDLES
    findings = []
    for product in _collection_products(client, handles):
        if product.get("status") != "ACTIVE" or not product.get("onlineStoreUrl"):
            continue
        variants = product.get("variants", {}).get("nodes") or []
        missing = [item for item in variants if str(item["id"]).rsplit("/", 1)[-1] not in live_ids]
        if not missing:
            continue
        score, reasons = _candidate_score(product)
        if score < 2:
            continue
        skus = sorted({str(item.get("sku") or "").strip() for item in missing if item.get("sku")})
        findings.append(
            {
                "key": str(product["id"]), "name": product["title"],
                "sku": ", ".join(skus) or "SKU ontbreekt",
                "status": "Sterke kandidaat" if score >= 5 else "Handmatig beoordelen",
                "reason": " ".join(reasons) or "Staat in de werkplaatscollectie en ontbreekt live.",
                "url": product.get("onlineStoreUrl") or "",
                "details": {"score": score, "product": product, "missingVariants": missing},
            }
        )
    return sorted(findings, key=lambda x: (-x["details"]["score"], x["name"].casefold()))


def _valkenpower_check(path: Path = DB_PATH) -> list[dict[str, Any]]:
    findings = []
    checked_at = _now()
    page_html: dict[str, str] = {}
    browser_required: list[tuple[str, str]] = []
    for series, url in VALKENPOWER_CONFIGURATORS.items():
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=45)
        if response.status_code == 403:
            browser_required.append((series, url))
        else:
            response.raise_for_status()
            page_html[series] = response.text
    if browser_required:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = browser.new_context(
                locale="nl-NL",
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
                ),
            )
            try:
                for series, url in browser_required:
                    page = context.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_timeout(1_500)
                    page_html[series] = page.content()
                    page.close()
            finally:
                context.close()
                browser.close()
    with _connect(path) as conn:
        for series, url in VALKENPOWER_CONFIGURATORS.items():
            html_text = page_html[series]
            soup = BeautifulSoup(html_text, "html.parser")
            for node in soup(["script", "style", "noscript"]):
                node.decompose()
            visible = " ".join(soup.stripped_strings).casefold()
            features = sorted(
                label for term, label in VALKENPOWER_FEATURE_TERMS.items() if term in visible
            )
            assets = sorted(
                _absolute_url(node.get("src", ""), url)
                for node in BeautifulSoup(html_text, "html.parser").select("script[src]")
                if node.get("src")
            )
            signature = sha256(
                json.dumps({"features": features, "assets": assets}, sort_keys=True).encode("utf-8")
            ).hexdigest()
            previous = conn.execute(
                "SELECT * FROM valkenpower_snapshots WHERE series=? ORDER BY id DESC LIMIT 1",
                (series,),
            ).fetchone()
            previous_features = set(json.loads(previous["features_json"])) if previous else set()
            new_features = sorted(set(features) - previous_features)
            if not previous:
                status = "Nulmeting vastgelegd"
                reason = "Dit is de eerste zelfstandige vergelijking; volgende wijzigingen worden hier zichtbaar."
            elif previous["signature"] == signature:
                status = "Geen wijziging gevonden"
                reason = "De zichtbare functies en geladen configuratoronderdelen zijn gelijk aan de vorige controle."
            elif new_features:
                status = "Mogelijke nieuwe functie"
                reason = "Nieuw aangetroffen: " + ", ".join(new_features) + "."
            else:
                status = "Technische wijziging gevonden"
                reason = "Valkenpower laadt gewijzigde configuratorcode; geen nieuw zichtbaar functielabel herkend."
            conn.execute(
                """
                INSERT INTO valkenpower_snapshots(
                    series,checked_at,url,signature,features_json,assets_json
                ) VALUES(?,?,?,?,?,?)
                """,
                (series, checked_at, url, signature, json.dumps(features), json.dumps(assets)),
            )
            findings.append(
                {
                    "key": series, "name": f"Valkenpower {series}", "sku": "",
                    "status": status, "reason": reason, "url": url,
                    "details": {"features": features, "newFeatures": new_features,
                                "assets": assets, "signature": signature},
                }
            )
    return findings


def run_full_sync(path: Path = DB_PATH) -> dict[str, Any]:
    init_database(path)
    started = _now()
    with _connect(path) as conn:
        run_id = int(conn.execute(
            "INSERT INTO sync_runs(started_at,status,trigger_type) VALUES(?,?,?)",
            (started, "RUNNING", "HANDMATIG"),
        ).lastrowid)
    try:
        snapshot = refresh_live_snapshot(path)
        version_id = snapshot["version_id"]
        models = version_models(version_id, path)
        client = ShopifyClient.from_settings()
        new_items = _new_candidates(client, models)
        removals = _live_model_statuses(client, models)
        valkenpower = _valkenpower_check(path)
        groups = {"new": new_items, "remove": removals, "valkenpower": valkenpower}
        with _connect(path) as conn:
            for kind, rows in groups.items():
                conn.executemany(
                    """
                    INSERT INTO sync_findings(
                        run_id,kind,finding_key,name,sku,status,reason,url,details_json
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (run_id, kind, row["key"], row["name"], row["sku"], row["status"],
                         row["reason"], row["url"], json.dumps(row["details"], ensure_ascii=False))
                        for row in rows
                    ],
                )
            conn.execute(
                """
                UPDATE sync_runs SET finished_at=?,status='SUCCEEDED',version_id=?,
                    new_count=?,removal_count=?,valkenpower_count=? WHERE id=?
                """,
                (_now(), version_id, len(new_items), len(removals), len(valkenpower), run_id),
            )
        return {"run_id": run_id, "version": snapshot["version"], **groups}
    except Exception as exc:
        with _connect(path) as conn:
            conn.execute(
                "UPDATE sync_runs SET finished_at=?,status='FAILED',error=? WHERE id=?",
                (_now(), str(exc), run_id),
            )
        raise


def latest_sync(path: Path = DB_PATH) -> dict[str, Any] | None:
    init_database(path)
    with _connect(path) as conn:
        run = conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not run:
            return None
        result = dict(run)
        rows = conn.execute(
            "SELECT * FROM sync_findings WHERE run_id=? ORDER BY kind,status,name", (run["id"],)
        ).fetchall()
    result["findings"] = [dict(row) for row in rows]
    return result


def summary_counts(models: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(models)
    return {
        "models": len(rows),
        "series": len({row["series"] for row in rows if row["series"]}),
        "categories": len({row["category"] for row in rows if row["category"]}),
        "available": sum(bool(row["available"]) for row in rows),
        "top_categories": Counter(row["category"] or "Onbekend" for row in rows).most_common(8),
        "all_categories": sorted(
            Counter(row["category"] or "Onbekend" for row in rows).items(),
            key=lambda item: (-item[1], item[0].casefold()),
        ),
    }
