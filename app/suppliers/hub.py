from __future__ import annotations

import csv
import gzip
import hashlib
import html
import io
import json
import math
import os
import re
import sqlite3
import time
import http.cookiejar
import urllib.parse
import urllib.request
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd
from pypdf import PdfReader
from bs4 import BeautifulSoup, Comment, NavigableString
from cryptography.fernet import Fernet


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DATABASE_DIR = DATA_DIR / "database"
SUPPLIER_DIR = DATABASE_DIR / "suppliers"
IMPORT_DIR = DATA_DIR / "imports"
EXPORT_DIR = DATA_DIR / "output" / "suppliers"
PRODUCT_MAKER_UPLOAD_DIR = DATA_DIR / "product_maker_uploads"
PRODUCT_MAKER_SUPPLIER_ASSET_DIR = DATA_DIR / "product_maker_supplier_assets"
CONFIG_DIR = BASE_DIR / "config"
REGISTRY_PATH = DATABASE_DIR / "supplier_registry.sqlite"
KEY_PATH = CONFIG_DIR / ".supplier_secrets.key"

SHOPIFY_COLUMNS = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Product Category", "Type",
    "Tags", "Published", "Option1 Name", "Option1 Value", "Variant SKU",
    "Variant Grams", "Variant Inventory Tracker", "Variant Inventory Qty",
    "Variant Inventory Policy", "Variant Fulfillment Service", "Variant Price",
    "Variant Barcode", "Image Src", "Image Position", "SEO Title",
    "SEO Description", "Status",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not value:
        raise ValueError("Leveranciersnaam levert geen geldige code op.")
    return value[:64]


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_directories() -> None:
    for path in (DATABASE_DIR, SUPPLIER_DIR, IMPORT_DIR, EXPORT_DIR, CONFIG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _fernet() -> Fernet:
    _ensure_directories()
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
        os.chmod(KEY_PATH, 0o600)
    return Fernet(KEY_PATH.read_bytes().strip())


def _encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str | None) -> str:
    if not value:
        return ""
    return _fernet().decrypt(value.encode("ascii")).decode("utf-8")


def init_registry() -> None:
    _ensure_directories()
    with _connect(REGISTRY_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'xml_url',
                source_location TEXT,
                auth_type TEXT NOT NULL DEFAULT 'none',
                username_encrypted TEXT,
                secret_encrypted TEXT,
                request_options_json TEXT NOT NULL DEFAULT '{}',
                field_mapping_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_run_at TEXT,
                last_run_status TEXT,
                last_run_message TEXT
            );
            """
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(suppliers)").fetchall()
        }
        if "website_url" not in columns:
            conn.execute("ALTER TABLE suppliers ADD COLUMN website_url TEXT")
        if "ai_research_allowed" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN ai_research_allowed INTEGER NOT NULL DEFAULT 0"
            )
        if "catalogue_url" not in columns:
            conn.execute("ALTER TABLE suppliers ADD COLUMN catalogue_url TEXT")
        if "dealer_portal_url" not in columns:
            conn.execute("ALTER TABLE suppliers ADD COLUMN dealer_portal_url TEXT")
        if "dealer_username_encrypted" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN dealer_username_encrypted TEXT"
            )
        if "dealer_secret_encrypted" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN dealer_secret_encrypted TEXT"
            )
        if "website_match_policy" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN website_match_policy TEXT NOT NULL "
                "DEFAULT 'exact_sku_or_ean'"
            )
        if "available_stock_quantity" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN available_stock_quantity "
                "INTEGER NOT NULL DEFAULT 1"
            )
        if "shopify_field_mapping_json" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN shopify_field_mapping_json "
                "TEXT NOT NULL DEFAULT '{}'"
            )
        if "shopify_metafield_mapping_json" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN "
                "shopify_metafield_mapping_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "source_transformations_json" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN "
                "source_transformations_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "missing_products_to_draft" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN missing_products_to_draft "
                "INTEGER NOT NULL DEFAULT 1"
            )
        if "delete_missing_products" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN delete_missing_products "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "delete_missing_after_months" not in columns:
            conn.execute(
                "ALTER TABLE suppliers ADD COLUMN delete_missing_after_months "
                "INTEGER NOT NULL DEFAULT 2"
            )


def supplier_database_path(slug: str) -> Path:
    from app.suppliers.routes import supplier_route

    return SUPPLIER_DIR / supplier_route(slugify(slug)).database_filename


def init_supplier_database(slug: str) -> Path:
    path = supplier_database_path(slug)
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                sku TEXT PRIMARY KEY,
                supplier_sku TEXT,
                ean TEXT,
                vendor TEXT,
                brand TEXT,
                source_title TEXT,
                source_description TEXT,
                price REAL,
                sale_price REAL,
                cost_price REAL,
                purchase_unit TEXT NOT NULL DEFAULT 'stuk',
                sales_unit TEXT NOT NULL DEFAULT 'stuk',
                purchase_units_per_sales_unit REAL NOT NULL DEFAULT 1,
                unit_calculation_mode TEXT NOT NULL DEFAULT 'multiply',
                gross_purchase_price_per_kg REAL,
                purchase_discount_percent REAL,
                net_purchase_price_per_kg REAL,
                kg_per_purchase_unit REAL,
                kg_per_sales_unit REAL,
                weight_grams REAL,
                stock_quantity INTEGER,
                available INTEGER,
                product_type TEXT,
                category TEXT,
                category_full TEXT,
                source_updated_at TEXT,
                source_present INTEGER NOT NULL DEFAULT 1,
                ai_title TEXT,
                html_description TEXT,
                content_locked INTEGER NOT NULL DEFAULT 0,
                content_locked_at TEXT,
                shopify_handle TEXT,
                shopify_status TEXT NOT NULL DEFAULT 'draft',
                inventory_policy TEXT NOT NULL DEFAULT 'continue',
                raw_data_json TEXT NOT NULL DEFAULT '{}',
                content_hash TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS product_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                image_url TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 1,
                alt_text TEXT,
                UNIQUE(sku, image_url),
                FOREIGN KEY(sku) REFERENCES products(sku) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS import_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                source_hash TEXT,
                rows_seen INTEGER NOT NULL DEFAULT 0,
                inserted INTEGER NOT NULL DEFAULT 0,
                updated INTEGER NOT NULL DEFAULT 0,
                unchanged INTEGER NOT NULL DEFAULT 0,
                missing INTEGER NOT NULL DEFAULT 0,
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS product_families (
                family_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                family_json TEXT NOT NULL,
                website_url TEXT,
                ai_research_allowed INTEGER NOT NULL DEFAULT 0,
                website_match_policy TEXT NOT NULL DEFAULT 'exact_sku_or_ean',
                calculated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS product_family_variants (
                family_key TEXT NOT NULL,
                sku TEXT NOT NULL,
                variant_title TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (family_key, sku),
                FOREIGN KEY(family_key) REFERENCES product_families(family_key)
                    ON DELETE CASCADE,
                FOREIGN KEY(sku) REFERENCES products(sku) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS supplier_content_profiles (
                profile_key TEXT PRIMARY KEY,
                supplier_slug TEXT NOT NULL,
                name TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK(status IN ('draft','active','retired')),
                source_language TEXT NOT NULL,
                target_language TEXT NOT NULL,
                target_locale TEXT NOT NULL,
                brand_owner TEXT NOT NULL,
                manufacturer_attribution TEXT NOT NULL,
                publisher_attribution TEXT NOT NULL,
                profile_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS supplier_content_profile_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT NOT NULL,
                version INTEGER NOT NULL,
                profile_json TEXT NOT NULL,
                change_note TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(profile_key,version),
                FOREIGN KEY(profile_key) REFERENCES supplier_content_profiles(profile_key)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS content_section_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT NOT NULL,
                section_key TEXT NOT NULL,
                position INTEGER NOT NULL,
                title_nl TEXT NOT NULL,
                required INTEGER NOT NULL DEFAULT 1 CHECK(required IN (0,1)),
                html_element TEXT NOT NULL DEFAULT 'section',
                min_items INTEGER NOT NULL DEFAULT 0,
                max_items INTEGER,
                formatting_json TEXT NOT NULL DEFAULT '{}',
                content_rules_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(profile_key,section_key),
                FOREIGN KEY(profile_key) REFERENCES supplier_content_profiles(profile_key)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS translation_glossary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT NOT NULL,
                source_term TEXT NOT NULL,
                target_term TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT 'general',
                forbidden_translation TEXT,
                notes TEXT,
                locked INTEGER NOT NULL DEFAULT 1 CHECK(locked IN (0,1)),
                UNIQUE(profile_key,source_term,context),
                FOREIGN KEY(profile_key) REFERENCES supplier_content_profiles(profile_key)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS icon_translation_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT NOT NULL,
                source_label TEXT NOT NULL,
                target_label TEXT NOT NULL,
                icon_code TEXT NOT NULL,
                image_policy TEXT NOT NULL DEFAULT 'preserve_source_artwork',
                display_order INTEGER NOT NULL DEFAULT 0,
                max_label_length INTEGER NOT NULL DEFAULT 40,
                source_image_url TEXT,
                managed_image_url TEXT,
                UNIQUE(profile_key,source_label),
                FOREIGN KEY(profile_key) REFERENCES supplier_content_profiles(profile_key)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS document_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT NOT NULL,
                document_type TEXT NOT NULL,
                title_pattern TEXT NOT NULL,
                required_sections_json TEXT NOT NULL DEFAULT '[]',
                translation_policy_json TEXT NOT NULL DEFAULT '{}',
                image_policy_json TEXT NOT NULL DEFAULT '{}',
                render_policy_json TEXT NOT NULL DEFAULT '{}',
                quality_rules_json TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                UNIQUE(profile_key,document_type),
                FOREIGN KEY(profile_key) REFERENCES supplier_content_profiles(profile_key)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS publication_quality_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_key TEXT NOT NULL,
                rule_code TEXT NOT NULL,
                scope TEXT NOT NULL,
                severity TEXT NOT NULL CHECK(severity IN ('warning','blocker')),
                description_nl TEXT NOT NULL,
                validator_type TEXT NOT NULL,
                validator_config_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                UNIQUE(profile_key,rule_code),
                FOREIGN KEY(profile_key) REFERENCES supplier_content_profiles(profile_key)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS product_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                document_type TEXT NOT NULL,
                title TEXT NOT NULL,
                language TEXT NOT NULL,
                local_path TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                source_url TEXT,
                source_revision TEXT,
                profile_key TEXT,
                profile_version INTEGER,
                shopify_file_id TEXT,
                shopify_cdn_url TEXT,
                translation_status TEXT NOT NULL,
                quality_status TEXT NOT NULL DEFAULT 'pending',
                updated_at TEXT NOT NULL,
                UNIQUE(sku,document_type,language),
                FOREIGN KEY(sku) REFERENCES products(sku) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS product_document_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_document_id INTEGER NOT NULL,
                asset_type TEXT NOT NULL,
                source_page INTEGER,
                target_page INTEGER,
                source_name TEXT,
                local_path TEXT,
                sha256 TEXT,
                caption_nl TEXT,
                purpose TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(product_document_id) REFERENCES product_documents(id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS product_publication_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                profile_key TEXT NOT NULL,
                profile_version INTEGER NOT NULL,
                target TEXT NOT NULL,
                status TEXT NOT NULL,
                checks_json TEXT NOT NULL,
                artifact_hashes_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(sku) REFERENCES products(sku) ON DELETE CASCADE
            );
            """
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()
        }
        if "ai_tags_json" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN ai_tags_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "shopify_draft_since" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN shopify_draft_since TEXT"
            )
        if "content_locked" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN content_locked "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "content_locked_at" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN content_locked_at TEXT"
            )
        if "product_group_name" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN product_group_name TEXT"
            )
        if "filter_values_json" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN filter_values_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
        if "execution" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN execution TEXT"
            )
        for column in ("subcategory_3", "subcategory_4", "subcategory_5"):
            if column not in columns:
                conn.execute(
                    f'ALTER TABLE products ADD COLUMN "{column}" TEXT'
                )
        if "purchase_unit" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN purchase_unit "
                "TEXT NOT NULL DEFAULT 'stuk'"
            )
        if "sales_unit" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN sales_unit "
                "TEXT NOT NULL DEFAULT 'stuk'"
            )
        if "purchase_units_per_sales_unit" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN purchase_units_per_sales_unit "
                "REAL NOT NULL DEFAULT 1"
            )
        if "unit_calculation_mode" not in columns:
            conn.execute(
                "ALTER TABLE products ADD COLUMN unit_calculation_mode "
                "TEXT NOT NULL DEFAULT 'multiply'"
            )
        certilas_columns = {
            "gross_purchase_price_per_kg": "REAL",
            "purchase_discount_percent": "REAL",
            "net_purchase_price_per_kg": "REAL",
            "kg_per_purchase_unit": "REAL",
            "kg_per_sales_unit": "REAL",
        }
        for name, definition in certilas_columns.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE products ADD COLUMN {name} {definition}"
                )
        document_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(product_documents)").fetchall()
        }
        for name, definition in {
            "profile_key": "TEXT",
            "profile_version": "INTEGER",
            "quality_status": "TEXT NOT NULL DEFAULT 'pending'",
        }.items():
            if name not in document_columns:
                conn.execute(
                    f"ALTER TABLE product_documents ADD COLUMN {name} {definition}"
                )
    return path


def list_suppliers() -> list[dict[str, Any]]:
    init_registry()
    with _connect(REGISTRY_PATH) as conn:
        rows = conn.execute(
            "SELECT * FROM suppliers ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(row) for row in rows]


def get_supplier(slug: str, include_credentials: bool = False) -> dict[str, Any] | None:
    init_registry()
    with _connect(REGISTRY_PATH) as conn:
        row = conn.execute("SELECT * FROM suppliers WHERE slug=?", (slug,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["request_options"] = json.loads(result.pop("request_options_json") or "{}")
    result["field_mapping"] = json.loads(result.pop("field_mapping_json") or "{}")
    result["shopify_field_mapping"] = json.loads(
        result.pop("shopify_field_mapping_json", "{}") or "{}"
    )
    result["shopify_metafield_mapping"] = json.loads(
        result.pop("shopify_metafield_mapping_json", "{}") or "{}"
    )
    result["source_transformations"] = json.loads(
        result.pop("source_transformations_json", "{}") or "{}"
    )
    result["has_username"] = bool(result.get("username_encrypted"))
    result["has_secret"] = bool(result.get("secret_encrypted"))
    result["has_dealer_username"] = bool(result.get("dealer_username_encrypted"))
    result["has_dealer_secret"] = bool(result.get("dealer_secret_encrypted"))
    if include_credentials:
        result["username"] = _decrypt(result.get("username_encrypted"))
        result["secret"] = _decrypt(result.get("secret_encrypted"))
        result["dealer_username"] = _decrypt(
            result.get("dealer_username_encrypted")
        )
        result["dealer_secret"] = _decrypt(result.get("dealer_secret_encrypted"))
    result.pop("username_encrypted", None)
    result.pop("secret_encrypted", None)
    result.pop("dealer_username_encrypted", None)
    result.pop("dealer_secret_encrypted", None)
    return result


def save_shopify_field_mapping(
    slug: str, mapping: dict[str, str]
) -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as conn:
        cursor = conn.execute(
            """
            UPDATE suppliers SET shopify_field_mapping_json=?,updated_at=?
            WHERE slug=?
            """,
            (json.dumps(mapping, ensure_ascii=False), utc_now(), slug),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Onbekende leverancier: {slug}")


def save_shopify_metafield_mapping(
    slug: str, mapping: dict[str, dict[str, str]]
) -> None:
    init_registry()
    cleaned: dict[str, dict[str, str]] = {}
    for identifier, config in mapping.items():
        source_field = str(config.get("source_field") or "").strip()
        if not source_field:
            continue
        owner = str(config.get("owner") or "").lower()
        namespace = str(config.get("namespace") or "").strip()
        key = str(config.get("key") or "").strip()
        metafield_type = str(config.get("type") or "").strip()
        if owner not in {"product", "variant"} or not namespace or not key:
            continue
        cleaned[identifier] = {
            "owner": owner,
            "namespace": namespace,
            "key": key,
            "type": metafield_type,
            "name": str(config.get("name") or "").strip(),
            "source_field": source_field,
        }
    with _connect(REGISTRY_PATH) as conn:
        cursor = conn.execute(
            """
            UPDATE suppliers SET shopify_metafield_mapping_json=?,updated_at=?
            WHERE slug=?
            """,
            (json.dumps(cleaned, ensure_ascii=False), utc_now(), slug),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Onbekende leverancier: {slug}")


def save_unified_source_mappings(
    slug: str,
    *,
    pim_mapping: dict[str, str],
    shopify_mapping: dict[str, str],
    metafield_mapping: dict[str, dict[str, str]],
) -> None:
    if not get_supplier(slug):
        raise ValueError(f"Onbekende leverancier: {slug}")
    clean_pim = {
        target: str(source or "") for target, source in pim_mapping.items()
        if target in DEFAULT_MAPPING
    }
    clean_shopify = {
        str(target): str(source) for target, source in shopify_mapping.items()
        if target and source
    }
    clean_meta = {}
    for identifier, config in metafield_mapping.items():
        if (
            config.get("owner") in {"product", "variant"}
            and config.get("namespace")
            and config.get("key")
            and config.get("source_field")
        ):
            clean_meta[identifier] = config
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            """
            UPDATE suppliers SET field_mapping_json=?,
                shopify_field_mapping_json=?,
                shopify_metafield_mapping_json=?,updated_at=?
            WHERE slug=?
            """,
            (
                json.dumps(clean_pim, ensure_ascii=False),
                json.dumps(clean_shopify, ensure_ascii=False),
                json.dumps(clean_meta, ensure_ascii=False),
                utc_now(), slug,
            ),
        )
    refresh_product_filters(slug)


def save_source_transformations(
    slug: str, transformations: dict[str, dict[str, Any]]
) -> None:
    if not get_supplier(slug):
        raise ValueError(f"Onbekende leverancier: {slug}")
    cleaned = {
        str(field): dict(config)
        for field, config in transformations.items()
        if field and any(value not in (None, "", False, []) for value in config.values())
    }
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            """
            UPDATE suppliers SET source_transformations_json=?,updated_at=?
            WHERE slug=?
            """,
            (json.dumps(cleaned, ensure_ascii=False), utc_now(), slug),
        )


def _number_for_calculation(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _calculation_operand(
    operand: dict[str, Any], record: dict[str, Any]
) -> float | None:
    if operand.get("kind") == "field":
        return _number_for_calculation(
            record.get(str(operand.get("value") or ""))
        )
    return _number_for_calculation(operand.get("value"))


def _evaluate_calculation(
    calculation: dict[str, Any], record: dict[str, Any]
) -> float | None:
    result = _calculation_operand(
        calculation.get("start") or {}, record
    )
    if result is None:
        return None
    for step in calculation.get("steps") or []:
        operand = _calculation_operand(
            step.get("operand") or {}, record
        )
        if operand is None:
            return None
        operator = step.get("operator")
        if operator == "+":
            result += operand
        elif operator == "-":
            result -= operand
        elif operator == "*":
            result *= operand
        elif operator == "/":
            if operand == 0:
                return None
            result /= operand
        else:
            return None
    return result


def _clean_product_description_html(
    value: str, options: dict[str, Any]
) -> str:
    if not value or not re.search(r"<[a-zA-Z][^>]*>", value):
        return value

    soup = BeautifulSoup(value, "html.parser")
    for node in soup.find_all(string=lambda item: isinstance(item, Comment)):
        node.extract()
    for tag in soup.find_all(["script", "style", "iframe", "object"]):
        tag.decompose()

    if options.get("remove_attributes", True):
        for tag in soup.find_all(True):
            if tag.name == "a":
                tag.attrs = {
                    key: item for key, item in tag.attrs.items()
                    if key in {"href", "title"}
                }
            else:
                tag.attrs = {}

    for node in soup.find_all(string=True):
        if not isinstance(node, NavigableString):
            continue
        normalized = re.sub(r"\s+", " ", str(node).replace("\xa0", " "))
        if normalized != str(node):
            node.replace_with(normalized)

    if options.get("breaks_to_paragraphs", True):
        for paragraph in list(soup.find_all("p")):
            if not paragraph.find("br"):
                continue
            parts = re.split(
                r"<br\s*/?>", paragraph.decode_contents(),
                flags=re.IGNORECASE,
            )
            meaningful_parts = [
                part.strip() for part in parts
                if BeautifulSoup(part, "html.parser").get_text(
                    " ", strip=True
                )
            ]
            if len(meaningful_parts) < 2:
                continue
            for part in meaningful_parts:
                fragment = BeautifulSoup(part, "html.parser")
                text = fragment.get_text(" ", strip=True)
                strong = fragment.find("strong")
                is_heading = (
                    strong is not None
                    and text.rstrip(":").casefold()
                    in {
                        "belangrijkste kenmerken",
                        "technische kenmerken",
                        "technische gegevens",
                    }
                )
                replacement = soup.new_tag("h3" if is_heading else "p")
                if is_heading:
                    replacement.string = text.rstrip(":")
                else:
                    for child in list(fragment.contents):
                        replacement.append(child)
                paragraph.insert_before(replacement)
            paragraph.decompose()

    if options.get("merge_feature_lists", True):
        for listing in list(soup.find_all("ul")):
            items = listing.find_all("li", recursive=False)
            if len(items) != 1 or not items[0].find("strong"):
                continue
            sibling = listing.find_next_sibling()
            if sibling and sibling.name == "p" and sibling.get_text(
                " ", strip=True
            ):
                item = items[0]
                while item.contents and (
                    (
                        isinstance(item.contents[-1], NavigableString)
                        and not str(item.contents[-1]).strip()
                    )
                    or getattr(item.contents[-1], "name", None) == "br"
                ):
                    item.contents[-1].extract()
                item.append(soup.new_tag("br"))
                for child in list(sibling.contents):
                    item.append(child)
                sibling.decompose()
        listings = list(soup.find_all("ul"))
        for listing in listings:
            previous = listing.find_previous_sibling()
            if previous and previous.name == "ul":
                for item in list(listing.find_all("li", recursive=False)):
                    previous.append(item)
                listing.decompose()

    if options.get("remove_empty", True):
        changed = True
        while changed:
            changed = False
            for tag in list(soup.find_all(
                ["p", "ul", "ol", "li", "strong", "em", "h2", "h3"]
            )):
                if not tag.get_text(" ", strip=True) and not tag.find("img"):
                    tag.decompose()
                    changed = True

    for strong in soup.find_all("strong"):
        strong_text = strong.get_text(" ", strip=True)
        strong.clear()
        strong.string = strong_text
    for item in soup.find_all("li"):
        while item.contents and (
            (
                isinstance(item.contents[-1], NavigableString)
                and not str(item.contents[-1]).strip()
            )
            or getattr(item.contents[-1], "name", None) == "br"
        ):
            item.contents[-1].extract()
    if options.get("merge_feature_lists", True):
        for listing in list(soup.find_all("ul")):
            previous = listing.find_previous_sibling()
            if previous and previous.name == "ul":
                for item in list(listing.find_all("li", recursive=False)):
                    previous.append(item)
                listing.decompose()

    allowed_tags = {
        "p", "ul", "ol", "li", "strong", "em", "br", "h2", "h3", "a"
    }
    for tag in list(soup.find_all(True)):
        if tag.name not in allowed_tags:
            tag.unwrap()

    result = str(soup).strip()
    result = re.sub(r">\s+<", "><", result)
    result = re.sub(r"\s+</", "</", result)
    return result


def _compose_source_fields(
    collection: dict[str, Any], record: dict[str, Any]
) -> str:
    parts = []
    for item in collection.get("items") or []:
        field = str(item.get("field") or "").strip()
        value = record.get(field)
        if not field or value in (None, ""):
            continue
        parts.append({
            "label": str(item.get("label") or field).strip(),
            "value": str(value).strip(),
            "unit": str(item.get("unit") or "").strip(),
        })
    output_format = collection.get("format") or "lines"
    if output_format == "template":
        template = str(collection.get("template") or "")

        def render_condition(match: re.Match) -> str:
            field = match.group(1).strip()
            return (
                match.group(2)
                if record.get(field) not in (None, "") else ""
            )

        rendered = re.sub(
            r"{%\s*if\s+([^%{}]+?)\s*%}(.*?){%\s*endif\s*%}",
            render_condition,
            template,
            flags=re.DOTALL | re.IGNORECASE,
        )
        rendered = re.sub(
            r"{{\s*([^{}]+?)\s*}}",
            lambda match: html.escape(
                str(record.get(match.group(1).strip()) or "").strip()
            ),
            rendered,
        )
        soup = BeautifulSoup(rendered, "html.parser")
        for tag in soup.find_all(["script", "style", "iframe", "object"]):
            tag.decompose()
        allowed_tags = {
            "p", "br", "strong", "b", "em", "i", "u", "s",
            "h2", "h3", "h4", "h5", "h6",
            "ul", "ol", "li", "dl", "dt", "dd",
            "table", "thead", "tbody", "tfoot", "tr", "th", "td",
            "div", "span", "a", "hr", "sup", "sub",
        }
        for tag in list(soup.find_all(True)):
            if tag.name not in allowed_tags:
                tag.unwrap()
                continue
            if tag.name == "a":
                tag.attrs = {
                    key: value for key, value in tag.attrs.items()
                    if key in {"href", "title", "target"}
                    and not (
                        key == "href"
                        and str(value).strip().lower().startswith(
                            ("javascript:", "data:")
                        )
                    )
                }
            else:
                tag.attrs = {}
        return re.sub(r">\s+<", "><", str(soup).strip())
    if not parts:
        return ""
    if output_format == "dimensions":
        values = [part["value"] for part in parts]
        unit = str(collection.get("unit") or "").strip()
        result = " × ".join(values)
        return f"{result} {unit}".strip()
    if output_format == "html_list":
        rows = "".join(
            f"<li><strong>{html.escape(part['label'])}:</strong> "
            f"{html.escape(part['value'])}"
            f"{(' ' + html.escape(part['unit'])) if part['unit'] else ''}"
            "</li>"
            for part in parts
        )
        return f"<ul>{rows}</ul>"
    if output_format == "text_symbols":
        text_symbols = {
            "small_check": "✓",
            "large_green_check": "✅",
            "green_checkbox": "✅",
            "green_dot": "🟢",
            "blue_arrow": "➡️",
            "gold_star": "⭐",
            "bullet": "•",
            "none": "",
        }
        symbol = text_symbols.get(
            str(collection.get("symbol") or "small_check"), "✓"
        )
        prefix = f"{symbol} " if symbol else ""
        return "\n".join(
            f"{prefix}{part['label']}: {part['value']}"
            f"{(' ' + part['unit']) if part['unit'] else ''}"
            for part in parts
        )
    separator = (
        str(collection.get("separator") or " · ")
        if output_format == "text" else "\n"
    )
    return separator.join(
        f"{part['label']}: {part['value']}"
        f"{(' ' + part['unit']) if part['unit'] else ''}"
        for part in parts
    )


def _nested_name_values(value: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []

    def scalar_values(item: Any) -> list[str]:
        if isinstance(item, dict):
            result = []
            for nested in item.values():
                result.extend(scalar_values(nested))
            return result
        if isinstance(item, list):
            result = []
            for nested in item:
                result.extend(scalar_values(nested))
            return result
        text = str(item or "").strip()
        return [text] if text else []

    def walk(item: Any) -> None:
        if isinstance(item, list):
            for nested in item:
                walk(nested)
            return
        if not isinstance(item, dict):
            return
        normalized = {
            str(key).casefold(): nested
            for key, nested in item.items()
        }
        if "name" in normalized and (
            "values" in normalized or "value" in normalized
        ):
            name_values = scalar_values(normalized["name"])
            values = scalar_values(
                normalized.get("values", normalized.get("value"))
            )
            if name_values and values:
                entries.append({
                    "name": name_values[0],
                    "value": ", ".join(dict.fromkeys(values)),
                })
            return
        for nested in item.values():
            walk(nested)

    walk(value)
    return entries


def _format_nested_field(
    value: Any, config: dict[str, Any]
) -> str:
    entries = _nested_name_values(value)
    if not entries:
        return ""
    output_format = config.get("format") or "html_list"
    if output_format == "lines":
        return "\n".join(
            f"{entry['name']}: {entry['value']}"
            for entry in entries
        )
    if output_format == "text_symbols":
        text_symbols = {
            "small_check": "✓",
            "large_green_check": "✅",
            "green_checkbox": "✅",
            "green_dot": "🟢",
            "blue_arrow": "➡️",
            "gold_star": "⭐",
            "bullet": "•",
            "none": "",
        }
        symbol = text_symbols.get(
            str(config.get("symbol") or "small_check"), "✓"
        )
        prefix = f"{symbol} " if symbol else ""
        return "\n".join(
            f"{prefix}{entry['name']}: {entry['value']}"
            for entry in entries
        )
    if output_format == "custom":
        before = str(config.get("before") or "")
        after = str(config.get("after") or "")
        item_template = str(
            config.get("item_template")
            or "<li><strong>{{ name }}:</strong> {{ value }}</li>"
        )
        rows = []
        for entry in entries:
            row = item_template.replace(
                "{{ name }}", html.escape(entry["name"])
            ).replace(
                "{{ value }}", html.escape(entry["value"])
            )
            rows.append(row)
        rendered = f"{before}{''.join(rows)}{after}"
        soup = BeautifulSoup(rendered, "html.parser")
        for tag in soup.find_all(["script", "style", "iframe", "object"]):
            tag.decompose()
        allowed = {
            "p", "br", "strong", "b", "em", "i", "u", "h2", "h3",
            "h4", "ul", "ol", "li", "dl", "dt", "dd", "div", "span",
            "table", "thead", "tbody", "tr", "th", "td", "hr",
        }
        for tag in list(soup.find_all(True)):
            if tag.name not in allowed:
                tag.unwrap()
            else:
                tag.attrs = {}
        return re.sub(r">\s+<", "><", str(soup).strip())
    symbols = {
        "small_check": "✓ ",
        "large_green_check": (
            '<span style="color:#16a34a;font-size:1.35em;'
            'font-weight:700;line-height:1">✔</span> '
        ),
        "green_checkbox": "✅ ",
        "green_dot": (
            '<span style="color:#16a34a;font-size:1.2em;'
            'line-height:1">●</span> '
        ),
        "blue_arrow": (
            '<span style="color:#2563eb;font-size:1.2em;'
            'font-weight:700;line-height:1">➜</span> '
        ),
        "gold_star": (
            '<span style="color:#d97706;font-size:1.2em;'
            'line-height:1">★</span> '
        ),
        "bullet": "• ",
        "none": "",
    }
    checkmark = (
        symbols.get(str(config.get("symbol") or "small_check"), "✓ ")
        if output_format == "checklist" else ""
    )
    list_item_style = (
        ' style="list-style-type:none"'
        if output_format == "checklist" else ""
    )
    rows = "".join(
        f"<li{list_item_style}>{checkmark}"
        f"<strong>{html.escape(entry['name'])}:</strong> "
        f"{html.escape(entry['value'])}</li>"
        for entry in entries
    )
    list_style = (
        ' style="list-style-type:none;padding-left:0;margin-left:0"'
        if output_format == "checklist" else ""
    )
    return f"<ul{list_style}>{rows}</ul>"


def apply_source_transformations(
    record: dict[str, Any],
    transformations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    transformed = dict(record)
    for field, config in transformations.items():
        operational_keys = {
            "calculation", "multiply_fields", "value_map",
            "remove_control", "strip_html", "normalize_spaces", "trim",
            "replacements", "find", "case", "prefix", "suffix",
            "decimal_comma_to_point", "output_field", "html_cleanup",
            "field_collection", "nested_field",
        }
        if not any(
            key in config
            and config.get(key) not in (None, "", False, [], {})
            for key in operational_keys
        ):
            # Instellingen zoals content_type sturen de import, maar mogen
            # de oorspronkelijke (mogelijk meervoudige) veldwaarde niet
            # naar tekst converteren.
            continue
        value = transformed.get(field)
        field_collection = config.get("field_collection")
        if isinstance(field_collection, dict):
            value = _compose_source_fields(
                field_collection, transformed
            )
        nested_field = config.get("nested_field")
        if isinstance(nested_field, dict):
            value = _format_nested_field(value, nested_field)
        calculation = config.get("calculation")
        if isinstance(calculation, dict) and calculation.get("start"):
            calculated = _evaluate_calculation(
                calculation, transformed
            )
            if calculated is not None:
                value = f"{calculated:.12g}"
        multiply_fields = [
            str(item) for item in config.get("multiply_fields") or []
        ]
        if multiply_fields and not calculation:
            numbers = [
                _number_for_calculation(record.get(item))
                for item in multiply_fields
            ]
            if all(number is not None for number in numbers):
                result = 1.0
                for number in numbers:
                    result *= float(number)
                divisor = _number_for_calculation(
                    config.get("divide_by")
                ) or 1
                value = result / divisor
        value_map = {
            str(key).strip().casefold(): mapped
            for key, mapped in (config.get("value_map") or {}).items()
        }
        normalized_value = str(value or "").strip().casefold()
        if normalized_value in value_map:
            value = value_map[normalized_value]
        text = "" if value is None else str(value)
        html_cleanup = config.get("html_cleanup")
        if isinstance(html_cleanup, dict) and html_cleanup.get("enabled"):
            text = _clean_product_description_html(text, html_cleanup)
        if config.get("remove_control"):
            text = "".join(
                character for character in text
                if unicodedata.category(character) not in {"Cc", "Cf"}
            )
        if config.get("strip_html"):
            text = re.sub(r"<[^>]+>", " ", text)
        if config.get("normalize_spaces"):
            text = re.sub(r"\s+", " ", text)
        if config.get("trim"):
            text = text.strip()
        replacements = config.get("replacements")
        if not isinstance(replacements, list):
            replacements = [{
                "find": config.get("find") or "",
                "replace": config.get("replace") or "",
            }]
        for replacement in replacements:
            find_text = str(replacement.get("find") or "")
            if find_text:
                text = text.replace(
                    find_text, str(replacement.get("replace") or "")
                )
        case_mode = config.get("case")
        if case_mode == "upper":
            text = text.upper()
        elif case_mode == "lower":
            text = text.lower()
        elif case_mode == "title":
            text = text.title()
        prefix = str(config.get("prefix") or "")
        suffix = str(config.get("suffix") or "")
        if prefix:
            if config.get("prefix_mode") == "remove":
                text = text.removeprefix(prefix)
            else:
                text = f"{prefix}{text}"
        if suffix:
            if config.get("suffix_mode") == "remove":
                text = text.removesuffix(suffix)
            else:
                text = f"{text}{suffix}"
        if config.get("decimal_comma_to_point"):
            text = text.replace(",", ".")
        output_field = str(config.get("output_field") or field).strip()
        transformed[output_field or field] = text
    return transformed


def save_source_field_mapping(slug: str, mapping: dict[str, str]) -> None:
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    merged = {**(supplier.get("field_mapping") or {})}
    for target, source in mapping.items():
        if target in DEFAULT_MAPPING:
            # Een lege waarde is een bewuste keuze voor 'Niet koppelen'.
            # Bewaar deze override, anders valt de import terug op de
            # gelijknamige DEFAULT_MAPPING en wordt het veld alsnog gevuld.
            merged[target] = source or ""
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            "UPDATE suppliers SET field_mapping_json=?,updated_at=? WHERE slug=?",
            (json.dumps(merged, ensure_ascii=False), utc_now(), slug),
        )
    refresh_product_filters(slug)


def save_source_field_mapping_locks(
    slug: str, locked_mapping: dict[str, str]
) -> None:
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    request_options = dict(supplier.get("request_options") or {})
    request_options["locked_field_mapping"] = {
        target: str(source or "")
        for target, source in locked_mapping.items()
        if target in DEFAULT_MAPPING
    }
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            """
            UPDATE suppliers SET request_options_json=?,updated_at=?
            WHERE slug=?
            """,
            (
                json.dumps(request_options, ensure_ascii=False),
                utc_now(), slug,
            ),
        )


def save_excel_header_row(slug: str, row_number: int) -> None:
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    row_number = int(row_number)
    if not 1 <= row_number <= 10_000:
        raise ValueError("De regel met kolomnamen moet tussen 1 en 10.000 liggen.")
    request_options = dict(supplier.get("request_options") or {})
    request_options["excel_header_row"] = row_number
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            "UPDATE suppliers SET request_options_json=?,updated_at=? WHERE slug=?",
            (json.dumps(request_options, ensure_ascii=False), utc_now(), slug),
        )


def save_sku_prefix(slug: str, prefix: str) -> None:
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    request_options = dict(supplier.get("request_options") or {})
    request_options["sku_prefix"] = _text(prefix).upper()
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            "UPDATE suppliers SET request_options_json=?,updated_at=? WHERE slug=?",
            (json.dumps(request_options, ensure_ascii=False), utc_now(), slug),
        )


def save_supplier(
    *,
    name: str,
    slug: str | None = None,
    source_type: str = "xml_url",
    source_location: str = "",
    auth_type: str = "none",
    username: str | None = None,
    secret: str | None = None,
    request_options: dict[str, Any] | None = None,
    field_mapping: dict[str, str] | None = None,
    website_url: str = "",
    catalogue_url: str = "",
    dealer_portal_url: str = "",
    dealer_username: str | None = None,
    dealer_secret: str | None = None,
    ai_research_allowed: bool = False,
    website_match_policy: str = "exact_sku_or_ean",
) -> str:
    init_registry()
    supplier_slug = slugify(slug or name)
    now = utc_now()
    with _connect(REGISTRY_PATH) as conn:
        current = conn.execute(
            """SELECT username_encrypted,secret_encrypted,
               dealer_username_encrypted,dealer_secret_encrypted
               FROM suppliers WHERE slug=?""",
            (supplier_slug,),
        ).fetchone()
        encrypted_username = (
            _encrypt(username) if username is not None
            else (current["username_encrypted"] if current else None)
        )
        encrypted_secret = (
            _encrypt(secret) if secret is not None
            else (current["secret_encrypted"] if current else None)
        )
        encrypted_dealer_username = (
            _encrypt(dealer_username) if dealer_username is not None
            else (current["dealer_username_encrypted"] if current else None)
        )
        encrypted_dealer_secret = (
            _encrypt(dealer_secret) if dealer_secret is not None
            else (current["dealer_secret_encrypted"] if current else None)
        )
        conn.execute(
            """
            INSERT INTO suppliers (
                slug, name, source_type, source_location, auth_type,
                username_encrypted, secret_encrypted, request_options_json,
                field_mapping_json, website_url, ai_research_allowed,
                website_match_policy, catalogue_url, dealer_portal_url,
                dealer_username_encrypted, dealer_secret_encrypted,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name,
                source_type=excluded.source_type,
                source_location=excluded.source_location,
                auth_type=excluded.auth_type,
                username_encrypted=excluded.username_encrypted,
                secret_encrypted=excluded.secret_encrypted,
                request_options_json=excluded.request_options_json,
                field_mapping_json=excluded.field_mapping_json,
                website_url=excluded.website_url,
                ai_research_allowed=excluded.ai_research_allowed,
                website_match_policy=excluded.website_match_policy,
                catalogue_url=excluded.catalogue_url,
                dealer_portal_url=excluded.dealer_portal_url,
                dealer_username_encrypted=excluded.dealer_username_encrypted,
                dealer_secret_encrypted=excluded.dealer_secret_encrypted,
                updated_at=excluded.updated_at
            """,
            (
                supplier_slug, name.strip(), source_type, source_location.strip(),
                auth_type, encrypted_username, encrypted_secret,
                json.dumps(request_options or {}, ensure_ascii=False),
                json.dumps(field_mapping or {}, ensure_ascii=False),
                website_url.strip(), int(ai_research_allowed),
                website_match_policy, catalogue_url.strip(),
                dealer_portal_url.strip(), encrypted_dealer_username,
                encrypted_dealer_secret, now, now,
            ),
        )
    init_supplier_database(supplier_slug)
    return supplier_slug


def _stock_is_available(value: Any, supplier: dict[str, Any]) -> bool:
    options = supplier.get("request_options") or {}
    mode = options.get("stock_availability_mode") or "numeric_positive"
    selected = {
        str(item).strip().casefold()
        for item in options.get("stock_availability_values") or []
        if str(item).strip()
    }
    normalized = str(value or "").strip().casefold()
    if mode == "value_actions":
        actions = {
            str(source).strip().casefold(): str(action)
            for source, action in (
                options.get("stock_value_actions") or {}
            ).items()
        }
        return actions.get(normalized) == "available"
    if mode == "selected_available":
        return normalized in selected
    if mode == "selected_unavailable":
        return normalized not in selected
    return (_integer(value) or 0) > 0


def _stock_stays_active_at_zero(value: Any, supplier: dict[str, Any]) -> bool:
    options = supplier.get("request_options") or {}
    normalized = str(value or "").strip().casefold()
    actions = {
        str(source).strip().casefold(): str(action)
        for source, action in (options.get("stock_value_actions") or {}).items()
    }
    return (
        actions.get(normalized) == "active_zero"
        or bool(options.get("keep_active_when_out_of_stock"))
    )


def save_inventory_mapping(
    slug: str,
    available_stock_quantity: int,
    *,
    inventory_location_id: str | None = None,
    availability_mode: str | None = None,
    availability_values: list[str] | None = None,
    availability_actions: dict[str, str] | None = None,
    continue_selling_when_out_of_stock: bool | None = None,
    continue_selling_collection_rules: list[dict[str, Any]] | None = None,
    delivery_time_notice_text: str | None = None,
    draft_only_when_no_location_stock: bool | None = None,
    keep_active_when_out_of_stock: bool | None = None,
    apply_existing: bool = True,
) -> dict[str, int]:
    quantity = int(available_stock_quantity)
    if quantity < 1 or quantity > 1_000_000:
        raise ValueError("Voorraad bij bronwaarde 1 moet tussen 1 en 1.000.000 liggen.")
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    request_options = dict(supplier.get("request_options") or {})
    if continue_selling_when_out_of_stock is not None:
        request_options["continue_selling_when_out_of_stock"] = bool(
            continue_selling_when_out_of_stock
        )
    if continue_selling_collection_rules is not None:
        request_options["continue_selling_collection_rules"] = [
            {
                "collection_id": str(rule.get("collection_id") or "").strip(),
                "collection_title": str(
                    rule.get("collection_title") or ""
                ).strip()[:255],
                "continue_selling": bool(rule.get("continue_selling")),
                "exclude": bool(rule.get("exclude")),
            }
            for rule in continue_selling_collection_rules
            if str(rule.get("collection_id") or "").strip()
        ]
    if delivery_time_notice_text is not None:
        request_options["delivery_time_notice_text"] = str(
            delivery_time_notice_text
        ).strip()[:255]
    if draft_only_when_no_location_stock is not None:
        request_options["draft_only_when_no_location_stock"] = bool(
            draft_only_when_no_location_stock
        )
    if keep_active_when_out_of_stock is not None:
        request_options["keep_active_when_out_of_stock"] = bool(
            keep_active_when_out_of_stock
        )
    if availability_mode is not None:
        if availability_mode not in {
            "numeric_positive", "selected_available",
            "selected_unavailable", "value_actions",
        }:
            raise ValueError("Onbekende voorraadwaarderegel.")
        request_options["stock_availability_mode"] = availability_mode
        request_options["stock_availability_values"] = list(dict.fromkeys(
            str(value).strip()
            for value in (availability_values or [])
            if str(value).strip()
        ))
        if availability_mode == "value_actions":
            request_options["stock_value_actions"] = {
                str(value).strip(): str(action)
                for value, action in (availability_actions or {}).items()
                if (
                    str(value).strip()
                    and action in {"available", "active_zero", "unavailable"}
                )
            }
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            """
            UPDATE suppliers SET available_stock_quantity=?,
                shopify_location_id=?,request_options_json=?,updated_at=?
            WHERE slug=?
            """,
            (
                quantity,
                (
                    str(inventory_location_id).strip()
                    if inventory_location_id is not None
                    else supplier.get("shopify_location_id")
                ),
                json.dumps(request_options, ensure_ascii=False),
                utc_now(), slug,
            ),
        )
    supplier["request_options"] = request_options
    if not apply_existing:
        return {"updated": 0, "available_quantity": quantity}
    field_map = {**DEFAULT_MAPPING, **(supplier.get("field_mapping") or {})}
    stock_field = field_map["stock"]
    updated = 0
    with _connect(init_supplier_database(slug)) as conn:
        inventory_policy = (
            "continue"
            if request_options.get("continue_selling_when_out_of_stock")
            else "deny"
        )
        rows = conn.execute(
            """
            SELECT sku,raw_data_json,price,sale_price
            FROM products WHERE source_present=1
            """
        ).fetchall()
        for row in rows:
            try:
                raw = json.loads(row["raw_data_json"] or "{}")
            except json.JSONDecodeError:
                raw = {}
            selling_price = (
                row["sale_price"]
                if row["sale_price"] is not None
                else row["price"]
            )
            if stock_field:
                is_available = _stock_is_available(
                    raw.get(stock_field), supplier
                )
            else:
                is_available = (
                    selling_price is not None and float(selling_price) > 0
                )
            is_publishable = (
                (
                    is_available
                    or inventory_policy == "continue"
                    or _stock_stays_active_at_zero(
                        raw.get(stock_field), supplier
                    )
                )
                and selling_price is not None
                and float(selling_price) > 0
            )
            conn.execute(
                """
                UPDATE products SET stock_quantity=?,available=?,shopify_status=?,
                    inventory_policy=?,updated_at=? WHERE sku=?
                """,
                (
                    quantity if is_available else 0,
                    int(is_available),
                    "active" if is_publishable else "draft",
                    inventory_policy,
                    utc_now(),
                    row["sku"],
                ),
            )
            updated += 1
    return {"updated": updated, "available_quantity": quantity}


def save_missing_product_policy(
    slug: str,
    *,
    draft_missing: bool,
    delete_missing: bool,
    delete_after_months: int,
) -> None:
    months = int(delete_after_months)
    if months < 1 or months > 120:
        raise ValueError("De bewaartermijn moet tussen 1 en 120 maanden liggen.")
    init_registry()
    with _connect(REGISTRY_PATH) as conn:
        cursor = conn.execute(
            """
            UPDATE suppliers SET
                missing_products_to_draft=?,
                delete_missing_products=?,
                delete_missing_after_months=?,
                updated_at=?
            WHERE slug=?
            """,
            (int(draft_missing), int(delete_missing), months, utc_now(), slug),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Onbekende leverancier: {slug}")


@dataclass
class SourceAnalysis:
    format: str
    row_count: int
    fields: list[str]
    sample: list[dict[str, Any]]
    raw_bytes: bytes
    records: list[dict[str, Any]]


def _xml_element_value(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return (element.text or "").strip()
    grouped: dict[str, list[Any]] = {}
    for child in children:
        grouped.setdefault(child.tag, []).append(
            _xml_element_value(child)
        )
    result: dict[str, Any] = {
        key: values[0] if len(values) == 1 else values
        for key, values in grouped.items()
    }
    for key, value in element.attrib.items():
        result[f"@{key}"] = value
    text = (element.text or "").strip()
    if text:
        result["_text"] = text
    return result


def _request_bytes(supplier: dict[str, Any]) -> bytes:
    location = supplier["source_location"]
    auth_type = supplier.get("auth_type", "none")
    if auth_type == "wordpress_post_password":
        secret = str(supplier.get("secret") or "")
        if not secret:
            raise ValueError(
                "Het WordPress-documentwachtwoord ontbreekt. Sla het eerst "
                "versleuteld op bij de broninstellingen."
            )
        page_url = urllib.parse.urlsplit(location)
        if page_url.scheme != "https" or not page_url.netloc:
            raise ValueError(
                "Een beveiligde WordPress-documentbron moet een HTTPS-URL zijn."
            )
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        headers = {"User-Agent": "Weldingshop-PIM/1.0"}
        post_url = urllib.parse.urljoin(
            location, "/wp-login.php?action=postpass"
        )
        payload = urllib.parse.urlencode({
            "post_password": secret,
            "redirect_to": location,
            "Submit": "Submit",
        }).encode()
        opener.open(
            urllib.request.Request(post_url, data=payload, headers=headers),
            timeout=60,
        ).read()
        with opener.open(
            urllib.request.Request(location, headers=headers), timeout=60
        ) as response:
            protected_html = response.read()
        soup = BeautifulSoup(protected_html, "html.parser")
        wanted_extensions = (
            (".pdf",) if "pdf" in str(supplier.get("source_type") or "")
            else (".xlsx", ".xls")
        )
        links = []
        for anchor in soup.find_all("a", href=True):
            target = urllib.parse.urljoin(location, str(anchor["href"]).strip())
            parsed = urllib.parse.urlsplit(target)
            extension_path = parsed.path.casefold()
            if (
                parsed.scheme == "https"
                and parsed.netloc.casefold() == page_url.netloc.casefold()
                and extension_path.endswith(wanted_extensions)
            ):
                links.append(target)
        if not links:
            if soup.select_one("form.post-password-form"):
                raise ValueError(
                    "Het WordPress-documentwachtwoord is niet geaccepteerd."
                )
            raise ValueError(
                "Na toegang is op de documentpagina geen passend downloadbestand gevonden."
            )
        with opener.open(
            urllib.request.Request(links[0], headers=headers), timeout=60
        ) as response:
            content = response.read()
        if wanted_extensions == (".pdf",):
            if not content.startswith(b"%PDF"):
                raise ValueError("De beveiligde download is geen geldig PDF-bestand.")
        elif not (
            content.startswith(b"PK\x03\x04")
            or content.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
        ):
            raise ValueError("De beveiligde download is geen geldig Excel-bestand.")
        return content
    request = urllib.request.Request(location, headers={"User-Agent": "Weldingshop-PIM/1.0"})
    username = supplier.get("username", "")
    secret = supplier.get("secret", "")
    if auth_type == "basic":
        import base64
        token = base64.b64encode(f"{username}:{secret}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    elif auth_type == "bearer":
        request.add_header("Authorization", f"Bearer {secret}")
    elif auth_type == "api_key_header":
        header = supplier.get("request_options", {}).get("api_key_header", "X-API-Key")
        request.add_header(header, secret)
    timeout = int(supplier.get("request_options", {}).get("timeout", 60))
    source_type = str(supplier.get("source_type") or "").casefold()
    is_xml_source = "xml" in source_type or location.casefold().endswith(".xml")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read()
            if not content.strip():
                raise ValueError("de leveranciersbron gaf een lege respons")
            if is_xml_source:
                # Validate the complete document here so truncated responses and
                # HTML error pages are retried before read_source starts parsing.
                root = ET.fromstring(content)
                root_name = str(root.tag).rsplit("}", 1)[-1].casefold()
                if root_name in {"html", "head", "body"}:
                    raise ValueError(
                        "de XML-leveranciersbron gaf een HTML-respons"
                    )
            return content
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt)
    raise ValueError(
        "Leveranciersbron kon na 3 pogingen niet geldig worden opgehaald: "
        f"{last_error}"
    ) from last_error


def read_source(
    supplier: dict[str, Any],
    uploaded_bytes: bytes | None = None,
    uploaded_name: str = "",
) -> SourceAnalysis:
    raw = uploaded_bytes if uploaded_bytes is not None else _request_bytes(supplier)
    source_type = supplier.get("source_type", "")
    name = uploaded_name.lower() or supplier.get("source_location", "").lower()

    if "pdf" in source_type or name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        page_texts = [page.extract_text() or "" for page in reader.pages]
        full_text = "\n".join(page_texts)
        rate_match = re.search(
            r"ACTUAL\s+RATE\s+EUR/PLN\s*:\s*(\d+[.,]\d+)",
            full_text,
            re.IGNORECASE,
        )
        if not rate_match:
            raise ValueError(
                "De Tecweld-PDF bevat geen EUR/PLN-koers; import is gestopt "
                "om te voorkomen dat PLN-prijzen als europrijzen worden opgeslagen."
            )
        eur_pln_rate = float(rate_match.group(1).replace(",", "."))
        if eur_pln_rate <= 0:
            raise ValueError("De EUR/PLN-koers in de Tecweld-PDF is ongeldig.")
        valid_from_match = re.search(
            r"VALID\s+FROM\s*:\s*(\d{2}[.]\d{2}[.]\d{4})",
            full_text,
            re.IGNORECASE,
        )
        price_list_valid_from = (
            valid_from_match.group(1) if valid_from_match else ""
        )
        records = []
        category = ""
        category_pattern = re.compile(r"^\s*\d+(?:\.\d+)+\s+(.+?)\s*$")
        product_pattern = re.compile(
            r"^\s*(?P<sku>\S+)\s+(?P<title>.+?)\s+"
            r"(?P<price>\d[\d ]*,\d{2})\s*zł/(?P<unit>\S+)\s*$",
            re.IGNORECASE,
        )
        for page_text in page_texts:
            for line in page_text.splitlines():
                category_match = category_pattern.match(line)
                if category_match and "zł/" not in line.casefold():
                    category = category_match.group(1).strip()
                    continue
                match = product_pattern.match(line)
                if not match:
                    continue
                price_pln = float(
                    match.group("price").replace(" ", "").replace(",", ".")
                )
                records.append({
                    "Part No": match.group("sku").strip(),
                    "Item": match.group("title").strip(),
                    "Price PLN": price_pln,
                    "Price EUR": round(price_pln / eur_pln_rate, 2),
                    "EUR/PLN Rate": eur_pln_rate,
                    "Source Currency": "PLN",
                    "Price List Valid From": price_list_valid_from,
                    "Unit": match.group("unit").strip(),
                    "Category": category,
                })
        if not records:
            raise ValueError(
                "De PDF is geopend, maar er zijn geen Tecweld-prijsregels herkend."
            )
        fmt = "PDF"
    elif "xml" in source_type or name.endswith(".xml"):
        root = ET.fromstring(raw)
        nodes = root.findall(".//product")
        if not nodes:
            candidates = [node for node in root.iter() if node is not root]
            counts: dict[str, int] = {}
            for node in candidates:
                counts[node.tag] = counts.get(node.tag, 0) + 1
            repeated = max(counts, key=counts.get) if counts else ""
            nodes = root.findall(f".//{repeated}") if repeated else []
        records = []
        for node in nodes:
            record = dict(node.attrib)
            for child in node:
                value = _xml_element_value(child)
                if child.tag not in record:
                    record[child.tag] = value
                elif isinstance(record[child.tag], list):
                    record[child.tag].append(value)
                else:
                    record[child.tag] = [record[child.tag], value]
            records.append(record)
        fmt = "XML"
    elif "json" in source_type or name.endswith(".json"):
        payload = json.loads(raw.decode("utf-8-sig"))
        if isinstance(payload, dict):
            for key in ("products", "items", "data", "results"):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
        records = [dict(row) for row in payload] if isinstance(payload, list) else [dict(payload)]
        fmt = "JSON"
    elif "excel" in source_type or name.endswith((".xlsx", ".xls")):
        excel_sheet = (
            supplier.get("request_options", {}).get("excel_sheet")
            or 0
        )
        try:
            excel_header_row = max(
                1,
                int(
                    supplier.get("request_options", {}).get(
                        "excel_header_row"
                    ) or 1
                ),
            )
        except (TypeError, ValueError):
            excel_header_row = 1
        frame = pd.read_excel(
            io.BytesIO(raw),
            sheet_name=excel_sheet,
            header=excel_header_row - 1,
        )
        records = frame.where(pd.notna(frame), None).to_dict("records")
        fmt = "Excel"
    else:
        frame = pd.read_csv(io.BytesIO(raw), sep=None, engine="python", encoding="utf-8-sig")
        records = frame.where(pd.notna(frame), None).to_dict("records")
        fmt = "CSV"

    fields = sorted({str(key) for record in records for key in record.keys()})
    return SourceAnalysis(
        format=fmt,
        row_count=len(records),
        fields=fields,
        sample=records[:10],
        raw_bytes=raw,
        records=records,
    )


DEFAULT_MAPPING = {
    "sku": "reference",
    "ean": "ean",
    "title": "name",
    "description": "description",
    "price": "price",
    "sale_price": "sale_price",
    "cost_price": "",
    "weight": "weight",
    "weight_kg": "",
    "primary_image": "",
    "stock": "stock",
    "product_type": "articleGroup",
    "category": "category",
    "category_full": "category_full",
    "updated_at": "date_upd",
    "product_group_name": "",
    "execution": "",
    "filter": "",
    "purchase_unit": "",
    "sales_unit": "",
    "purchase_units_per_sales_unit": "",
    "unit_calculation_mode": "",
    "gross_purchase_price_per_kg": "",
    "purchase_discount_percent": "",
    "net_purchase_price_per_kg": "",
    "kg_per_purchase_unit": "",
    "kg_per_sales_unit": "",
}


_VALKENPOWER_CATEGORY_STOPWORDS = {
    "de", "het", "een", "en", "van", "voor", "met", "op", "in", "incl",
    "inclusief", "professioneel", "professional", "stuks", "st", "mm", "cm",
    "meter", "liter", "kg", "ton", "delig", "dlg", "valkenpower", "bullram",
    "mammuth", "fluxon", "airpro", "fabbri", "torso", "woodcraft", "hugong",
}


def _valkenpower_title_tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z]+", _text(value).casefold())
        if len(token) >= 3 and token not in _VALKENPOWER_CATEGORY_STOPWORDS
    }


def _valkenpower_sku_family(value: str) -> str:
    bare = _text(value).upper().removeprefix("VP-")
    match = re.match(r"[A-Z]+", bare)
    return (match.group(0) if match else "")[:4]


def _valkenpower_row_path(row: sqlite3.Row) -> tuple[str, str, str, str, str, str]:
    try:
        filters = json.loads(row["filter_values_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        filters = []
    return (
        _text(row["product_group_name"]),
        _text(filters[0]) if filters else "",
        _text(row["execution"]),
        _text(row["subcategory_3"]),
        _text(row["subcategory_4"]),
        _text(row["subcategory_5"]),
    )


def _infer_valkenpower_category_path(
    conn: sqlite3.Connection, sku: str, title: str,
) -> tuple[str, str, str, str, str, str] | None:
    """Infer a new product's hierarchy from established Valkenpower families."""
    candidates = conn.execute(
        """
        SELECT sku,source_title,product_group_name,filter_values_json,execution,
               subcategory_3,subcategory_4,subcategory_5
        FROM products
        WHERE source_present=1
          AND TRIM(COALESCE(product_group_name,''))<>''
        """
    ).fetchall()
    if not candidates:
        return None

    family = _valkenpower_sku_family(sku)
    family_rows = [
        row for row in candidates
        if len(family) >= 2 and _valkenpower_sku_family(row["sku"]) == family
    ]
    if len(family_rows) >= 2:
        counts: dict[tuple[str, str, str, str, str, str], int] = {}
        for row in family_rows:
            category_path = _valkenpower_row_path(row)
            counts[category_path] = counts.get(category_path, 0) + 1
        best_path, support = max(counts.items(), key=lambda item: item[1])
        if support / len(family_rows) >= 0.60:
            return best_path

    wanted_tokens = _valkenpower_title_tokens(title)
    if len(wanted_tokens) < 2:
        return None
    scored = []
    for row in candidates:
        candidate_tokens = _valkenpower_title_tokens(row["source_title"])
        overlap = len(wanted_tokens & candidate_tokens)
        if overlap < 2:
            continue
        union = len(wanted_tokens | candidate_tokens) or 1
        jaccard = overlap / union
        sequence = SequenceMatcher(
            None, _text(title).casefold(), _text(row["source_title"]).casefold()
        ).ratio()
        scored.append((0.70 * jaccard + 0.30 * sequence, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] < 0.58:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.06:
        top_path = _valkenpower_row_path(scored[0][1])
        if _valkenpower_row_path(scored[1][1]) != top_path:
            return None
    return _valkenpower_row_path(scored[0][1])

SOURCE_FIELD_ALIASES = {
    "sku": {
        "sku", "article", "articleno", "articlenumber", "articlecode",
        "artikel", "artikelno", "artikelnummer", "artikelcode",
        "itemno", "itemnumber", "reference", "productcode", "model",
    },
    "ean": {
        "ean", "eancode", "barcode", "barcodenumber", "gtin", "gtin13",
        "ean13",
    },
    "title": {
        "title", "name", "productname", "producttitle", "articleName",
        "articlename", "omschrijving", "description", "designation",
        "titlenl",
    },
    "description": {
        "description", "productdescription", "longdescription",
        "descriptionlong", "langeomschrijving", "webdescription",
        "descriptionnl",
    },
    "price": {
        "price", "listprice", "grossprice", "brutoprijs", "catalogprice",
        "adviesprijs", "prijs", "priceexvat",
    },
    "sale_price": {
        "saleprice", "sellingprice", "retailprice", "consumerprice",
        "verkoopprijs", "netprice", "priceinvat",
    },
    "cost_price": {
        "costprice", "purchaseprice", "netpurchaseprice",
        "inkoopprijs", "specialpriceexvat",
    },
    "weight": {
        "weight", "weightkg", "weightgrams", "gewicht", "gewichtkg",
    },
    "weight_kg": {
        "packweight", "shippingweightkg", "verzendgewichtkg",
    },
    "primary_image": {
        "primaryimage", "mainimage", "mainimageurl", "hoofdafbeelding",
    },
    "stock": {
        "stock", "stockquantity", "quantity", "voorraad",
        "beschikbaar", "available",
    },
    "product_type": {
        "producttype", "articlegroup", "productgroup", "artikelgroep",
    },
    "category": {"category", "categorie", "productcategory"},
    "category_full": {
        "categoryfull", "fullcategory", "categorypath", "categoriepad",
    },
    "updated_at": {
        "updatedat", "lastupdated", "modifiedat", "dateupd",
        "wijzigingsdatum",
    },
    "purchase_unit": {
        "purchaseunit", "buyingunit", "priceunit", "inkoopenheid",
        "inkoopeenheid", "prijseenheid", "factuureenheid", "pu",
    },
    "sales_unit": {
        "salesunit", "sellingunit", "verkoopeenheid", "shopifyunit",
    },
    "purchase_units_per_sales_unit": {
        "purchaseunitspersalesunit", "unitsperpackage", "packquantity",
        "packagequantity", "conversionfactor", "omrekenfactor",
        "inhoud", "aantalperverkoopeenheid",
    },
    "unit_calculation_mode": {
        "unitcalculationmode", "calculationmode", "rekenwijze",
        "omrekenwijze",
    },
    "gross_purchase_price_per_kg": {
        "grosspriceperkg", "grosspurchasepriceperkg",
        "brutoinkoopprijsperkg", "brutoprijsperkg",
    },
    "purchase_discount_percent": {
        "discount", "discountpercent", "purchase discount",
        "inkoopkorting", "korting",
    },
    "net_purchase_price_per_kg": {
        "netpriceperkg", "netpurchasepriceperkg",
        "nettoinkoopprijsperkg", "nettoprijsperkg",
    },
    "kg_per_purchase_unit": {
        "kgceweldunit", "kgperpurchaseunit", "kgperunit",
        "kgperinkoopeenheid",
    },
    "kg_per_sales_unit": {
        "kgceweldbundle", "kgpersalesunit", "kgperbundle",
        "kgperverkoopeenheid", "kgperbundel",
    },
    "product_group_name": {
        "productgroupname", "productgroepnaam", "productgroep",
        "descriptionproductgroup",
    },
    "execution": {"execution", "uitvoering", "variant"},
    "filter": {
        "filter", "filters", "filtervalue", "filterwaarde", "attributes",
    },
}


def _normalized_field_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def suggest_source_field_mapping(fields: Iterable[Any]) -> dict[str, str]:
    """Geef alleen eenduidige, exacte aliasvoorstellen voor bronkolommen."""
    by_normalized: dict[str, list[str]] = {}
    for field in fields:
        text = str(field)
        normalized = _normalized_field_name(text)
        if normalized:
            by_normalized.setdefault(normalized, []).append(text)
    suggestions: dict[str, str] = {}
    claimed: set[str] = set()
    preferred_aliases = {
        "sku": ["sku", "model", "reference", "articleno"],
        "title": [
            "title", "titlenl", "producttitle", "productname", "name", "articlename",
            "omschrijving", "description", "designation",
        ],
        "description": [
            "descriptionnl", "longdescription", "productdescription", "descriptionlong",
            "langeomschrijving", "webdescription", "description",
        ],
        "price": ["priceexvat", "price", "listprice", "grossprice"],
        "sale_price": ["priceinvat", "saleprice", "sellingprice"],
        "cost_price": ["specialpriceexvat", "costprice", "purchaseprice"],
        "weight_kg": ["packweight", "shippingweightkg"],
    }
    for target, aliases in SOURCE_FIELD_ALIASES.items():
        ordered_aliases = preferred_aliases.get(target, sorted(aliases))
        for alias in ordered_aliases:
            matches = [
                field for field in by_normalized.get(
                    _normalized_field_name(alias), []
                )
                if field not in claimed
            ]
            if len(matches) == 1:
                suggestions[target] = matches[0]
                claimed.add(matches[0])
                break
    # Sommige prijslijsten hebben één korte kolom "Product description" die
    # zowel de zichtbare productnaam als de bronomschrijving vormt.
    if "title" not in suggestions and "description" in suggestions:
        description_field = suggestions["description"]
        if _normalized_field_name(description_field) == "productdescription":
            suggestions["title"] = description_field
    return suggestions


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _certilas_product_group(description: str, current_group: str = "") -> str:
    """Fill trusted process gaps from unambiguous Certilas packaging patterns."""
    current = _text(current_group)
    if current.casefold() not in {"", "nan", "none", "null"}:
        return current
    text = re.sub(r"\s+", " ", _text(description)).strip()
    if re.search(r"\bx\s*1000\s*mm\b", text, re.IGNORECASE):
        return "GTAW solid wire/Other"
    if re.search(r"\bD-300\b", text, re.IGNORECASE):
        return "GMAW solid wire/Other"
    return ""


CERTILAS_CONFIRMED_PACKAGE_WEIGHTS = {
    # CroNiMo HLS 3,2 x 350 mm; confirmed by the product owner.
    "8720663416452": 2.5,
    # CroNiMo HLS 4,0 x 450 mm; confirmed from barcode by the product owner.
    "8720663416469": 2.5,
}


def _certilas_confirmed_package_weight(ean: str) -> float | None:
    return CERTILAS_CONFIRMED_PACKAGE_WEIGHTS.get(_ean(ean))


def _sku_with_prefix(supplier_sku: str, prefix: str) -> str:
    supplier_sku = _text(supplier_sku).upper()
    prefix = _text(prefix).upper()
    if not supplier_sku or not prefix or supplier_sku.startswith(prefix):
        return supplier_sku
    return f"{prefix}{supplier_sku}"


def _float(value: Any) -> float | None:
    try:
        number = float(str(value).strip().removesuffix("%").replace(",", "."))
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _package_weight(value: Any) -> float | None:
    """Lees ook leveranciersnotaties zoals 21 x 0,8 en 3,2(4x0,8)."""
    text = str(value or "").strip().casefold().replace(",", ".")
    leading = re.match(r"^\s*(\d+(?:\.\d+)?)\s*\(", text)
    if leading:
        return float(leading.group(1))
    multiplication = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*", text
    )
    if multiplication:
        return float(multiplication.group(1)) * float(multiplication.group(2))
    return _float(value)


def _integer(value: Any) -> int | None:
    number = _float(value)
    return int(number) if number is not None else None


def _ean(value: Any) -> str:
    text = _text(value)
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text if re.fullmatch(r"\d{8,14}", text) else ""


PRODUCT_NAME_GROUPS = [
    ("Ademhalingsbescherming", r"(?:stofmasker|gelaatsmasker|ffp[123]|filter klasse)"),
    ("Gehoorbescherming", r"(?:oordop|gehoorkap|\bsnr\b)"),
    ("Oogbescherming", r"(?:veiligheidsbril|goggle|lens clear|lens smoke)"),
    ("Veiligheidshelmen", r"(?:veiligheidshelm|\bhelm\b)"),
    ("Veiligheidsharnassen", r"(?:harnas|positioneringsgordel)"),
    ("Valstopapparaten", r"(?:valstop|valblok|\bsrl\b)"),
    ("Vanglijnen", r"(?:vanglijn|veiligheidslijn|leeflijn|kernmantellijn)"),
    ("Ankerpunten", r"(?:ankerpunt|ankerpaal|ankerstatief)"),
    ("Verbindingsmiddelen", r"(?:karabiner|steigerhaak|veiligheidshaak)"),
    ("Valbeveiligingssets", r"(?:valbeveiligingsset|dakset)"),
    ("Gereedschapswagens", r"\b(?:gereedschapswagen|toolwagen|topkist)\w*"),
    ("Momentsleutels", r"\bmomentsleutel\w*"),
    ("Schroevendraaiers", r"\bschroevendraaier\w*"),
    ("Meetgereedschap", r"\b(?:schuifmaat|multimeter|meetgereedschap)\w*"),
    ("Accugereedschap", r"\b(?:accuboormachine|accuslagmoersleutel|accumachine)\w*"),
    ("Boren", r"(?:boor|boren)\w*"),
    ("Bits", r"bit\w*"),
    ("Doppen", r"(?:dop|doppen)\w*"),
    ("Sleutels", r"sleutel\w*"),
    ("Ratels", r"ratel\w*"),
    ("Tangen", r"tang\w*"),
    ("Hamers", r"hamer\w*"),
    ("Schrapers", r"schraper\w*"),
    ("Zagen", r"zaag\w*"),
    ("Lampen", r"lamp\w*"),
    ("Krikken", r"krik\w*"),
]


def _normalized_product_group(
    title: str, category: str, category_full: str
) -> str:
    searchable = title.casefold()
    for group, pattern in PRODUCT_NAME_GROUPS:
        if re.search(pattern, searchable):
            return group
    group = category.strip()
    category_searchable = f"{category} {category_full}".casefold()
    for detected_group, pattern in PRODUCT_NAME_GROUPS:
        if re.search(pattern, category_searchable):
            return detected_group
    if group.casefold() in {
        "sets", "accessoires", "onderdelen", "enkele producten"
    }:
        parts = [part.strip() for part in category_full.split(">") if part.strip()]
        if parts:
            group = parts[0]
    if not group and category_full:
        parts = [part.strip() for part in category_full.split(">") if part.strip()]
        group = parts[-1] if parts else ""
    replacements = {
        "Losse krachtdoppen & Acc.": "Losse krachtdoppen & Accessoires",
    }
    return replacements.get(group, group)


def _split_filter_values(value: Any) -> list[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = re.split(r"[\n;|]+", _text(value))
    return list(dict.fromkeys(
        str(part).strip() for part in parts if str(part).strip()
    ))


def _derived_filter_values(title: str, description: str) -> list[str]:
    text = re.sub(r"<[^>]+>", " ", f"{title} {description}").lower()
    values: list[str] = []
    is_drill = bool(re.search(r"\b(?:boren|boor)\b", text))
    patterns = [
        ("Aansluiting", r"(?<![\d/])(?:1/4|3/8|1/2|3/4|1)(?:\s*)[\"”]"),
        (
            "Diameter" if is_drill else "Maat",
            r"(?<![\d.,])\d+(?:[.,]\d+)?\s*mm\b",
        ),
        ("Spanning", r"(?<!\d)\d+(?:[.,]\d+)?\s*v\b"),
    ]
    for label, pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            clean = re.sub(r"\s+", " ", match.strip()).replace("”", '"')
            value = f"{label}: {clean}"
            if value not in values:
                values.append(value)
    profiles = [
        ("6-kant", r"\b6[\s-]*kant\b"),
        ("12-kant", r"\b12[\s-]*kant\b"),
        ("E-Torx", r"\be[\s-]*torx\b"),
        ("Torx", r"(?<!e-)\btorx\b"),
        ("Inbus", r"\binbus\b"),
        ("Phillips", r"\b(?:phillips|ph\d)\b"),
        ("Pozidriv", r"\b(?:pozidriv|pz\d)\b"),
    ]
    for label, pattern in profiles:
        if re.search(pattern, text):
            values.append(f"Profiel: {label}")
    for norm in re.findall(r"\bEN\s*\d{3,5}(?::\d{4})?\b", text, re.IGNORECASE):
        values.append("Norm: " + re.sub(r"\s+", " ", norm.upper()))
    for weight in re.findall(
        r"(?:maximaal|maximum|up to)\s*(\d{2,3})\s*kg\b",
        text,
        re.IGNORECASE,
    ):
        values.append(f"Max. gebruikersgewicht: {weight} kg")
    protection_class = re.search(r"\bFFP[123](?:V)?(?:\s+NR\s+D)?\b", text, re.IGNORECASE)
    if protection_class:
        values.append(f"Beschermingsklasse: {protection_class.group(0).upper()}")
    snr = re.search(r"\bSNR\s*(\d{2})\s*dB\b", text, re.IGNORECASE)
    if snr:
        values.append(f"Demping: SNR {snr.group(1)} dB")
    return list(dict.fromkeys(values))


CERTILAS_PROCESS_FILTERS = {
    "GMAW": "MIG",
    "GTAW": "TIG",
    "SMAW": "Electrode lassen",
    "FCAW": "Gevulde draad",
    "SAW": "Poederdek",
    "ESAW": "Elektroslaklassen",
    "Brazing": "Solderen",
    "Metal spray wires": "Thermisch spuiten",
    "Metal Powders": "Poederlassen",
    "Oxi-Acetylene welding": "Autogeen lassen",
    "Specials": "Overig",
}

CERTILAS_ALLOWED_PROCESSES = frozenset({
    *CERTILAS_PROCESS_FILTERS.values(),
    "Autogeen hardoplassen",
    "Poederlassen",
    "Solderen",
    "Gevulde draad",
    "Poederdek",
    "MIG",
    "TIG",
    "Electrode lassen",
    "Toeslagcomponent",
    "Overig",
})

CERTILAS_MATERIAL_FILTERS = {
    "Carbon steel": "Staal",
    "Stainless steel": "RVS",
    "Nickel base": "Nikkel",
    "Aluminium": "Aluminium",
    "Copper base": "Koper",
    "Hardfacing": "Slijtvaste legering",
    "Titanium base": "Titanium",
    "Cast iron": "Gietijzer",
    "Underwater": "Staal",
    "Other": "Overig",
}

CERTILAS_ALLOWED_MATERIALS = frozenset({
    *CERTILAS_MATERIAL_FILTERS.values(),
    "Gietijzer",
    "Magnesium",
    "Nikkel",
    "Koper",
    "RVS",
    "Slijtvaste legering",
    "Staal",
    "Titanium",
    "Zirkonium",
    "Overig",
})


def _certilas_process_filter(product_group: str, description: str) -> str:
    group = _text(product_group)
    for prefix, label in CERTILAS_PROCESS_FILTERS.items():
        if group.casefold().startswith(prefix.casefold()):
            return label
    text = _text(description)
    if re.match(r"^Surcharge\b", text, re.IGNORECASE):
        return "Toeslagcomponent"
    if re.match(r"^Powder\b", text, re.IGNORECASE):
        return "Poederlassen"
    if re.match(r"^DUR\s+(?:R|CS|WC)\b", text, re.IGNORECASE):
        return "Autogeen hardoplassen"
    if re.match(r"^(?:L-|CuZn|CuNi\d*Zn|F-SW|.*Flux)", text, re.IGNORECASE):
        return "Solderen"
    if re.match(r"^AA\b", text, re.IGNORECASE):
        return "Gevulde draad"
    if re.match(r"^Ultra Clean S\d|^S\d(?:\s|$)", text, re.IGNORECASE):
        return "Poederdek"
    if re.match(r"^SG\b", text, re.IGNORECASE):
        return "MIG"
    if re.search(r"\bTig\b", text, re.IGNORECASE):
        return "TIG"
    if re.search(r"\d+(?:[,.]\d+)?\s*x\s*\d+\s*mm\b", text, re.IGNORECASE):
        return "Electrode lassen"
    if group.casefold() == "productlist generated":
        return "Overig"
    return "Overig"


def _certilas_material_filter(product_group: str, description: str) -> str:
    group = _text(product_group)
    text = _text(description)
    # De prijslijstgroep is soms te algemeen of aantoonbaar onjuist (CuSi3
    # staat bijvoorbeeld onder Carbon steel). Eenduidige legeringscodes uit
    # de artikelomschrijving zijn dan de betrouwbaardere bron.
    if re.search(r"\b(?:AlMg|AlSi|Al\s*99|AlZn|Aluminium)", text, re.IGNORECASE):
        return "Aluminium"
    if re.search(
        r"\b(?:NiCr|NiCro|NiAl|NiCu|NiMo|NiTi|NiBSi|Ni-?Al|Nickel|"
        r"Alloy\s*[C82]|625)", text, re.IGNORECASE,
    ):
        return "Nikkel"
    if re.search(
        r"(?:^|\s)(?:(?:SP|AA)\s*-?\s*)?(?:Cu(?:[A-Za-z0-9]|\b)|L-Cu|L-Ag|CuZn|CuNi|CuSn|CuP|"
        r"brass|bronze)", text, re.IGNORECASE,
    ):
        return "Koper"
    if re.search(r"\b(?:FeNi|NiFe|E\s+Ni\b)", text, re.IGNORECASE):
        return "Gietijzer"
    if re.search(r"\b(?:Zirconium|Zr\s*702)\b", text, re.IGNORECASE):
        return "Zirkonium"
    if re.search(r"\bMagnesium\b", text, re.IGNORECASE):
        return "Magnesium"
    if re.search(r"\bERTi[- ]?\d", text, re.IGNORECASE):
        return "Titanium"
    if "/" in group:
        material = group.split("/", 1)[1].strip()
        for source, label in CERTILAS_MATERIAL_FILTERS.items():
            if material.casefold() == source.casefold():
                return label
    if re.search(r"(?:^|\s)(?:308|309|310|312|316|317|318|347|430|904L|2209|2594)(?:\D|$)|\b1\.4(?:3|4|5)\d{2}\b", text, re.IGNORECASE):
        return "RVS"
    if re.search(r"\b(?:AlMg|AlSi|Al\s*99|AlZn|Aluminium)", text, re.IGNORECASE):
        return "Aluminium"
    if re.search(
        r"\b(?:NiCr|NiCro|NiAl|NiCu|NiMo|NiTi|NiBSi|Ni-?Al|Nickel|"
        r"Alloy\s*[C82]|625)", text, re.IGNORECASE,
    ):
        return "Nikkel"
    if re.search(
        r"(?:^|\s)(?:(?:SP|AA)\s*-?\s*)?(?:Cu(?:[A-Za-z0-9]|\b)|L-Cu|L-Ag|CuZn|CuNi|CuSn|CuP|"
        r"brass|bronze)", text, re.IGNORECASE,
    ):
        return "Koper"
    if re.search(
        r"\b(?:DUR|WC|WCo|Hardfacing|SPA?\s*(?:55|725))\b",
        text, re.IGNORECASE,
    ):
        return "Slijtvaste legering"
    if re.search(r"\b(?:FeNi|NiFe|E\s+Ni\b)", text, re.IGNORECASE):
        return "Gietijzer"
    if re.search(r"\b(?:Zirconium|Zr\s*702)\b", text, re.IGNORECASE):
        return "Zirkonium"
    if re.search(r"\bMagnesium\b", text, re.IGNORECASE):
        return "Magnesium"
    if re.search(r"\bERTi[- ]?\d", text, re.IGNORECASE):
        return "Titanium"
    if re.search(
        r"\b(?:1\.4122|1\.4115|420-[BC]|13\s*Cr)\b",
        text, re.IGNORECASE,
    ):
        return "RVS"
    if re.search(
        r"\b(?:1\.3505|1\.0616|SP10Mn|SP\s*(?:33|37))\b",
        text, re.IGNORECASE,
    ):
        return "Staal"
    if re.search(r"\b(?:Sn\d|Pb\d|Zn\s*99|BABBITS)", text, re.IGNORECASE):
        return "Overig"
    if re.search(r"\b(?:WCo|CoCr|8812-Co|WC2Co|WSC)", text, re.IGNORECASE):
        return "Slijtvaste legering"
    if re.search(
        r"(?:\bFlux|\bF-SW\b|\bbacking strip\b|\bCarbon Rod\b|"
        r"\bGuts\b|\bWear plate\b)", text, re.IGNORECASE,
    ):
        return "Overig"
    if re.search(r"\b(?:SG\s|Ultra Clean|ER\s*\d|G[1-4]\b|S\d\b)", text, re.IGNORECASE):
        return "Staal"
    if re.match(r"^Surcharge\b", text, re.IGNORECASE):
        return "Overig"
    if _text(product_group).casefold() == "productlist generated":
        return "Overig"
    if re.search(r"\bSS\s*6356\b", text, re.IGNORECASE):
        return "Staal"
    return "Overig"


def _certilas_material_type(material: str, description: str) -> str:
    text = re.sub(r"\s+", " ", _text(description)).strip()
    text = re.sub(r"^Surcharge\s+", "", text, flags=re.IGNORECASE)
    if material == "RVS":
        grade = re.search(
            r"(?<!\d)(2209|2594|307|308|309|310|312|316|317|318|347|"
            r"410|420|430|904)(?!\d)", text, re.IGNORECASE,
        )
        if grade:
            # Filter op de herkenbare hoofdkwaliteit (308/309/316 enz.).
            # De precieze L/H/Si/LMo-uitvoering blijft in de productnaam staan.
            return grade.group(1).upper()
    base = re.split(
        r"\s+\d+(?:[,.]\d+)?\s*(?:x\s*\d+(?:[,.]\d+)?\s*)?mm\b|"
        r"\s*\(-?\d+|\s+\d+(?:[,.]\d+)?\s*kg\b",
        text, maxsplit=1, flags=re.IGNORECASE,
    )[0].strip(" -_")
    base = re.sub(
        r"^(?:Powder\s+(?:PTA|HVOF|PS)?|SP(?:A)?|Surcharge)\s+",
        "", base, flags=re.IGNORECASE,
    ).strip()
    return base or material


def _certilas_filter_values(
    product_group: str, title: str, description: str,
) -> list[str]:
    process = _certilas_process_filter(product_group, description)
    material = _certilas_material_filter(product_group, description)
    if process not in CERTILAS_ALLOWED_PROCESSES:
        raise ValueError(f"Ongeldig Certilas-lasproces: {process}")
    if material not in CERTILAS_ALLOWED_MATERIALS:
        raise ValueError(f"Ongeldig Certilas-materiaal: {material}")
    return [
        f"Lasproces: {process}",
        f"Materiaal: {material}",
    ]


def _derived_execution(title: str, product_group: str = "") -> str:
    text = title.lower()
    values = []
    if product_group == "Veiligheidsharnassen":
        dimension = re.search(r"(?<!\d)([1-5])\s*d(?:\b|[- ])", text)
        if dimension:
            values.append(f"{dimension.group(1)}D-harnas")
    if product_group == "Vanglijnen":
        if "dubbel" in text:
            values.append("Dubbele uitvoering")
        elif "enkel" in text:
            values.append("Enkele uitvoering")
        if "elast" in text:
            values.append("Elastisch")
    is_set = bool(re.search(r"(?:set|sets)\b|\b\d+\s*[- ]?delig\b", text))
    type_names = {
        "Boren": ("Borenset", "Boor"),
        "Bits": ("Bitset", "Bit"),
        "Doppen": ("Doppenset", "Losse dop"),
        "Sleutels": ("Sleutelset", "Losse sleutel"),
        "Ratels": ("Ratelset", "Ratel"),
        "Tangen": ("Tangenset", "Tang"),
        "Hamers": ("Hamerset", "Hamer"),
        "Schrapers": ("Schraperset", "Schraper"),
        "Zagen": ("Zaagset", "Zaag"),
        "Lampen": ("Lampenset", "Lamp"),
    }
    if product_group in type_names:
        values.append(type_names[product_group][0 if is_set else 1])
    piece_match = re.search(r"\b(\d+)\s*[- ]?delig\b", text)
    if piece_match:
        values.append(f"{piece_match.group(1)}-delige set")
    for label, pattern in [
        ("Lang", r"\blang\b"),
        ("Kort", r"\bkort\b"),
        ("Diep", r"\bdiep\b"),
        ("Stubby", r"\bstubby\b"),
        ("VDE 1000V", r"\bvde\b"),
    ]:
        if re.search(pattern, text):
            values.append(label)
    colours = [
        colour.title() for colour in
        ("zwart", "rood", "blauw", "groen", "oranje", "grijs", "wit", "geel")
        if re.search(rf"\b{colour}\b", text)
    ]
    if colours:
        values.append("/".join(colours))
    return " · ".join(dict.fromkeys(values))


def refresh_product_filters(slug: str) -> dict[str, int]:
    from app.suppliers.routes import supplier_route

    route = supplier_route(slug)
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    field_map = {**DEFAULT_MAPPING, **(supplier.get("field_mapping") or {})}
    updated = 0
    with _connect(init_supplier_database(slug)) as conn:
        rows = conn.execute(
            """
            SELECT sku,source_title,source_description,category,category_full,
                raw_data_json FROM products
            """
        ).fetchall()
        for row in rows:
            try:
                raw = json.loads(row["raw_data_json"] or "{}")
            except json.JSONDecodeError:
                raw = {}
            group = (
                _text(raw.get(field_map["product_group_name"]))
                if field_map.get("product_group_name")
                else _normalized_product_group(
                    row["source_title"] or "",
                    row["category"] or "",
                    row["category_full"] or "",
                )
            )
            if route.uses_certilas_filters:
                group = _certilas_product_group(
                    row["source_description"] or "", group
                )
            filters = (
                _split_filter_values(raw.get(field_map["filter"]))
                if field_map.get("filter")
                else _derived_filter_values(
                    row["source_title"] or "", row["source_description"] or ""
                )
            )
            if route.uses_certilas_filters:
                filters = _certilas_filter_values(
                    group,
                    row["source_title"] or "",
                    row["source_description"] or "",
                )
            execution = (
                _text(raw.get(field_map["execution"]))
                if field_map.get("execution")
                else _derived_execution(row["source_title"] or "", group)
            )
            conn.execute(
                """
                UPDATE products SET product_group_name=?,filter_values_json=?,
                    execution=?,updated_at=? WHERE sku=?
                """,
                (
                    group, json.dumps(filters, ensure_ascii=False),
                    execution, utc_now(), row["sku"],
                ),
            )
            updated += 1
    return {"updated": updated}


def _image_urls(
    record: dict[str, Any],
    additional_keys: Iterable[str] | None = None,
) -> list[str]:
    candidates = []
    ordered_keys = [
        "primary_image", "mainimage", "main_image", "thumbnail", "Images",
        "multi",
    ] + [
        f"image_{index}" for index in range(50)
    ] + list(additional_keys or [])
    for key in ordered_keys:
        raw_value = record.get(key)
        values = (
            raw_value if isinstance(raw_value, (list, tuple))
            else [raw_value]
        )
        for item in values:
            value = _text(item)
            if not value:
                continue
            for part in re.split(r"[|,;\r\n]+\s*", value):
                if (
                    part.startswith(("http://", "https://"))
                    and _is_publishable_image_url(part)
                    and part not in candidates
                ):
                    candidates.append(part)
    return candidates


def _is_publishable_image_url(url: str) -> bool:
    lowered = url.casefold()
    rejected_markers = (
        "/img/tmp/",
        "product_mini",
        "/thumbnail/",
        "_thumbnail.",
        "-thumbnail.",
        "/thumb/",
        "_thumb.",
        "-thumb.",
    )
    return not any(marker in lowered for marker in rejected_markers)


def remove_rejected_supplier_images(slug: str) -> dict[str, int]:
    path = init_supplier_database(slug)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT id,image_url FROM product_images"
        ).fetchall()
        rejected_ids = [
            row["id"] for row in rows
            if not _is_publishable_image_url(row["image_url"])
        ]
        if rejected_ids:
            placeholders = ",".join("?" for _ in rejected_ids)
            conn.execute(
                f"DELETE FROM product_images WHERE id IN ({placeholders})",
                tuple(rejected_ids),
            )
    return {"checked": len(rows), "rejected": len(rejected_ids)}


def import_records(
    slug: str,
    analysis: SourceAnalysis,
    mapping: dict[str, str] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, int]:
    from app.suppliers.routes import supplier_route

    route = supplier_route(slug)
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    field_map = {**DEFAULT_MAPPING, **(supplier.get("field_mapping") or {}), **(mapping or {})}
    path = init_supplier_database(slug)
    now = utc_now()
    source_hash = hashlib.sha256(analysis.raw_bytes).hexdigest()
    archive_dir = IMPORT_DIR / slug
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{source_hash[:12]}.{analysis.format.lower()}"
    archive_path.write_bytes(analysis.raw_bytes)

    stats = {"seen": 0, "inserted": 0, "updated": 0, "unchanged": 0, "missing": 0}
    seen: set[str] = set()
    source_transformations = supplier.get("source_transformations") or {}
    with _connect(path) as conn:
        run_id = conn.execute(
            "INSERT INTO import_runs(started_at,status,source_hash) VALUES (?, 'running', ?)",
            (now, source_hash),
        ).lastrowid
        total_records = max(len(analysis.records), 1)
        sku_prefix = supplier.get("request_options", {}).get("sku_prefix") or ""
        for record_index, record in enumerate(analysis.records, 1):
            original_record = dict(record)
            record = apply_source_transformations(
                record, source_transformations
            )
            supplier_sku = _text(record.get(field_map["sku"])).upper()
            if not supplier_sku:
                continue
            sku = _sku_with_prefix(supplier_sku, sku_prefix)
            seen.add(sku)
            stats["seen"] += 1
            title = _text(record.get(field_map["title"]))
            source_price = _float(record.get(field_map["price"]))
            source_sale_price = _float(record.get(field_map["sale_price"]))
            source_cost_price = (
                _float(record.get(field_map["cost_price"]))
                if field_map.get("cost_price") else None
            )
            gross_purchase_price_per_kg = (
                _float(record.get(field_map["gross_purchase_price_per_kg"]))
                if field_map.get("gross_purchase_price_per_kg") else None
            )
            purchase_discount_percent = (
                _float(record.get(field_map["purchase_discount_percent"]))
                if field_map.get("purchase_discount_percent") else None
            )
            net_purchase_price_per_kg = (
                _float(record.get(field_map["net_purchase_price_per_kg"]))
                if field_map.get("net_purchase_price_per_kg") else None
            )
            if (
                net_purchase_price_per_kg is None
                and gross_purchase_price_per_kg is not None
                and purchase_discount_percent is not None
            ):
                net_purchase_price_per_kg = (
                    gross_purchase_price_per_kg
                    * (1 - purchase_discount_percent / 100)
                )
            kg_per_purchase_unit = (
                _package_weight(record.get(field_map["kg_per_purchase_unit"]))
                if field_map.get("kg_per_purchase_unit") else None
            )
            kg_per_sales_unit = (
                _package_weight(record.get(field_map["kg_per_sales_unit"]))
                if field_map.get("kg_per_sales_unit") else None
            )
            if route.uses_certilas_filters:
                confirmed_weight = _certilas_confirmed_package_weight(
                    record.get(field_map["ean"])
                )
                if confirmed_weight is not None:
                    kg_per_purchase_unit = confirmed_weight
                    kg_per_sales_unit = confirmed_weight
            # De verkoopprijsbasis is altijd één verpakking. Een bundel/omdoos
            # kan later uit meerdere van deze verkoopverpakkingen bestaan.
            if kg_per_purchase_unit:
                kg_per_sales_unit = kg_per_purchase_unit
            calculated_purchase_units = (
                kg_per_sales_unit / kg_per_purchase_unit
                if (
                    kg_per_sales_unit is not None
                    and kg_per_sales_unit > 0
                    and kg_per_purchase_unit is not None
                    and kg_per_purchase_unit > 0
                )
                else None
            )
            calculated_cost_price = (
                net_purchase_price_per_kg * kg_per_sales_unit
                if (
                    net_purchase_price_per_kg is not None
                    and kg_per_sales_unit is not None
                    and kg_per_sales_unit > 0
                )
                else net_purchase_price_per_kg
            )
            if source_cost_price is not None:
                calculated_cost_price = source_cost_price
            source_weight_grams = _float(
                record.get(field_map["weight"])
            )
            if field_map.get("weight_kg"):
                weight_kg = _float(record.get(field_map["weight_kg"]))
                source_weight_grams = (
                    weight_kg * 1000 if weight_kg is not None else None
                )
            selling_price = (
                source_sale_price if source_sale_price is not None else source_price
            )
            if field_map.get("stock"):
                is_available = _stock_is_available(
                    record.get(field_map["stock"]), supplier
                )
            else:
                # Een ingestelde voorraad is bij een bestand zonder
                # voorraadkolom een bewuste aanname. Pas die uitsluitend toe
                # wanneer het artikel een geldige verkoopprijs heeft.
                is_available = (
                    selling_price is not None and float(selling_price) > 0
                )
            is_publishable = (
                (
                    is_available
                    or _stock_stays_active_at_zero(
                        record.get(field_map["stock"]), supplier
                    )
                )
                and selling_price is not None
                and float(selling_price) > 0
            )
            mapped_stock = (
                int(supplier.get("available_stock_quantity") or 1)
                if is_available else 0
            )
            normalized = {
                "sku": sku,
                "supplier_sku": supplier_sku,
                "ean": _ean(record.get(field_map["ean"])),
                "vendor": supplier["name"],
                "brand": supplier["name"],
                "source_title": title,
                "source_description": _text(record.get(field_map["description"])),
                "price": source_price,
                "sale_price": source_sale_price,
                "purchase_unit": (
                    _text(record.get(field_map["purchase_unit"])) or "stuk"
                    if field_map.get("purchase_unit") else "stuk"
                ),
                "sales_unit": (
                    _text(record.get(field_map["sales_unit"])) or "stuk"
                    if field_map.get("sales_unit") else "stuk"
                ),
                "purchase_units_per_sales_unit": (
                    _float(record.get(
                        field_map["purchase_units_per_sales_unit"]
                    )) or calculated_purchase_units or 1
                    if field_map.get("purchase_units_per_sales_unit")
                    else calculated_purchase_units or 1
                ),
                "unit_calculation_mode": (
                    _text(record.get(field_map["unit_calculation_mode"])).lower()
                    if field_map.get("unit_calculation_mode") else "multiply"
                ),
                "gross_purchase_price_per_kg": gross_purchase_price_per_kg,
                "purchase_discount_percent": purchase_discount_percent,
                "net_purchase_price_per_kg": net_purchase_price_per_kg,
                "kg_per_purchase_unit": kg_per_purchase_unit,
                "kg_per_sales_unit": kg_per_sales_unit,
                "cost_price": calculated_cost_price,
                "weight_grams": source_weight_grams,
                "stock_quantity": mapped_stock,
                "available": int(is_available),
                "shopify_status": "active" if is_publishable else "draft",
                "product_type": _text(record.get(field_map["product_type"])),
                "category": _text(record.get(field_map["category"])),
                "category_full": _text(record.get(field_map["category_full"])),
                "source_updated_at": _text(record.get(field_map["updated_at"])),
                "raw_data_json": json.dumps(record, ensure_ascii=False, default=str),
            }
            if normalized["purchase_units_per_sales_unit"] <= 0:
                normalized["purchase_units_per_sales_unit"] = 1
            if normalized["unit_calculation_mode"] not in {"multiply", "divide"}:
                normalized["unit_calculation_mode"] = "multiply"
            normalized["product_group_name"] = (
                _text(record.get(field_map["product_group_name"]))
                if field_map.get("product_group_name")
                else _normalized_product_group(
                    title, normalized["category"], normalized["category_full"]
                )
            )
            if route.uses_certilas_filters:
                normalized["product_group_name"] = _certilas_product_group(
                    normalized["source_description"],
                    normalized["product_group_name"],
                )
            normalized["filter_values_json"] = json.dumps(
                _certilas_filter_values(
                    normalized["product_group_name"], title,
                    normalized["source_description"],
                )
                if route.uses_certilas_filters
                else (
                    _split_filter_values(record.get(field_map["filter"]))
                    if field_map.get("filter")
                    else _derived_filter_values(
                        title, normalized["source_description"]
                    )
                ),
                ensure_ascii=False,
            )
            normalized["execution"] = (
                _text(record.get(field_map["execution"]))
                if field_map.get("execution")
                else _derived_execution(title, normalized["product_group_name"])
            )
            normalized["subcategory_3"] = ""
            normalized["subcategory_4"] = ""
            normalized["subcategory_5"] = ""
            content_hash = hashlib.sha256(
                json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            existing = conn.execute(
                """
                SELECT content_hash,product_group_name,filter_values_json,execution,
                       subcategory_3,subcategory_4,subcategory_5,
                       raw_data_json,content_locked
                FROM products WHERE sku=?
                """,
                (sku,),
            ).fetchone()
            if (
                existing
                and (
                    route.preserves_enrichment
                    or supplier.get("request_options", {}).get(
                        "preserve_enrichment"
                    )
                )
            ):
                existing_group = existing["product_group_name"]
                if route.uses_certilas_filters:
                    existing_group = _certilas_product_group(
                        normalized["source_description"], existing_group
                    )
                normalized["product_group_name"] = (
                    existing_group or normalized["product_group_name"]
                )
                normalized["execution"] = (
                    existing["execution"] or normalized["execution"]
                )
                normalized["subcategory_3"] = existing["subcategory_3"] or ""
                normalized["subcategory_4"] = existing["subcategory_4"] or ""
                normalized["subcategory_5"] = existing["subcategory_5"] or ""
                if (
                    not route.uses_certilas_filters
                    and existing["filter_values_json"] not in (None, "", "[]")
                ):
                    normalized["filter_values_json"] = existing[
                        "filter_values_json"
                    ]
                content_hash = hashlib.sha256(
                    json.dumps(
                        normalized, sort_keys=True, ensure_ascii=False
                    ).encode()
                ).hexdigest()
            elif existing is None and route.infers_category_path:
                inferred_path = _infer_valkenpower_category_path(
                    conn, sku, normalized["source_title"]
                )
                if inferred_path:
                    (
                        normalized["product_group_name"], inferred_filter,
                        normalized["execution"], normalized["subcategory_3"],
                        normalized["subcategory_4"], normalized["subcategory_5"],
                    ) = inferred_path
                    normalized["filter_values_json"] = json.dumps(
                        [inferred_filter] if inferred_filter else [],
                        ensure_ascii=False,
                    )
                    content_hash = hashlib.sha256(
                        json.dumps(
                            normalized, sort_keys=True, ensure_ascii=False
                        ).encode()
                    ).hexdigest()
            if existing is None:
                stats["inserted"] += 1
            elif existing["content_hash"] == content_hash:
                stats["unchanged"] += 1
            else:
                stats["updated"] += 1
            existing_raw = (
                json.loads(existing["raw_data_json"] or "{}")
                if existing else {}
            )
            product_maker_overrides = existing_raw.get("product_maker_overrides")
            if isinstance(product_maker_overrides, dict):
                for field in (
                    "ean", "vendor", "brand", "source_description",
                    "sale_price", "cost_price",
                    "purchase_unit", "sales_unit",
                    "purchase_units_per_sales_unit", "stock_quantity",
                    "available", "product_type", "product_group_name",
                ):
                    if field in product_maker_overrides:
                        normalized[field] = product_maker_overrides[field]
                content_hash = hashlib.sha256(
                    json.dumps(
                        normalized, sort_keys=True, ensure_ascii=False
                    ).encode()
                ).hexdigest()
            website_enrichment = existing_raw.get("website_enrichment")
            preserved_raw = {
                key: existing_raw.get(key)
                for key in (
                    "website_enrichment",
                    "valkenpower_category_evidence",
                    "collection_index",
                    "product_maker_overrides",
                )
                if isinstance(existing_raw.get(key), dict)
                and existing_raw.get(key)
            }
            if preserved_raw:
                imported_raw = json.loads(normalized["raw_data_json"] or "{}")
                imported_raw.update(preserved_raw)
                normalized["raw_data_json"] = json.dumps(
                    imported_raw, ensure_ascii=False, default=str
                )
            conn.execute(
                """
                INSERT INTO products (
                    sku,supplier_sku,ean,vendor,brand,source_title,source_description,
                    price,sale_price,cost_price,purchase_unit,sales_unit,
                    purchase_units_per_sales_unit,unit_calculation_mode,
                    gross_purchase_price_per_kg,purchase_discount_percent,
                    net_purchase_price_per_kg,kg_per_purchase_unit,
                    kg_per_sales_unit,
                    weight_grams,stock_quantity,available,product_type,
                    category,category_full,source_updated_at,raw_data_json,content_hash,
                    shopify_status,product_group_name,filter_values_json,execution,
                    subcategory_3,subcategory_4,subcategory_5,
                    first_seen_at,last_seen_at,updated_at
                ) VALUES (
                    :sku,:supplier_sku,:ean,:vendor,:brand,:source_title,:source_description,
                    :price,:sale_price,:cost_price,:purchase_unit,:sales_unit,
                    :purchase_units_per_sales_unit,:unit_calculation_mode,
                    :gross_purchase_price_per_kg,:purchase_discount_percent,
                    :net_purchase_price_per_kg,:kg_per_purchase_unit,
                    :kg_per_sales_unit,
                    :weight_grams,:stock_quantity,:available,:product_type,
                    :category,:category_full,:source_updated_at,:raw_data_json,:content_hash,
                    :shopify_status,:product_group_name,:filter_values_json,:execution,
                    :subcategory_3,:subcategory_4,:subcategory_5,
                    :first_seen_at,:last_seen_at,:updated_at
                )
                ON CONFLICT(sku) DO UPDATE SET
                    supplier_sku=excluded.supplier_sku,ean=excluded.ean,vendor=excluded.vendor,
                    brand=excluded.brand,source_title=excluded.source_title,
                    source_description=excluded.source_description,price=excluded.price,
                    sale_price=excluded.sale_price,
                    cost_price=COALESCE(excluded.cost_price,products.cost_price),
                    purchase_unit=excluded.purchase_unit,
                    sales_unit=excluded.sales_unit,
                    purchase_units_per_sales_unit=excluded.purchase_units_per_sales_unit,
                    unit_calculation_mode=excluded.unit_calculation_mode,
                    gross_purchase_price_per_kg=excluded.gross_purchase_price_per_kg,
                    purchase_discount_percent=excluded.purchase_discount_percent,
                    net_purchase_price_per_kg=excluded.net_purchase_price_per_kg,
                    kg_per_purchase_unit=excluded.kg_per_purchase_unit,
                    kg_per_sales_unit=excluded.kg_per_sales_unit,
                    weight_grams=excluded.weight_grams,
                    stock_quantity=excluded.stock_quantity,available=excluded.available,
                    product_type=excluded.product_type,category=excluded.category,
                    category_full=excluded.category_full,source_updated_at=excluded.source_updated_at,
                    raw_data_json=excluded.raw_data_json,content_hash=excluded.content_hash,
                    shopify_status=excluded.shopify_status,
                    product_group_name=excluded.product_group_name,
                    filter_values_json=excluded.filter_values_json,
                    execution=excluded.execution,
                    subcategory_3=COALESCE(products.subcategory_3,excluded.subcategory_3),
                    subcategory_4=COALESCE(products.subcategory_4,excluded.subcategory_4),
                    subcategory_5=COALESCE(products.subcategory_5,excluded.subcategory_5),
                    source_present=1,shopify_draft_since=NULL,
                    last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at
                """,
                {
                    **normalized,
                    "content_hash": content_hash,
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "updated_at": now,
                },
            )
            image_record = dict(original_record)
            if field_map.get("primary_image"):
                image_record["primary_image"] = original_record.get(
                    field_map["primary_image"]
                )
            configured_primary_fields = [
                field for field, config in source_transformations.items()
                if config.get("content_type") == "primary_image"
            ]
            if configured_primary_fields and not image_record.get(
                "primary_image"
            ):
                image_record["primary_image"] = original_record.get(
                    configured_primary_fields[0]
                )
            configured_additional_fields = [
                field for field, config in source_transformations.items()
                if config.get("content_type") == "additional_images"
            ]
            source_images = _image_urls(
                image_record, configured_additional_fields
            )
            if source_images and not (existing and existing["content_locked"]):
                preserved_enrichment_images = []
                if isinstance(website_enrichment, dict) and website_enrichment:
                    preserved_enrichment_images = list(
                        dict.fromkeys(
                            str(url).strip()
                            for url in website_enrichment.get("image_urls") or []
                            if str(url).strip()
                        )
                    )
                    if not preserved_enrichment_images:
                        preserved_enrichment_images = [
                            row["image_url"] for row in conn.execute(
                                """SELECT image_url FROM product_images
                                   WHERE sku=? AND image_url LIKE '%certilas.com%'
                                   ORDER BY position,id""",
                                (sku,),
                            ).fetchall()
                        ]
                conn.execute("DELETE FROM product_images WHERE sku=?", (sku,))
                combined_images = list(dict.fromkeys([
                    *source_images, *preserved_enrichment_images,
                ]))
                for position, url in enumerate(combined_images, 1):
                    conn.execute(
                        "INSERT OR IGNORE INTO product_images(sku,image_url,position,alt_text) VALUES (?,?,?,?)",
                        (sku, url, position, title),
                    )
            if progress_callback and (
                record_index == total_records or record_index % 25 == 0
            ):
                percent = 25 + int(60 * record_index / total_records)
                progress_callback(
                    min(percent, 85),
                    f"Producten verwerken: {record_index} van {total_records}",
                )
        if seen:
            placeholders = ",".join("?" for _ in seen)
            stats["missing"] = conn.execute(
                f"SELECT COUNT(*) FROM products WHERE sku NOT IN ({placeholders}) AND source_present=1",
                tuple(seen),
            ).fetchone()[0]
            conn.execute(
                f"""UPDATE products SET source_present=0,stock_quantity=0,available=0,
                    shopify_status='draft',
                    shopify_draft_since=COALESCE(shopify_draft_since,?),
                    updated_at=? WHERE sku NOT IN ({placeholders})""",
                (now, now, *seen),
            )
        if slug == "valkenpower" and conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='official_category_evidence'"""
        ).fetchone():
            requeue = conn.execute(
                """DELETE FROM official_category_evidence
                   WHERE status IN ('not_found','not_listed')
                     AND pim_sku IN (
                       SELECT sku FROM products
                       WHERE source_present=1 AND available=1
                         AND COALESCE(stock_quantity,0)>0
                     )"""
            )
            stats["breadcrumb_requeued_on_restock"] = max(
                0, int(requeue.rowcount or 0)
            )
        if route.applies_alloy_surcharges:
            from app.suppliers.certilas_surcharges import (
                apply_certilas_alloy_surcharge_bundles,
            )
            stats["alloy_surcharge_bundles"] = (
                apply_certilas_alloy_surcharge_bundles(conn, now)
            )
        conn.execute(
            """
            UPDATE import_runs SET finished_at=?,status='success',rows_seen=?,
                inserted=?,updated=?,unchanged=?,missing=?,message=?
            WHERE id=?
            """,
            (
                now, stats["seen"], stats["inserted"], stats["updated"],
                stats["unchanged"], stats["missing"], str(archive_path), run_id,
            ),
        )
    with _connect(REGISTRY_PATH) as conn:
        conn.execute(
            "UPDATE suppliers SET last_run_at=?,last_run_status='success',last_run_message=?,updated_at=? WHERE slug=?",
            (now, json.dumps(stats), now, slug),
        )
    return stats


def supplier_stats(slug: str) -> dict[str, int]:
    path = init_supplier_database(slug)
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) total,
                SUM(source_present=1) active,
                SUM(source_present=0) missing,
                SUM(available=1) available,
                SUM(CASE WHEN source_description IS NOT NULL AND TRIM(source_description)<>'' THEN 1 ELSE 0 END) descriptions
            FROM products
            """
        ).fetchone()
        images = conn.execute("SELECT COUNT(*) FROM product_images").fetchone()[0]
    result = {key: int(row[key] or 0) for key in row.keys()}
    result["images"] = images
    return result


def supplier_database_cleanup_preview(slug: str) -> dict[str, int]:
    path = init_supplier_database(slug)
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) total,
                SUM(source_present=1) active,
                SUM(source_present=0) missing
            FROM products
            """
        ).fetchone()
        missing_images = conn.execute(
            """
            SELECT COUNT(*)
            FROM product_images i
            JOIN products p ON p.sku=i.sku
            WHERE p.source_present=0
            """
        ).fetchone()[0]
    return {
        "total": int(row["total"] or 0),
        "active": int(row["active"] or 0),
        "missing": int(row["missing"] or 0),
        "missing_images": int(missing_images or 0),
    }


def cleanup_missing_supplier_products(slug: str) -> dict[str, Any]:
    """Archiveer en verwijder uitsluitend niet-actuele lokale PIM-producten."""
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")
    path = init_supplier_database(slug)
    preview = supplier_database_cleanup_preview(slug)
    if not preview["missing"]:
        return {
            **preview,
            "removed": 0,
            "removed_images": 0,
            "archive": "",
        }

    archive_dir = EXPORT_DIR / slug / "database-cleanup"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / (
        f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-"
        "missing-products.json.gz"
    )
    with _connect(path) as conn:
        products = [
            dict(row) for row in conn.execute(
                "SELECT * FROM products WHERE source_present=0 ORDER BY sku"
            ).fetchall()
        ]
        images = [
            dict(row) for row in conn.execute(
                """
                SELECT i.* FROM product_images i
                JOIN products p ON p.sku=i.sku
                WHERE p.source_present=0
                ORDER BY i.sku,i.position
                """
            ).fetchall()
        ]
        with gzip.open(archive_path, "wt", encoding="utf-8") as archive:
            json.dump(
                {
                    "supplier": supplier["name"],
                    "slug": slug,
                    "archived_at": utc_now(),
                    "products": products,
                    "images": images,
                },
                archive,
                ensure_ascii=False,
                default=str,
            )
        conn.execute(
            "DELETE FROM products WHERE source_present=0"
        )

    # Geef vrijgekomen SQLite-pagina's na de transactie terug aan het bestand.
    with _connect(path) as conn:
        conn.execute("VACUUM")
    return {
        **preview,
        "removed": len(products),
        "removed_images": len(images),
        "archive": str(archive_path),
    }


def migrate_legacy_v2(slug: str, legacy_path: Path) -> dict[str, int]:
    """Neem de oude centrale PIM-data over zonder actuele brondata te overschrijven."""
    if not legacy_path.exists():
        raise FileNotFoundError(legacy_path)
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError(f"Onbekende leverancier: {slug}")

    now = utc_now()
    target_path = init_supplier_database(slug)
    source = _connect(legacy_path)
    target = _connect(target_path)
    stats = {"inserted_missing": 0, "enriched": 0, "images_added": 0}
    try:
        legacy_products = source.execute("SELECT * FROM products").fetchall()
        for product in legacy_products:
            sku = _text(product["sku"]).upper()
            if not sku:
                continue
            existing = target.execute(
                "SELECT sku FROM products WHERE sku=?", (sku,)
            ).fetchone()
            if existing:
                target.execute(
                    """
                    UPDATE products SET
                        ai_title=COALESCE(NULLIF(ai_title,''), ?),
                        html_description=COALESCE(NULLIF(html_description,''), ?),
                        cost_price=COALESCE(cost_price, ?),
                        shopify_handle=COALESCE(NULLIF(shopify_handle,''), ?),
                        shopify_status=COALESCE(NULLIF(shopify_status,''), ?),
                        inventory_policy=COALESCE(NULLIF(inventory_policy,''), ?)
                    WHERE sku=?
                    """,
                    (
                        product["ai_title"], product["html_description"],
                        product["cost_price"], product["shopify_handle"],
                        product["shopify_status"], product["inventory_policy"], sku,
                    ),
                )
                stats["enriched"] += 1
            else:
                raw = {
                    "legacy_source": str(legacy_path),
                    "legacy_product_id": product["id"],
                }
                target.execute(
                    """
                    INSERT INTO products (
                        sku,supplier_sku,ean,vendor,brand,source_title,
                        source_description,price,cost_price,weight_grams,
                        product_type,category,source_present,ai_title,
                        html_description,shopify_handle,shopify_status,
                        inventory_policy,raw_data_json,first_seen_at,last_seen_at,
                        updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        sku, sku, product["ean"], supplier["name"],
                        product["brand"] or supplier["name"],
                        product["source_title"], product["source_description"],
                        product["price"], product["cost_price"],
                        (float(product["weight"]) * 1000) if product["weight"] else None,
                        product["product_type"], product["category"],
                        product["ai_title"], product["html_description"],
                        product["shopify_handle"], product["shopify_status"] or "draft",
                        product["inventory_policy"] or "continue",
                        json.dumps(raw), now, now, now,
                    ),
                )
                stats["inserted_missing"] += 1

        for image in source.execute(
            "SELECT sku,url,position,alt_text FROM product_images"
        ).fetchall():
            if not target.execute(
                "SELECT 1 FROM products WHERE sku=?", (image["sku"],)
            ).fetchone():
                continue
            cursor = target.execute(
                """
                INSERT OR IGNORE INTO product_images(sku,image_url,position,alt_text)
                VALUES (?,?,?,?)
                """,
                (image["sku"], image["url"], image["position"] or 1, image["alt_text"]),
            )
            stats["images_added"] += int(cursor.rowcount > 0)
        target.commit()
    finally:
        source.close()
        target.close()
    return stats


def list_products(
    slug: str,
    limit: int = 500,
    query: str = "",
) -> list[dict[str, Any]]:
    path = init_supplier_database(slug)
    search = query.strip()
    pattern = f"%{search}%"
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT sku,source_title,ai_title,source_description,ean,price,sale_price,
                stock_quantity,available,
                purchase_unit,sales_unit,purchase_units_per_sales_unit,
                unit_calculation_mode,gross_purchase_price_per_kg,
                purchase_discount_percent,net_purchase_price_per_kg,
                kg_per_purchase_unit,kg_per_sales_unit,cost_price,
                category,product_group_name,execution,
                filter_values_json,
                source_present,source_updated_at
            FROM products
            WHERE ?='' OR sku LIKE ? COLLATE NOCASE
                OR source_title LIKE ? COLLATE NOCASE
                OR ai_title LIKE ? COLLATE NOCASE
                OR source_description LIKE ? COLLATE NOCASE
                OR ean LIKE ? COLLATE NOCASE
            ORDER BY sku LIMIT ?
            """,
            (search, pattern, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
    result = []
    for row in rows:
        product = dict(row)
        try:
            filters = json.loads(product.pop("filter_values_json") or "[]")
        except json.JSONDecodeError:
            filters = []
        product["filter"] = " | ".join(filters)
        product["display_title"] = (
            product.get("source_title")
            or product.get("ai_title")
            or product.get("source_description")
            or ""
        )
        result.append(product)
    return result


def search_supplier_products(slug: str, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    path = init_supplier_database(slug)
    pattern = f"%{query.strip()}%"
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT sku,source_title,price,sale_price,available,source_present
            FROM products
            WHERE ?='' OR sku LIKE ? OR source_title LIKE ? OR ean LIKE ?
            ORDER BY source_present DESC, source_title COLLATE NOCASE, sku
            LIMIT ?
            """,
            (query.strip(), pattern, pattern, pattern, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_supplier_product(slug: str, sku: str) -> dict[str, Any] | None:
    path = init_supplier_database(slug)
    with _connect(path) as conn:
        product = conn.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
        if not product:
            return None
        result = dict(product)
        result["images"] = [
            dict(row) for row in conn.execute(
                "SELECT image_url,position,alt_text FROM product_images WHERE sku=? ORDER BY position",
                (sku,),
            ).fetchall()
        ]
    try:
        result["raw_data"] = json.loads(result.get("raw_data_json") or "{}")
    except json.JSONDecodeError:
        result["raw_data"] = {}
    manual_images = (
        (result["raw_data"].get("product_maker_overrides") or {})
        .get("manual_images") or []
    )
    known_images = {str(item.get("image_url") or "") for item in result["images"]}
    result["images"].extend(
        item for item in manual_images
        if str(item.get("image_url") or "") not in known_images
        and Path(str(item.get("image_url") or "")).is_file()
    )
    return result


def _persist_product_maker_local_image(
    slug: str, sku: str, image: dict[str, Any], position: int,
) -> dict[str, Any] | None:
    """Kopieer een tijdelijke upload naar een duurzaam leveranciers-PIM-pad."""
    source = Path(str(image.get("url") or ""))
    if not source.is_absolute() or not source.is_file() or not image.get("selected"):
        return None
    try:
        source.resolve().relative_to(PRODUCT_MAKER_UPLOAD_DIR.resolve())
    except ValueError:
        return None
    suffix = source.suffix.casefold()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        return None
    safe_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", slug).strip("-") or "supplier"
    safe_sku = re.sub(r"[^A-Za-z0-9_.-]+", "-", sku).strip("-") or "product"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    directory = PRODUCT_MAKER_SUPPLIER_ASSET_DIR / safe_slug / safe_sku
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}{suffix}"
    if not target.exists():
        target.write_bytes(source.read_bytes())
    return {
        "image_url": str(target.resolve()),
        "position": int(position),
        "alt_text": str(image.get("title") or ""),
        "source": "handmatige upload",
    }


def save_product_maker_values(
    slug: str, sku: str, values: dict[str, Any],
    images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist confirmed productmaker values in one supplier product."""
    path = init_supplier_database(slug)
    now = utc_now()
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT raw_data_json FROM products WHERE sku=?", (sku,)
        ).fetchone()
        if not row:
            # A productmaker product may legitimately be new for this supplier.
            # Create it explicitly; the saved overrides below make it durable
            # across later supplier imports.
            conn.execute(
                """INSERT INTO products(
                   sku,supplier_sku,source_title,source_description,source_present,
                   raw_data_json,first_seen_at,last_seen_at,updated_at
                   ) VALUES(?,?,?,?,1,?,?,?,?)""",
                (
                    sku, sku, str(values.get("title") or sku),
                    str(values.get("short_description") or ""),
                    json.dumps(
                        {"product_maker_created": True, "supplier_slug": slug},
                        ensure_ascii=False,
                    ),
                    now, now, now,
                ),
            )
            row = conn.execute(
                "SELECT raw_data_json FROM products WHERE sku=?", (sku,)
            ).fetchone()
        try:
            raw = json.loads(row["raw_data_json"] or "{}")
        except json.JSONDecodeError:
            raw = {}
        tags = values.get("tags") or []
        if isinstance(tags, str):
            tags = [item.strip() for item in tags.splitlines() if item.strip()]
        overrides = {
            "ean": str(values.get("ean") or ""),
            "vendor": str(values.get("vendor") or ""),
            "brand": str(values.get("vendor") or ""),
            "ai_title": str(values.get("title") or ""),
            "html_description": str(values.get("description_html") or ""),
            "source_description": str(values.get("short_description") or ""),
            "sale_price": float(str(values.get("sale_price") or 0).replace(",", ".")),
            "cost_price": float(str(values.get("purchase_price") or 0).replace(",", ".")),
            "purchase_unit": str(values.get("purchase_unit") or "stuk"),
            "sales_unit": str(values.get("sales_unit") or "stuk"),
            "purchase_units_per_sales_unit": float(
                str(values.get("unit_factor") or 1).replace(",", ".")
            ),
            "stock_quantity": int(values.get("initial_quantity") or 0),
            "available": int(int(values.get("initial_quantity") or 0) > 0),
            "product_type": str(values.get("product_type") or ""),
            "product_group_name": str(values.get("product_type") or ""),
            "ai_tags_json": tags,
            "seo_title": str(values.get("seo_title") or ""),
            "seo_description": str(values.get("seo_description") or ""),
            "compare_at_price": str(values.get("compare_at_price") or ""),
            "category_id": str(values.get("category_id") or ""),
            "category_label": str(values.get("category_label") or ""),
            "metafields": values.get("metafields") or [],
            "source_url": str(values.get("source_url") or ""),
            "notes": str(values.get("notes") or ""),
            "saved_at": now,
        }
        manual_images = [
            saved for position, image in enumerate(images or [], start=1)
            if (saved := _persist_product_maker_local_image(
                slug, sku, image, position
            )) is not None
        ]
        overrides["manual_images"] = manual_images
        raw["product_maker_overrides"] = overrides
        conn.execute(
            """UPDATE products SET ean=?,vendor=?,brand=?,ai_title=?,
               html_description=?,source_description=?,sale_price=?,cost_price=?,
               purchase_unit=?,sales_unit=?,purchase_units_per_sales_unit=?,
               stock_quantity=?,available=?,product_type=?,product_group_name=?,
               ai_tags_json=?,raw_data_json=?,content_locked=1,
               content_locked_at=?,updated_at=? WHERE sku=?""",
            (
                overrides["ean"], overrides["vendor"], overrides["brand"],
                overrides["ai_title"], overrides["html_description"],
                overrides["source_description"], overrides["sale_price"],
                overrides["cost_price"], overrides["purchase_unit"],
                overrides["sales_unit"], overrides["purchase_units_per_sales_unit"],
                overrides["stock_quantity"], overrides["available"],
                overrides["product_type"], overrides["product_group_name"],
                json.dumps(tags, ensure_ascii=False),
                json.dumps(raw, ensure_ascii=False), now, now, sku,
            ),
        )
        saved_images = len(manual_images)
        for position, image in enumerate(images or [], start=1):
            image_url = str(image.get("url") or "").strip()
            # Openbare beelden blijven in de URL-catalogus. Lokale uploads zijn
            # hierboven naar een duurzaam leveranciersasset gekopieerd en staan
            # in product_maker_overrides.manual_images.
            if (
                not image_url.startswith(("http://", "https://"))
                or not _is_publishable_image_url(image_url)
                or not image.get("selected")
            ):
                continue
            conn.execute(
                """INSERT INTO product_images(sku,image_url,position,alt_text)
                   VALUES(?,?,?,?) ON CONFLICT(sku,image_url) DO UPDATE SET
                   position=excluded.position,alt_text=excluded.alt_text""",
                (sku, image_url, position, overrides["ai_title"]),
            )
            saved_images += 1
    return {"slug": slug, "sku": sku, "saved_images": saved_images}


def _handle(title: str, sku: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (title or sku).lower()).strip("-")
    return value[:240] or sku.lower()


def _shopify_rows(
    slug: str,
    limit: int | None = None,
    title_query: str = "",
) -> Iterable[dict[str, str]]:
    path = init_supplier_database(slug)
    query = "SELECT * FROM products WHERE source_present=1"
    params: tuple[Any, ...] = ()
    if title_query.strip():
        query += (
            " AND (source_title LIKE ? COLLATE NOCASE "
            "OR ai_title LIKE ? COLLATE NOCASE)"
        )
        pattern = f"%{title_query.strip()}%"
        params = (pattern, pattern)
    query += " ORDER BY sku"
    if limit:
        query += " LIMIT ?"
        params = (*params, limit)
    with _connect(path) as conn:
        products = conn.execute(query, params).fetchall()
        for product in products:
            sku = product["sku"]
            title = product["ai_title"] or product["source_title"] or sku
            description = product["html_description"] or product["source_description"] or ""
            price = product["sale_price"] if product["sale_price"] is not None else product["price"]
            images = conn.execute(
                "SELECT image_url,position FROM product_images WHERE sku=? ORDER BY position",
                (sku,),
            ).fetchall()
            base = {
                "Handle": product["shopify_handle"] or _handle(title, sku),
                "Title": title,
                "Body (HTML)": description,
                "Vendor": product["vendor"] or "",
                "Product Category": "",
                "Type": product["category"] or product["product_type"] or "",
                "Tags": ", ".join(filter(None, [product["vendor"], product["category"], product["product_type"]])),
                "Published": (
                    "TRUE"
                    if (product["shopify_status"] or "").lower() == "active"
                    else "FALSE"
                ),
                "Option1 Name": "Title",
                "Option1 Value": "Default Title",
                "Variant SKU": sku,
                "Variant Grams": str(int(product["weight_grams"] or 0)),
                "Variant Inventory Tracker": "shopify",
                "Variant Inventory Qty": str(product["stock_quantity"] or 0),
                "Variant Inventory Policy": product["inventory_policy"] or "continue",
                "Variant Fulfillment Service": "manual",
                "Variant Price": f"{price:.2f}" if price is not None else "",
                "Variant Barcode": product["ean"] or "",
                "Image Src": "",
                "Image Position": "",
                "SEO Title": title[:70],
                "SEO Description": re.sub("<[^>]+>", " ", description)[:155].strip(),
                "Status": product["shopify_status"] or "draft",
            }
            if not images:
                yield base
            for index, image in enumerate(images):
                row = dict(base)
                row["Image Src"] = image["image_url"]
                row["Image Position"] = str(image["position"])
                if index:
                    row = {column: "" for column in SHOPIFY_COLUMNS} | {
                        "Handle": base["Handle"],
                        "Image Src": image["image_url"],
                        "Image Position": str(image["position"]),
                    }
                yield row


def shopify_csv_bytes(
    slug: str,
    limit: int | None = None,
    title_query: str = "",
) -> bytes:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=SHOPIFY_COLUMNS)
    writer.writeheader()
    writer.writerows(_shopify_rows(slug, limit, title_query))
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def save_shopify_export(slug: str, limit: int | None = None) -> Path:
    directory = EXPORT_DIR / slug
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"shopify-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"
    path.write_bytes(shopify_csv_bytes(slug, limit))
    return path
