from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


DEFAULT_DATABASE = Path(__file__).resolve().parents[2] / "data" / "standalone_product_maker.sqlite3"
DEFAULT_UPLOAD_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "product_maker_uploads"
DEFAULT_ERP_DATABASE = Path("/opt/weldingshop-erp/current/data/weldingshop_erp.sqlite3")
EVIDENCE_STATES = {"proven", "derived", "proposed", "missing", "conflict"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def lines(value: str | Iterable[str]) -> list[str]:
    source = value.splitlines() if isinstance(value, str) else value
    return list(dict.fromkeys(str(item).strip() for item in source if str(item).strip()))


def normalized_host(value: str) -> str:
    host = (urlparse(value).hostname or value).strip().casefold()
    return host.removeprefix("www.").rstrip(".")


class ProductMakerService:
    """Standalone productmaker; owns no ERP or supplier-sync data."""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE):
        self.database_path = str(database_path)
        self._schema()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _schema(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS pm_suppliers(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    brand TEXT NOT NULL DEFAULT '',
                    approved_domains_json TEXT NOT NULL DEFAULT '[]',
                    research_enabled INTEGER NOT NULL DEFAULT 1 CHECK(research_enabled IN(0,1)),
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pm_drafts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    supplier_id INTEGER REFERENCES pm_suppliers(id),
                    sku TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    ean TEXT NOT NULL DEFAULT '',manufacturer_number TEXT NOT NULL DEFAULT '',
                    vendor TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',
                    description_html TEXT NOT NULL DEFAULT '',short_description TEXT NOT NULL DEFAULT '',
                    seo_title TEXT NOT NULL DEFAULT '',seo_description TEXT NOT NULL DEFAULT '',
                    purchase_price TEXT NOT NULL DEFAULT '0.00',sale_price TEXT NOT NULL DEFAULT '0.00',
                    compare_at_price TEXT NOT NULL DEFAULT '',initial_quantity INTEGER NOT NULL DEFAULT 0,
                    purchase_unit TEXT NOT NULL DEFAULT 'stuk',sales_unit TEXT NOT NULL DEFAULT 'stuk',
                    unit_factor TEXT NOT NULL DEFAULT '1',product_type TEXT NOT NULL DEFAULT '',
                    category_id TEXT NOT NULL DEFAULT '',category_label TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',metafields_json TEXT NOT NULL DEFAULT '[]',
                    source_url TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',
                    price_from_purchase_invoice INTEGER NOT NULL DEFAULT 0
                        CHECK(price_from_purchase_invoice IN(0,1)),
                    status TEXT NOT NULL DEFAULT 'draft',shopify_product_id TEXT NOT NULL DEFAULT '',
                    shopify_variant_id TEXT NOT NULL DEFAULT '',shopify_admin_url TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pm_evidence(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,draft_id INTEGER NOT NULL REFERENCES pm_drafts(id) ON DELETE CASCADE,
                    field_name TEXT NOT NULL,value_json TEXT NOT NULL,state TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',source_title TEXT NOT NULL DEFAULT '',
                    source_excerpt TEXT NOT NULL DEFAULT '',matched_by TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,retrieved_at TEXT NOT NULL,
                    approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN(0,1)),
                    UNIQUE(draft_id,field_name,value_json,source_url)
                );
                CREATE TABLE IF NOT EXISTS pm_assets(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,draft_id INTEGER NOT NULL REFERENCES pm_drafts(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,url TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',official INTEGER NOT NULL DEFAULT 0,
                    identifier_verified INTEGER NOT NULL DEFAULT 0,
                    selected INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,
                    UNIQUE(draft_id,kind,url)
                );
                CREATE TABLE IF NOT EXISTS pm_research_runs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,draft_id INTEGER NOT NULL REFERENCES pm_drafts(id) ON DELETE CASCADE,
                    mode TEXT NOT NULL,status TEXT NOT NULL,query TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',message TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,finished_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS pm_audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,draft_id INTEGER,
                    action TEXT NOT NULL,details_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pm_draft_automation(
                    draft_id INTEGER PRIMARY KEY REFERENCES pm_drafts(id) ON DELETE CASCADE,
                    source_research INTEGER NOT NULL DEFAULT 1 CHECK(source_research IN(0,1)),
                    evidence_enrichment INTEGER NOT NULL DEFAULT 1 CHECK(evidence_enrichment IN(0,1)),
                    asset_collection INTEGER NOT NULL DEFAULT 1 CHECK(asset_collection IN(0,1)),
                    category_suggestion INTEGER NOT NULL DEFAULT 1 CHECK(category_suggestion IN(0,1)),
                    quality_checks INTEGER NOT NULL DEFAULT 1 CHECK(quality_checks IN(0,1)),
                    updated_at TEXT NOT NULL
                );
                """
            )
            supplier_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(pm_suppliers)")
            }
            if "sync_slug" not in supplier_columns:
                db.execute("ALTER TABLE pm_suppliers ADD COLUMN sync_slug TEXT")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_pm_suppliers_sync_slug "
                "ON pm_suppliers(sync_slug) WHERE sync_slug IS NOT NULL"
            )
            draft_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(pm_drafts)")
            }
            if "incidental" not in draft_columns:
                db.execute(
                    "ALTER TABLE pm_drafts ADD COLUMN incidental INTEGER NOT NULL DEFAULT 0"
                )
            if "price_from_purchase_invoice" not in draft_columns:
                db.execute(
                    "ALTER TABLE pm_drafts ADD COLUMN price_from_purchase_invoice "
                    "INTEGER NOT NULL DEFAULT 0 CHECK(price_from_purchase_invoice IN(0,1))"
                )

    def sync_registered_suppliers(self, suppliers: Iterable[dict[str, Any]]) -> int:
        """Mirror central supplier-sync identities into the productmaker."""
        now = utc_now()
        synchronized = 0
        with self.connect() as db:
            for supplier in suppliers:
                if not supplier.get("enabled", 1):
                    continue
                slug = str(supplier.get("slug") or "").strip()
                name = str(supplier.get("name") or slug).strip()
                if not slug or not name:
                    continue
                domains = []
                for value in (
                    supplier.get("website_url"), supplier.get("source_location"),
                ):
                    if str(value or "").startswith(("http://", "https://")):
                        domain = normalized_host(str(value))
                        if domain and domain not in domains:
                            domains.append(domain)
                existing = db.execute(
                    "SELECT id,approved_domains_json FROM pm_suppliers "
                    "WHERE sync_slug=? OR name=? COLLATE NOCASE "
                    "ORDER BY sync_slug IS NOT NULL DESC LIMIT 1",
                    (slug, name),
                ).fetchone()
                if existing:
                    retained = json.loads(existing["approved_domains_json"] or "[]")
                    merged_domains = list(dict.fromkeys([*domains, *retained]))
                    db.execute(
                        """UPDATE pm_suppliers SET name=?,brand=?,sync_slug=?,
                           approved_domains_json=?,research_enabled=1,updated_at=?
                           WHERE id=?""",
                        (name, name, slug, json.dumps(merged_domains), now, existing["id"]),
                    )
                else:
                    db.execute(
                        """INSERT INTO pm_suppliers(
                           name,brand,approved_domains_json,research_enabled,
                           created_at,updated_at,sync_slug
                           ) VALUES(?,?,?,1,?,?,?)""",
                        (name, name, json.dumps(domains), now, now, slug),
                    )
                synchronized += 1
        return synchronized

    def save_supplier(
        self, name: str, approved_domains: str | Iterable[str], *, brand: str = "",
        supplier_id: int | None = None, research_enabled: bool = True,
    ) -> int:
        name = name.strip()
        domains = [normalized_host(item) for item in lines(approved_domains)]
        domains = [item for item in domains if item]
        if not name or not domains:
            raise ValueError("Leveranciersnaam en minimaal één goedgekeurd domein zijn verplicht")
        now = utc_now()
        with self.connect() as db:
            if supplier_id:
                db.execute(
                    """UPDATE pm_suppliers SET name=?,brand=?,approved_domains_json=?,
                       research_enabled=?,updated_at=? WHERE id=?""",
                    (name, brand.strip(), json.dumps(domains), int(research_enabled), now, int(supplier_id)),
                )
                return int(supplier_id)
            cursor = db.execute(
                """INSERT INTO pm_suppliers(name,brand,approved_domains_json,research_enabled,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                (name, brand.strip(), json.dumps(domains), int(research_enabled), now, now),
            )
            return int(cursor.lastrowid)

    def list_suppliers(self, *, synced_only: bool = False) -> list[dict[str, Any]]:
        with self.connect() as db:
            query = "SELECT * FROM pm_suppliers"
            if synced_only:
                query += " WHERE sync_slug IS NOT NULL"
            query += " ORDER BY name"
            result = [dict(row) for row in db.execute(query)]
        for item in result:
            item["approved_domains"] = json.loads(item.pop("approved_domains_json") or "[]")
        return result

    def get_supplier(self, supplier_id: int) -> dict[str, Any]:
        matches = [item for item in self.list_suppliers() if int(item["id"]) == int(supplier_id)]
        if not matches:
            raise ValueError("Leverancier niet gevonden")
        return matches[0]

    def mark_incidental(self, draft_id: int, incidental: bool = True) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE pm_drafts SET incidental=?,updated_at=? WHERE id=?",
                (int(incidental), utc_now(), int(draft_id)),
            )

    def delete_incidental_draft(self, draft_id: int) -> bool:
        """Remove a temporary product and its orphan temporary supplier."""
        with self.connect() as db:
            row = db.execute(
                "SELECT supplier_id,incidental FROM pm_drafts WHERE id=?",
                (int(draft_id),),
            ).fetchone()
            if not row or not row["incidental"]:
                return False
            supplier_id = row["supplier_id"]
            db.execute("DELETE FROM pm_audit WHERE draft_id=?", (int(draft_id),))
            db.execute("DELETE FROM pm_drafts WHERE id=?", (int(draft_id),))
            if supplier_id:
                db.execute(
                    """DELETE FROM pm_suppliers WHERE id=? AND sync_slug IS NULL
                       AND NOT EXISTS(
                         SELECT 1 FROM pm_drafts WHERE supplier_id=pm_suppliers.id
                       )""",
                    (int(supplier_id),),
                )
            return True

    @staticmethod
    def _money(value: Any, field: str, allow_empty: bool = False) -> str:
        if allow_empty and str(value or "").strip() == "":
            return ""
        try:
            amount = Decimal(str(value or "0").replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError(f"{field} is geen geldig bedrag") from exc
        if amount < 0:
            raise ValueError(f"{field} mag niet negatief zijn")
        return f"{amount:.2f}"

    def save_draft(self, draft_id: int | None = None, **values: Any) -> int:
        sku = str(values.get("sku") or "").strip()
        vendor = str(values.get("vendor") or "").strip()
        supplier_id = int(values["supplier_id"]) if values.get("supplier_id") else None
        if supplier_id and not vendor:
            supplier = self.get_supplier(supplier_id)
            vendor = str(supplier.get("brand") or supplier.get("name") or "").strip()
        if not sku:
            raise ValueError("SKU is verplicht")
        if not supplier_id and not vendor:
            raise ValueError("Merk/vendor is verplicht wanneer geen goedgekeurde leverancier is gekozen")
        ean = re.sub(r"\D", "", str(values.get("ean") or ""))
        if ean and len(ean) not in {8, 12, 13, 14}:
            raise ValueError("EAN/GTIN moet 8, 12, 13 of 14 cijfers bevatten")
        factor = Decimal(str(values.get("unit_factor") or "1").replace(",", "."))
        quantity = int(values.get("initial_quantity") or 0)
        if factor <= 0 or quantity < 0:
            raise ValueError("Omrekenfactor moet positief zijn en voorraad mag niet negatief zijn")
        now = utc_now()
        record = {
            "supplier_id": supplier_id,
            "sku": sku,"ean": ean,"manufacturer_number": str(values.get("manufacturer_number") or "").strip(),
            "vendor": vendor,"title": str(values.get("title") or "").strip(),
            "description_html": str(values.get("description_html") or "").strip(),
            "short_description": str(values.get("short_description") or "").strip(),
            "seo_title": str(values.get("seo_title") or "").strip(),
            "seo_description": str(values.get("seo_description") or "").strip(),
            "purchase_price": self._money(values.get("purchase_price"), "Inkoopprijs"),
            "sale_price": self._money(values.get("sale_price"), "Verkoopprijs"),
            "compare_at_price": self._money(values.get("compare_at_price"), "Vergelijkingsprijs", True),
            "initial_quantity": quantity,
            "purchase_unit": str(values.get("purchase_unit") or "stuk").strip(),
            "sales_unit": str(values.get("sales_unit") or "stuk").strip(),
            "unit_factor": str(factor.normalize()),"product_type": str(values.get("product_type") or "").strip(),
            "category_id": str(values.get("category_id") or "").strip(),
            "category_label": str(values.get("category_label") or "").strip(),
            "tags_json": json.dumps(lines(values.get("tags") or []), ensure_ascii=False),
            "metafields_json": json.dumps(values.get("metafields") or [], ensure_ascii=False),
            "source_url": str(values.get("source_url") or "").strip(),
            "notes": str(values.get("notes") or "").strip(),
            "price_from_purchase_invoice": int(bool(
                values.get("price_from_purchase_invoice", False)
            )),
            "updated_at": now,
        }
        columns = list(record)
        with self.connect() as db:
            if not draft_id:
                same_sku = db.execute(
                    "SELECT id,status,supplier_id FROM pm_drafts WHERE sku=? COLLATE NOCASE",
                    (sku,),
                ).fetchone()
                if same_sku:
                    if same_sku["status"] not in {"draft", "shopify_draft"}:
                        raise ValueError(
                            f"SKU {sku} bestaat al met status {same_sku['status']}"
                        )
                    if record["supplier_id"] is None and same_sku["supplier_id"]:
                        record["supplier_id"] = int(same_sku["supplier_id"])
                    draft_id = int(same_sku["id"])
            if draft_id:
                existing = db.execute("SELECT status FROM pm_drafts WHERE id=?", (int(draft_id),)).fetchone()
                if not existing:
                    raise ValueError("Productconcept niet gevonden")
                if existing["status"] == "publishing":
                    raise ValueError("Productconcept wordt momenteel gepubliceerd")
                db.execute(
                    f"UPDATE pm_drafts SET {','.join(f'{column}=?' for column in columns)} WHERE id=?",
                    (*[record[column] for column in columns], int(draft_id)),
                )
                self._audit(db, int(draft_id), "draft_updated", {"fields": columns})
                return int(draft_id)
            record["created_at"] = now
            columns = list(record)
            cursor = db.execute(
                f"INSERT INTO pm_drafts({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                [record[column] for column in columns],
            )
            draft_id = int(cursor.lastrowid)
            self._audit(db, draft_id, "draft_created", {"sku": sku})
            return draft_id

    def list_drafts(self, query: str = "") -> list[dict[str, Any]]:
        query = str(query or "").strip()
        with self.connect() as db:
            sql = """SELECT d.*,s.name supplier_name FROM pm_drafts d
                     LEFT JOIN pm_suppliers s ON s.id=d.supplier_id"""
            params: tuple[Any, ...] = ()
            if query:
                sql += """ WHERE d.sku LIKE ? OR d.ean LIKE ? OR d.manufacturer_number LIKE ?
                           OR d.vendor LIKE ? OR d.title LIKE ? OR s.name LIKE ?"""
                needle = f"%{query}%"
                params = (needle,) * 6
            sql += " ORDER BY d.updated_at DESC"
            return [dict(row) for row in db.execute(sql, params)]

    def automation_settings(self, draft_id: int) -> dict[str, bool]:
        defaults = {
            "source_research": True, "evidence_enrichment": True,
            "asset_collection": True, "category_suggestion": True,
            "quality_checks": True,
        }
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM pm_draft_automation WHERE draft_id=?", (int(draft_id),)
            ).fetchone()
        if not row:
            return defaults
        return {key: bool(row[key]) for key in defaults}

    def save_automation_settings(self, draft_id: int, **settings: Any) -> None:
        current = self.automation_settings(draft_id)
        current.update({key: bool(value) for key, value in settings.items() if key in current})
        with self.connect() as db:
            db.execute(
                """INSERT INTO pm_draft_automation(
                       draft_id,source_research,evidence_enrichment,asset_collection,
                       category_suggestion,quality_checks,updated_at
                   ) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(draft_id) DO UPDATE SET
                       source_research=excluded.source_research,
                       evidence_enrichment=excluded.evidence_enrichment,
                       asset_collection=excluded.asset_collection,
                       category_suggestion=excluded.category_suggestion,
                       quality_checks=excluded.quality_checks,updated_at=excluded.updated_at""",
                (int(draft_id), int(current["source_research"]),
                 int(current["evidence_enrichment"]), int(current["asset_collection"]),
                 int(current["category_suggestion"]), int(current["quality_checks"]), utc_now()),
            )

    def get_draft(self, draft_id: int) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                """SELECT d.*,s.name supplier_name,s.sync_slug supplier_sync_slug,
                          s.approved_domains_json
                   FROM pm_drafts d LEFT JOIN pm_suppliers s ON s.id=d.supplier_id WHERE d.id=?""",
                (int(draft_id),),
            ).fetchone()
        if not row:
            raise ValueError("Productconcept niet gevonden")
        result = dict(row)
        result["tags"] = json.loads(result.pop("tags_json") or "[]")
        result["metafields"] = json.loads(result.pop("metafields_json") or "[]")
        result["approved_domains"] = json.loads(result.pop("approved_domains_json") or "[]")
        result["evidence"] = self.list_evidence(draft_id)
        result["assets"] = self.list_assets(draft_id)
        return result

    def add_evidence(self, draft_id: int, field_name: str, value: Any, *, state: str,
                     source_url: str = "", source_title: str = "", source_excerpt: str = "",
                     matched_by: str = "", confidence: float = 0, approved: bool = False) -> int:
        if state not in EVIDENCE_STATES:
            raise ValueError("Ongeldige bewijsstatus")
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
        with self.connect() as db:
            db.execute(
                """INSERT INTO pm_evidence(draft_id,field_name,value_json,state,source_url,
                   source_title,source_excerpt,matched_by,confidence,retrieved_at,approved)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(draft_id,field_name,value_json,source_url)
                   DO UPDATE SET state=excluded.state,source_title=excluded.source_title,
                   source_excerpt=excluded.source_excerpt,matched_by=excluded.matched_by,
                   confidence=excluded.confidence,retrieved_at=excluded.retrieved_at""",
                (int(draft_id), field_name, value_json, state, source_url, source_title,
                 source_excerpt[:1200], matched_by, max(0, min(float(confidence), 1)), utc_now(), int(approved)),
            )
            row = db.execute(
                "SELECT id FROM pm_evidence WHERE draft_id=? AND field_name=? AND value_json=? AND source_url=?",
                (int(draft_id), field_name, value_json, source_url),
            ).fetchone()
            return int(row["id"])

    def approve_evidence(self, evidence_id: int, approved: bool = True) -> None:
        with self.connect() as db:
            db.execute("UPDATE pm_evidence SET approved=? WHERE id=?", (int(approved), int(evidence_id)))

    def approve_all_evidence(self, draft_id: int) -> int:
        """Approve all non-conflicting evidence for one standalone draft."""
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE pm_evidence SET approved=1
                   WHERE draft_id=? AND state<>'conflict' AND approved=0""",
                (int(draft_id),),
            )
            count = int(cursor.rowcount or 0)
            self._audit(db, int(draft_id), "all_evidence_approved", {"count": count})
            return count

    def list_evidence(self, draft_id: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM pm_evidence WHERE draft_id=? ORDER BY field_name,id", (int(draft_id),)
            )]
        for row in rows:
            row["value"] = json.loads(row.pop("value_json"))
        return rows

    def add_asset(self, draft_id: int, kind: str, url: str, *, title: str = "",
                  source_url: str = "", official: bool = False,
                  identifier_verified: bool = False, selected: bool = False) -> int:
        if kind not in {"image", "datasheet", "manual", "safety", "document"}:
            raise ValueError("Ongeldig bestandstype")
        with self.connect() as db:
            db.execute(
                """INSERT INTO pm_assets(draft_id,kind,url,title,source_url,official,
                   identifier_verified,selected,created_at) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(draft_id,kind,url) DO UPDATE SET title=excluded.title,
                   source_url=excluded.source_url,official=excluded.official,
                   identifier_verified=excluded.identifier_verified,
                   selected=MAX(pm_assets.selected,excluded.selected)""",
                (int(draft_id), kind, url, title, source_url, int(official),
                 int(identifier_verified), int(selected), utc_now()),
            )
            row = db.execute(
                "SELECT id FROM pm_assets WHERE draft_id=? AND kind=? AND url=?",
                (int(draft_id), kind, url),
            ).fetchone()
            return int(row["id"])

    def select_asset(self, asset_id: int, selected: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE pm_assets SET selected=? WHERE id=?", (int(selected), int(asset_id)))

    def list_assets(self, draft_id: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM pm_assets WHERE draft_id=? ORDER BY kind,id", (int(draft_id),)
            )]

    def save_uploaded_image(
        self, draft_id: int, filename: str, content: bytes, *, title: str = "",
    ) -> int:
        """Store a manually uploaded image and select it for publication."""
        suffix = Path(str(filename or "")).suffix.casefold()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("Gebruik een JPG-, PNG- of WebP-afbeelding")
        if not content:
            raise ValueError("De gekozen afbeelding is leeg")
        if len(content) > 20 * 1024 * 1024:
            raise ValueError("De afbeelding mag maximaal 20 MB groot zijn")
        upload_directory = DEFAULT_UPLOAD_DIRECTORY / str(int(draft_id))
        upload_directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(content).hexdigest()
        path = upload_directory / f"{digest}{suffix}"
        if not path.exists():
            path.write_bytes(content)
        return self.add_asset(
            draft_id, "image", str(path),
            title=title or Path(filename).stem or "Handmatig toegevoegde productfoto",
            source_url="handmatige upload", official=True,
            identifier_verified=True, selected=True,
        )

    def clear_source_material(
        self, draft_id: int, *, preserve_manual_uploads: bool = False,
    ) -> None:
        """Remove evidence and assets before rebuilding from another source route."""
        with self.connect() as db:
            db.execute("DELETE FROM pm_evidence WHERE draft_id=?", (int(draft_id),))
            if preserve_manual_uploads:
                db.execute(
                    "DELETE FROM pm_assets WHERE draft_id=? AND source_url<>'handmatige upload'",
                    (int(draft_id),),
                )
            else:
                db.execute("DELETE FROM pm_assets WHERE draft_id=?", (int(draft_id),))
            self._audit(
                db, int(draft_id), "source_material_cleared",
                {"preserve_manual_uploads": preserve_manual_uploads},
            )

    def quality_report(self, draft_id: int) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        approved_fields = {item["field_name"] for item in draft["evidence"] if item["approved"]}
        verified_source = any(
            item["field_name"] == "source_url"
            and item["state"] == "proven"
            and float(item.get("confidence") or 0) >= 1
            and str(item.get("source_url") or "").startswith("https://")
            and str(item.get("matched_by") or "") in {
                "sku", "ean", "manufacturer_number",
            }
            for item in draft["evidence"]
        )
        verified_supplier_pim = bool(draft.get("supplier_sync_slug")) and any(
            item["state"] == "proven"
            and item.get("approved")
            and float(item.get("confidence") or 0) >= 1
            and str(item.get("matched_by") or "") == "supplier_sync"
            for item in draft["evidence"]
        )
        conflicts = [item for item in draft["evidence"] if item["state"] == "conflict" and not item["approved"]]
        selected_images = [item for item in draft["assets"] if item["kind"] == "image" and item["selected"]]
        configured_metafields = {
            f"metafield.{item.get('namespace')}.{item.get('key')}"
            for item in draft["metafields"] if item.get("namespace") and item.get("key")
        }
        deferred_price = bool(draft.get("price_from_purchase_invoice"))
        checks = {
            "SKU aanwezig": bool(draft["sku"]),
            "Leverancier/merk aanwezig": bool(draft["vendor"]),
            "Titel aanwezig": bool(draft["title"]),
            "Omschrijving aanwezig": bool(draft["description_html"]),
            "Inkoopprijs geldig of komt uit inkoopfactuur": (
                Decimal(draft["purchase_price"]) > 0 or deferred_price
            ),
            "Verkoopprijs geldig of wacht op inkoopfactuur": (
                Decimal(draft["sale_price"]) > 0 or deferred_price
            ),
            "Eenheden compleet": bool(draft["purchase_unit"] and draft["sales_unit"] and Decimal(draft["unit_factor"]) > 0),
            "Officiële productbron": bool(
                verified_supplier_pim or (
                    draft["source_url"]
                    and ("source_url" in approved_fields or verified_source)
                )
            ),
            "Minimaal één goedgekeurde foto": bool(selected_images),
            "Productcategorie gekozen": bool(draft["category_id"]),
            "Ingevulde metafields bewezen": configured_metafields.issubset(approved_fields),
            "Geen open bronconflicten": not conflicts,
        }
        return {"ready": all(checks.values()), "checks": checks,
                "score": round(100 * sum(checks.values()) / len(checks)),
                "conflicts": conflicts}

    def refresh_purchase_invoice_price(
        self, draft_id: int, erp_database: str | Path = DEFAULT_ERP_DATABASE,
    ) -> dict[str, Any] | None:
        """Import the latest exact-SKU purchase price from the ERP invoice history."""
        draft = self.get_draft(draft_id)
        if not draft.get("price_from_purchase_invoice"):
            return None
        database = Path(erp_database)
        if not database.is_file():
            return None
        normalized_sku = re.sub(r"[^A-Z0-9]", "", str(draft["sku"]).upper())
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as erp:
            erp.row_factory = sqlite3.Row
            row = erp.execute(
                """SELECT supplier_name,supplier_article_number,shopify_sku,
                          invoice_number,invoice_date,unit_price,currency
                   FROM supplier_product_purchase_history
                   WHERE UPPER(REPLACE(REPLACE(REPLACE(shopify_sku,'.',''),'-',''),' ',''))=?
                   ORDER BY invoice_date DESC,id DESC LIMIT 1""",
                (normalized_sku,),
            ).fetchone()
        if not row:
            return None
        price = self._money(row["unit_price"], "Inkoopfactuurprijs")
        values = {
            key: draft.get(key) for key in (
                "supplier_id", "sku", "ean", "manufacturer_number", "vendor",
                "title", "description_html", "short_description", "seo_title",
                "seo_description", "purchase_price", "sale_price",
                "compare_at_price", "initial_quantity", "purchase_unit",
                "sales_unit", "unit_factor", "product_type", "category_id",
                "category_label", "tags", "metafields", "source_url", "notes",
                "price_from_purchase_invoice",
            )
        }
        values["purchase_price"] = price
        proposed_sale_price = ""
        if Decimal(str(draft.get("sale_price") or "0")) <= 0:
            proposed_sale_price = str(
                (Decimal(price) * Decimal("1.41")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP,
                )
            )
            values["sale_price"] = proposed_sale_price
        self.save_draft(draft_id, **values)
        self.add_evidence(
            draft_id, "purchase_price", price, state="proven",
            source_title=(
                f"Inkoopfactuur {row['invoice_number']} · {row['supplier_name']}"
            ),
            source_excerpt=(
                f"Factuurdatum {row['invoice_date']}; leveranciersartikel "
                f"{row['supplier_article_number']}; netto stuksprijs {price} "
                f"{row['currency'] or 'EUR'}."
            ),
            matched_by="exact_shopify_sku", confidence=1, approved=True,
        )
        if proposed_sale_price:
            self.add_evidence(
                draft_id, "sale_price", proposed_sale_price, state="proposed",
                source_title="Automatisch verkoopprijsvoorstel",
                source_excerpt=(
                    f"Geen verkoopprijs gekoppeld; inkoopprijs {price} × 1,41 = "
                    f"{proposed_sale_price}."
                ),
                matched_by="purchase_price_markup_1_41", confidence=1,
                approved=False,
            )
        return {
            **dict(row), "purchase_price": price,
            "proposed_sale_price": proposed_sale_price,
        }

    def start_research(self, draft_id: int, mode: str, query: str) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO pm_research_runs(draft_id,mode,status,query,started_at) VALUES(?,?,'running',?,?)",
                (int(draft_id), mode, query, utc_now()),
            )
            return int(cursor.lastrowid)

    def finish_research(self, run_id: int, status: str, result: dict[str, Any], message: str = "") -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE pm_research_runs SET status=?,result_json=?,message=?,finished_at=? WHERE id=?",
                (status, json.dumps(result, ensure_ascii=False), message, utc_now(), int(run_id)),
            )

    @staticmethod
    def _audit(db: sqlite3.Connection, draft_id: int | None, action: str, details: dict[str, Any]) -> None:
        db.execute(
            "INSERT INTO pm_audit(draft_id,action,details_json,created_at) VALUES(?,?,?,?)",
            (draft_id, action, json.dumps(details, ensure_ascii=False), utc_now()),
        )
