from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.ai.providers.openai_provider import OpenAIProvider
from app.product_families import get_or_create_product_families, load_stored_product_families
from app.suppliers.hub import (
    REGISTRY_PATH, _connect, get_supplier, init_registry,
    init_supplier_database, utc_now,
)


ENRICHMENT_VERSION = 2


def _merge_welding_position_icons(
    description: str,
    positions: list[str],
    position_images: dict[str, str],
) -> str:
    """Safely add/replace only the welding-position block in locked HTML."""
    if not positions:
        return description
    soup = BeautifulSoup(description or "", "html.parser")
    heading = next(
        (
            tag for tag in soup.find_all(["h2", "h3", "h4"])
            if tag.get_text(" ", strip=True).casefold() == "lasposities"
        ),
        None,
    )
    if heading is None:
        heading = soup.new_tag("h3")
        heading.string = "Lasposities"
        soup.append(heading)
    sibling = heading.find_next_sibling()
    if sibling and sibling.name in {"p", "div", "table"}:
        sibling.decompose()
    table = soup.new_tag("table")
    body = soup.new_tag("tbody")
    row = soup.new_tag("tr")
    for code in positions:
        cell = soup.new_tag("td")
        image_url = str(position_images.get(code) or "").strip()
        if image_url:
            icon = soup.new_tag(
                "img", src=image_url, alt=f"Laspositie {code}",
                width="64", height="64", loading="lazy",
            )
            cell.append(icon)
            cell.append(soup.new_tag("br"))
        label = soup.new_tag("strong")
        label.string = code
        cell.append(label)
        row.append(cell)
    body.append(row)
    table.append(body)
    heading.insert_after(table)
    return str(soup)


def _numeric_enrichment_version(value: Any) -> int:
    """Return a numeric AI version; legacy provenance labels are version 0."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _has_current_dutch_enrichment(item: dict[str, Any]) -> bool:
    enrichment = (item.get("current_raw_data") or {}).get("website_enrichment") or {}
    return (
        enrichment.get("language") == "nl"
        and _numeric_enrichment_version(enrichment.get("enrichment_version"))
        >= ENRICHMENT_VERSION
    )


def init_enrichment_jobs() -> None:
    init_registry()
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS catalogue_enrichment_jobs(
                id TEXT PRIMARY KEY,supplier_slug TEXT NOT NULL,status TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,
                enriched_products INTEGER NOT NULL DEFAULT 0,found_images INTEGER NOT NULL DEFAULT 0,
                current_family TEXT,message TEXT,error TEXT,pid INTEGER,
                created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,updated_at TEXT NOT NULL
            )"""
        )


def catalogue_enrichment_status(slug: str = "certilas") -> dict[str, Any]:
    init_enrichment_jobs()
    supplier = get_supplier(slug) or {}
    families = get_or_create_product_families(slug, supplier=supplier)
    eligible = []
    for family in families:
        ean_variants = [
            item for item in family.get("variants") or []
            if str(item.get("ean") or "").strip()
        ]
        if ean_variants:
            eligible.append((family, ean_variants))
    pending = [
        family for family, variants in eligible
        if any(not _has_current_dutch_enrichment(item) for item in variants)
    ]
    with _connect(REGISTRY_PATH) as connection:
        row = connection.execute(
            """SELECT * FROM catalogue_enrichment_jobs
               WHERE supplier_slug=? ORDER BY created_at DESC LIMIT 1""",
            (slug,),
        ).fetchone()
    return {
        "families": len(families),
        "eligible": len(eligible),
        "pending": len(pending),
        "enriched": len(eligible) - len(pending),
        "job": dict(row) if row else None,
    }


def start_catalogue_enrichment(slug: str = "certilas") -> dict[str, Any]:
    status = catalogue_enrichment_status(slug)
    active = status.get("job") or {}
    if active.get("status") in {"queued", "running"}:
        return active
    job_id = uuid.uuid4().hex
    now = utc_now()
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """INSERT INTO catalogue_enrichment_jobs(
               id,supplier_slug,status,total,created_at,updated_at
               ) VALUES(?,?,'queued',?,?,?)""",
            (job_id, slug, status["pending"], now, now),
        )
    environment = os.environ.copy()
    project_dir = Path(__file__).resolve().parents[2]
    environment["PYTHONPATH"] = str(project_dir)
    process = subprocess.Popen(
        [sys.executable, "-m", "app.suppliers.website_enrichment", "--bulk", slug, job_id],
        cwd=project_dir, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            "UPDATE catalogue_enrichment_jobs SET pid=?,updated_at=? WHERE id=?",
            (process.pid, utc_now(), job_id),
        )
    return {"id": job_id, "status": "queued", "pid": process.pid}


def run_catalogue_enrichment(slug: str, job_id: str) -> None:
    supplier = get_supplier(slug) or {}
    status = catalogue_enrichment_status(slug)
    pending_keys = []
    for family in get_or_create_product_families(slug, supplier=supplier):
        variants = [v for v in family.get("variants") or [] if str(v.get("ean") or "").strip()]
        if variants and any(not _has_current_dutch_enrichment(v) for v in variants):
            pending_keys.append((family["family_key"], family["title"]))
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """UPDATE catalogue_enrichment_jobs SET status='running',total=?,
               started_at=?,updated_at=? WHERE id=?""",
            (len(pending_keys), utc_now(), utc_now(), job_id),
        )
    enriched = images = completed = 0
    errors = []
    for family_key, title in pending_keys:
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE catalogue_enrichment_jobs SET current_family=?,message=?,updated_at=?
                   WHERE id=?""",
                (title, f"Verrijken: {title}", utc_now(), job_id),
            )
        try:
            result = research_certilas_family(family_key, supplier=supplier)
            enriched += int(result.get("updated") or 0)
            images += int(result.get("images") or 0)
        except Exception as exc:
            errors.append(f"{title}: {exc}")
        completed += 1
        with _connect(REGISTRY_PATH) as connection:
            connection.execute(
                """UPDATE catalogue_enrichment_jobs SET completed=?,enriched_products=?,
                   found_images=?,error=?,updated_at=? WHERE id=?""",
                (completed, enriched, images, "\n".join(errors[-20:]), utc_now(), job_id),
            )
    with _connect(REGISTRY_PATH) as connection:
        connection.execute(
            """UPDATE catalogue_enrichment_jobs SET status=?,current_family=NULL,
               message=?,finished_at=?,updated_at=? WHERE id=?""",
            (
                "completed_with_errors" if errors else "completed",
                f"{enriched} producten verrijkt; {images} foto's gevonden; {len(errors)} fouten.",
                utc_now(), utc_now(), job_id,
            ),
        )


def _certilas_url(value: str) -> str:
    value = str(value or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not (
        parsed.hostname == "certilas.com" or parsed.hostname.endswith(".certilas.com")
    ):
        return ""
    return value


def _product_image_url(value: str) -> str:
    value = str(value or "").strip()
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {
            "certilas.com",
            "pro.cdn.certilas.com",
            "portaal.certilas.nl",
        }
        or "..." in value
    ):
        return ""
    path = parsed.path.casefold()
    if not path.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return ""
    return value


def _reachable_image_urls(urls: list[str]) -> list[str]:
    """Keep only URLs that currently return actual image content."""
    reachable = []
    for url in dict.fromkeys(urls):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Weldingshop-PIM/1.0",
                "Range": "bytes=0-2047",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                content_type = str(
                    response.headers.get("Content-Type") or ""
                ).casefold()
                if response.status < 400 and content_type.startswith("image/"):
                    reachable.append(url)
        except Exception:
            continue
    return reachable


def _shopify_position_assets(
    database_path: Path,
    source_images: dict[str, str],
) -> dict[str, str]:
    """Copy official pictograms to Shopify Files and return CDN URLs."""
    if not source_images:
        return {}
    import sqlite3

    from app.shopify.client import ShopifyClient

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS managed_external_assets(
                asset_key TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                shopify_file_id TEXT NOT NULL,
                shopify_cdn_url TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cached = {
            row["asset_key"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM managed_external_assets"
            ).fetchall()
        }
        result: dict[str, str] = {}
        missing: dict[str, str] = {}
        for code, source_url in source_images.items():
            asset_key = f"certilas-welding-position-{code.casefold()}"
            item = cached.get(asset_key)
            if (
                item
                and item.get("source_url") == source_url
                and str(item.get("shopify_cdn_url") or "").startswith(
                    "https://cdn.shopify.com/"
                )
            ):
                result[code] = item["shopify_cdn_url"]
            else:
                missing[code] = source_url
        if not missing:
            return result

        client = ShopifyClient.from_settings()
        for code, source_url in missing.items():
            payload = client.graphql(
                """
                mutation StoreWeldingPosition($files:[FileCreateInput!]!){
                  fileCreate(files:$files){
                    files{
                      ... on MediaImage{id fileStatus image{url}}
                    }
                    userErrors{field message}
                  }
                }
                """,
                {"files": [{
                    "originalSource": source_url,
                    "contentType": "IMAGE",
                    "alt": f"Certilas laspositie {code}",
                    "filename": f"certilas-laspositie-{code.casefold()}.png",
                }]},
            )["fileCreate"]
            if payload.get("userErrors"):
                raise RuntimeError(
                    "Shopify Files weigerde pictogram " + code + ": "
                    + json.dumps(payload["userErrors"], ensure_ascii=False)
                )
            created = (payload.get("files") or [{}])[0]
            file_id = created.get("id")
            if not file_id:
                raise RuntimeError(
                    f"Shopify gaf geen bestands-ID terug voor laspositie {code}."
                )
            cdn_url = str((created.get("image") or {}).get("url") or "")
            file_status = created.get("fileStatus")
            for _ in range(20):
                if (
                    file_status == "READY"
                    and cdn_url.startswith("https://cdn.shopify.com/")
                ):
                    break
                time.sleep(1)
                node = client.graphql(
                    """
                    query StoredWeldingPosition($id:ID!){
                      node(id:$id){
                        ... on MediaImage{id fileStatus image{url}}
                      }
                    }
                    """,
                    {"id": file_id},
                ).get("node") or {}
                file_status = node.get("fileStatus")
                cdn_url = str((node.get("image") or {}).get("url") or "")
            if not cdn_url.startswith("https://cdn.shopify.com/"):
                raise RuntimeError(
                    f"Shopify-CDN-URL voor laspositie {code} is niet gereed."
                )
            asset_key = f"certilas-welding-position-{code.casefold()}"
            connection.execute(
                """
                INSERT INTO managed_external_assets(
                    asset_key,source_url,shopify_file_id,
                    shopify_cdn_url,updated_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(asset_key) DO UPDATE SET
                    source_url=excluded.source_url,
                    shopify_file_id=excluded.shopify_file_id,
                    shopify_cdn_url=excluded.shopify_cdn_url,
                    updated_at=excluded.updated_at
                """,
                (asset_key, source_url, file_id, cdn_url, utc_now()),
            )
            result[code] = cdn_url
        connection.commit()
        return result


def _readable_fact(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip().rstrip(",") for item in value if str(item).strip())
    return str(value or "").strip()


def _fact_label(value: str) -> str:
    labels = {
        "gas_acc_en_iso_14175": "Beschermgas volgens EN ISO 14175",
        "analysis": "Chemische analyse",
        "mechanical_values_as_welded": "Mechanische waarden in gelaste toestand",
        "Rp0,2_MPa": "Rekgrens Rp0,2 (MPa)", "Rm_MPa": "Treksterkte Rm (MPa)",
        "A5_percent": "Rek A5 (%)", "Impact_Energy_J_ISO_V_RT": "Kerfslagwaarde ISO-V bij kamertemperatuur (J)",
        "Impact_Energy_J_ISO_V_-196C": "Kerfslagwaarde ISO-V bij -196 °C (J)",
    }
    return labels.get(value, value.replace("_", " ").strip().capitalize())


def _section_html(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item).strip().rstrip(",") for item in value if str(item).strip()]
        return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>" if items else ""
    if isinstance(value, dict):
        # Verpakkingsvarianten staan al in de variantnaam en horen niet bij Eigenschappen.
        rows = []
        for key, item in value.items():
            if key == "packaging" or item in (None, "", [], {}):
                continue
            label = html.escape(_fact_label(str(key)))
            if isinstance(item, dict):
                nested = "".join(
                    f"<li><strong>{html.escape(_fact_label(str(nested_key)))}:</strong> "
                    f"{html.escape(_readable_fact(nested_value))}</li>"
                    for nested_key, nested_value in item.items() if nested_value not in (None, "")
                )
                if nested:
                    rows.append(f"<li><strong>{label}</strong><ul>{nested}</ul></li>")
            else:
                rows.append(f"<li><strong>{label}:</strong> {html.escape(_readable_fact(item))}</li>")
        return "<ul>" + "".join(rows) + "</ul>" if rows else ""
    text = _readable_fact(value)
    return f"<p>{html.escape(text)}</p>" if text else ""


def research_certilas_family(
    family_key: str,
    *,
    database_path: str | Path | None = None,
    provider: OpenAIProvider | None = None,
    supplier: dict[str, Any] | None = None,
    image_validator: Callable[[list[str]], list[str]] | None = None,
) -> dict[str, Any]:
    """Research and persist one family, only for variants matched by exact EAN."""
    supplier = supplier if supplier is not None else (get_supplier("certilas") or {})
    website_url = _certilas_url(supplier.get("website_url") or "")
    if not supplier.get("ai_research_allowed") or not website_url:
        raise ValueError("AI-webonderzoek voor de officiële Certilas-site is niet toegestaan.")
    families = load_stored_product_families("certilas", database_path)
    family = next((item for item in families if item["family_key"] == family_key), None)
    if not family:
        raise ValueError("Onbekende opgeslagen productfamilie.")
    variants = [
        {"sku": item["sku"], "ean": str(item.get("ean") or "").strip()}
        for item in family["variants"] if str(item.get("ean") or "").strip()
    ]
    if not variants:
        raise ValueError("Deze familie bevat geen EAN-codes voor een exacte websitekoppeling.")
    path = Path(database_path) if database_path else init_supplier_database("certilas")
    portal_data: dict[str, Any] = {}
    if supplier.get("has_dealer_username") and supplier.get("has_dealer_secret"):
        process = subprocess.run(
            [sys.executable, "-m", "app.suppliers.certilas_portal", family_key],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True, text=True, timeout=75, check=False,
        )
        if process.returncode == 0 and process.stdout.strip():
            portal_data = json.loads(process.stdout)
    catalogue_url = _certilas_url(supplier.get("catalogue_url") or "")
    prompt = f"""Onderzoek uitsluitend de officiële website certilas.com/nl en de
ingestelde officiële catalogus {catalogue_url or website_url}.
Zoek de officiële productpagina voor deze opgeslagen Certilas-productfamilie:
familienaam: {family['title']}
basis: {family['base']}
varianten (SKU en EAN): {json.dumps(variants, ensure_ascii=False)}
Reeds via beveiligde dealerlogin opgehaalde, exact via EAN te controleren tekst:
{str(portal_data.get('text') or '')[:12000]}

Geef uitsluitend aantoonbare gegevens van de officiële productpagina terug.
Een variant is alleen exact_matched als zijn volledige EAN letterlijk op de pagina staat.
Verzin niets. Neem alleen echte productfoto-URL's op (geen iconen, logo's of laspositie-iconen).
Schrijf alle beschrijvende tekst uitsluitend in natuurlijk, technisch correct Nederlands.
Vertaal Engelse, Duitse, Franse en andere anderstalige brontekst volledig naar het Nederlands.
Laat merknamen, productnamen, artikelnummers, EAN's, chemische symbolen, materiaalsoorten,
lasposities en officiële norm- en classificatiecodes zoals EN ISO, AWS en DIN exact ongewijzigd.
Gebruik geen anderstalige koppen of toelichtingen in type, properties, applications,
classifications, approvals of source_summary.
Antwoord als JSON met: product_page_url, matched_eans (array), type, properties,
applications, classifications (object), approvals (array), welding_positions (array),
image_urls (array), source_summary.
"""
    provider = provider or OpenAIProvider()
    research_model = os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-5.6-terra")
    client = provider.client.with_options(timeout=90.0, max_retries=0)
    response = client.responses.create(
        model=research_model,
        tools=[{
            "type": "web_search",
            "filters": {"allowed_domains": ["certilas.com"]},
        }],
        input=prompt,
    )
    raw_output = response.output_text.strip()
    if raw_output.startswith("```"):
        raw_output = raw_output.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    data = json.loads(raw_output)
    page_url = _certilas_url(data.get("product_page_url") or "")
    if not page_url:
        raise ValueError("AI vond geen geldige officiële Certilas-productpagina.")
    requested_eans = {item["ean"] for item in variants}
    matched_eans = requested_eans.intersection(
        str(value).strip() for value in (data.get("matched_eans") or [])
    )
    matched_eans.update(
        requested_eans.intersection(portal_data.get("matched_eans") or [])
    )
    if not matched_eans:
        raise ValueError("Geen enkele variant kon exact via EAN worden bevestigd.")
    ai_images = [
        url for url in (_product_image_url(value) for value in data.get("image_urls") or [])
        if url
    ]
    portal_images = [
            url for url in (
                _product_image_url(value) for value in portal_data.get("image_urls") or []
            ) if url
        ]
    image_candidates = list(dict.fromkeys(portal_images or ai_images))
    images = (image_validator or _reachable_image_urls)(image_candidates)
    sections = []
    for heading, key in (
        ("Type", "type"), ("Eigenschappen", "properties"),
        ("Toepassingen", "applications"),
    ):
        value = _section_html(data.get(key))
        if value:
            sections.append(f"<h3>{heading}</h3>{value}")
    classifications = data.get("classifications") or {}
    if isinstance(classifications, dict) and classifications:
        items = "".join(
            f"<li><strong>{html.escape(str(key))}:</strong> {html.escape(_readable_fact(value))}</li>"
            for key, value in classifications.items() if value
        )
        if items:
            sections.append(f"<h3>Classificaties</h3><ul>{items}</ul>")
    approvals = _section_html(data.get("approvals"))
    if approvals:
        sections.append(f"<h3>Goedkeuringen</h3>{approvals}")
    welding_positions = list(dict.fromkeys([
        *[str(value).strip() for value in data.get("welding_positions") or []],
        *[str(value).strip() for value in portal_data.get("welding_positions") or []],
    ]))
    welding_positions = [value for value in welding_positions if value]
    position_image_candidates: dict[str, str] = {}
    valid_position_urls: set[str] = set()
    shopify_position_urls: dict[str, str] = {}
    if welding_positions:
        raw_position_images = portal_data.get(
            "welding_position_images"
        ) or {}
        position_image_candidates = {
            str(code).strip().upper(): _product_image_url(url)
            for code, url in raw_position_images.items()
            if str(code).strip().upper() in welding_positions
        }
        valid_position_urls = set(
            (image_validator or _reachable_image_urls)([
                url for url in position_image_candidates.values() if url
            ])
        )
        shopify_position_urls = _shopify_position_assets(
            path,
            {
                code: url
                for code, url in position_image_candidates.items()
                if url in valid_position_urls
            },
        )
        position_items = []
        for code in welding_positions:
            image_url = shopify_position_urls.get(code, "")
            if image_url:
                position_items.append(
                    '<td>'
                    f'<img src="{html.escape(image_url)}" '
                    f'alt="Laspositie {html.escape(code)}" '
                    'width="64" height="64" loading="lazy">'
                    f'<br><strong>{html.escape(code)}</strong></td>'
                )
            else:
                position_items.append(
                    f"<td><strong>{html.escape(code)}</strong></td>"
                )
        sections.append(
            "<h3>Lasposities</h3><table><tbody><tr>"
            + "".join(position_items) + "</tr></tbody></table>"
        )
    description = (
        f"<h2>{html.escape(family['title'])}</h2>" + "".join(sections)
    )
    import sqlite3
    updated = 0
    with sqlite3.connect(path) as connection:
        for item in variants:
            if item["ean"] not in matched_eans:
                continue
            row = connection.execute(
                """SELECT raw_data_json,content_locked,html_description
                   FROM products WHERE sku=?""",
                (item["sku"],)
            ).fetchone()
            if not row:
                continue
            raw = json.loads(row[0] or "{}")
            # Een lock beschermt bestaande redactionele inhoud, maar mag een
            # leeg veld niet verhinderen om voor het eerst veilig te vullen.
            content_locked = bool(row[1]) and bool(str(row[2] or "").strip())
            previous_enrichment = raw.get("website_enrichment") or {}
            previous_images = [
                str(url).strip()
                for url in previous_enrichment.get("image_urls") or []
                if str(url).strip()
            ]
            if not content_locked:
                connection.execute(
                    """DELETE FROM product_images
                       WHERE sku=? AND image_url LIKE '%certilas.com%'""",
                    (item["sku"],),
                )
            if previous_images and not content_locked:
                placeholders = ",".join("?" for _ in previous_images)
                connection.execute(
                    f"""DELETE FROM product_images
                        WHERE sku=? AND image_url IN ({placeholders})""",
                    (item["sku"], *previous_images),
                )
            raw["website_enrichment"] = {
                "enrichment_version": ENRICHMENT_VERSION,
                "language": "nl",
                "source_url": page_url, "matched_by": "ean",
                "matched_value": item["ean"], "researched_at": utc_now(),
                "source_summary": str(data.get("source_summary") or ""),
                "dealer_portal_source_url": portal_data.get("source_url") or "",
                "catalogue_url": catalogue_url,
                "image_urls": images,
                "facts": {
                    "type": data.get("type"),
                    "properties": data.get("properties"),
                    "applications": data.get("applications"),
                    "classifications": classifications,
                    "approvals": data.get("approvals"),
                    "welding_positions": welding_positions,
                    "welding_position_images": {
                        code: shopify_position_urls[code]
                        for code in shopify_position_urls
                    },
                },
            }
            if content_locked:
                locked_description = _merge_welding_position_icons(
                    str(row[2] or ""), welding_positions,
                    shopify_position_urls,
                )
                connection.execute(
                    "UPDATE products SET html_description=?,raw_data_json=?,updated_at=? WHERE sku=?",
                    (
                        locked_description, json.dumps(raw, ensure_ascii=False),
                        utc_now(), item["sku"],
                    ),
                )
            else:
                connection.execute(
                    "UPDATE products SET html_description=?,raw_data_json=?,updated_at=? WHERE sku=?",
                    (description, json.dumps(raw, ensure_ascii=False), utc_now(), item["sku"]),
                )
            # A content lock protects editorial copy, not missing verified
            # supplier media. This is additive and idempotent for both paths.
            for position, image_url in enumerate(images, start=1):
                connection.execute(
                    """INSERT OR IGNORE INTO product_images(sku,image_url,position,alt_text)
                       VALUES(?,?,?,?)""",
                    (item["sku"], image_url, position, family["title"]),
                )
            updated += 1
        if images:
            stored = connection.execute(
                "SELECT family_json FROM product_families WHERE family_key=?",
                (family_key,),
            ).fetchone()
            if stored:
                stored_family = json.loads(stored[0])
                stored_family["image_url"] = images[0]
                connection.execute(
                    "UPDATE product_families SET family_json=? WHERE family_key=?",
                    (json.dumps(stored_family, ensure_ascii=False), family_key),
                )
    return {"updated": updated, "images": len(images), "source_url": page_url}


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--bulk":
        run_catalogue_enrichment(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("Gebruik: python -m app.suppliers.website_enrichment --bulk SLUG JOB_ID")
