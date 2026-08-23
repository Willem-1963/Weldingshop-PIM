from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import base64
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader


PROFILE_KEY = "tecweld-nl-weldingshop"
PROFILE_VERSION = 2
POLISH_MARKERS = re.compile(
    r"\b(?:aby|ale|będzie|cięc\w*|cięcia|ciśnieni\w*|czas|do|funkcj\w*|"
    r"grubości|jest|któr\w*|materiał\w*|może|należy|oraz|palnik\w*|"
    r"parametr\w*|pokrętło|powietrz\w*|prąd\w*|przycisk|przełączen\w*|"
    r"przewod\w*|regulacj\w*|są|służy|spawani\w*|sprężark\w*|"
    r"tryb\w*|uchwyt\w*|ustawi\w*|wartoś\w*|wyboru|wykorzyst\w*|wypływ\w*|"
    r"wyposażon\w*|zakres|zasilani\w*|znamionow\w*|zosta\w*|"
    r"długoś\w*|napięci\w*|przeznaczon\w*|w zestawie|dane techniczne|"
    r"instrukcja|obsługi|bezpieczeństw\w*|zestaw\w*|sklep\w*|"
    r"spawani\w*|uchwyt\w*|wentylator\w*|osprzęt\w*|oferta\w*|"
    r"części\w*|zamienne\w*)\b",
    re.IGNORECASE,
)
ENGLISH_MARKERS = re.compile(
    r"\b(?:brazing wire|pressure regulators?|plasma torches?|welding machines?|"
    r"welding equipment|handle with|slide valve|valve pin|diaphragm|handle knob|"
    r"flowmeters?|diffusers?|electrodes?|professional equipment|workshop use|"
    r"feature bullets?|technical specifications?)\b",
    re.IGNORECASE,
)
PROTECTED_TOKEN = re.compile(
    r"https?://\S+|\b[A-Z]{2,}[A-Z0-9./+-]*\d[A-Z0-9./+-]*\b|"
    r"\b\d+(?:[.,]\d+)?\s*(?:V|A|W|kW|mm|cm|m|kg|g|bar|l/min|%)?\b",
    re.IGNORECASE,
)


def polish_score(value: Any) -> int:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return len(POLISH_MARKERS.findall(text))


def english_score(value: Any) -> int:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return len(ENGLISH_MARKERS.findall(text))


def _customer_translation_text(data: dict[str, Any]) -> str:
    return "\n".join([
        str(data.get("title") or ""),
        str(data.get("product_group_name") or ""),
        str(data.get("description") or ""),
        *[str(key) for key in (data.get("technical_specifications") or {})],
        *[str(value) for value in (data.get("technical_specifications") or {}).values()],
        *[str(value) for value in (data.get("accessories") or [])],
        *[
            str(item.get("label_nl") or item.get("source_label") or "")
            for item in (data.get("feature_icons") or [])
            if isinstance(item, dict)
        ],
    ])


def assert_dutch_product_content(data: dict[str, Any]) -> None:
    customer_text = _customer_translation_text(data)
    if not str(data.get("description") or "").strip():
        raise ValueError("Nederlandse productomschrijving ontbreekt")
    score = polish_score(customer_text)
    if score:
        raise ValueError(
            f"Nederlandse taalcontrole afgekeurd: {score} Poolse tekstsignalen"
        )
    english_signals = english_score(customer_text)
    if english_signals:
        raise ValueError(
            f"Nederlandse taalcontrole afgekeurd: {english_signals} Engelse tekstsignalen"
        )


def validate_complete_product_translation(
    source: dict[str, Any], translated: dict[str, Any]
) -> None:
    for field in ("title", "product_group_name", "description"):
        if str(source.get(field) or "").strip() and not str(
            translated.get(field) or ""
        ).strip():
            raise ValueError(
                f"Volledigheidscontrole mislukt: {field} ontbreekt"
            )
    for field in ("accessories", "feature_icons"):
        if len(translated.get(field) or []) != len(source.get(field) or []):
            raise ValueError(
                f"Volledigheidscontrole mislukt: aantal {field} is gewijzigd"
            )
    if len(translated.get("technical_specifications") or {}) != len(
        source.get("technical_specifications") or {}
    ):
        raise ValueError(
            "Volledigheidscontrole mislukt: aantal technische eigenschappen is gewijzigd"
        )
    source_text = _customer_translation_text(source)
    target_text = _customer_translation_text(translated)
    if len(source_text.strip()) >= 40 and len(target_text.strip()) < len(
        source_text.strip()
    ) * 0.55:
        raise ValueError(
            "Volledigheidscontrole mislukt: de vertaling is te sterk ingekort"
        )
    source_tokens = Counter(PROTECTED_TOKEN.findall(source_text))
    target_tokens = Counter(PROTECTED_TOKEN.findall(target_text))
    missing_tokens = list((source_tokens - target_tokens).elements())
    if missing_tokens:
        raise ValueError(
            "Identiteitscontrole mislukt: technische waarden/codes ontbreken: "
            + ", ".join(missing_tokens[:10])
        )
    assert_dutch_product_content(translated)


def repair_product_dutch(provider: Any, model: str, data: dict[str, Any]) -> dict[str, Any]:
    """One bounded repair attempt; identity, values, units and URLs must not change."""
    prompt = """Zet de klantgerichte velden in onderstaande JSON volledig om naar natuurlijk,
zakelijk technisch Nederlands in Weldingshop-stijl. Vertaal title, product_group_name,
description, de sleutels én
gewone tekstwaarden van technical_specifications, accessories en de label_nl-velden van
feature_icons. Behoud alle getallen, eenheden, modelnamen, merken, normen, SKU's, technische
codes en URL's exact. Voeg niets toe en laat alle overige velden exact staan.
Antwoord uitsluitend met het volledige JSON-object. De aantallen technische eigenschappen,
accessoires en functie-iconen moeten exact gelijk blijven. De vertaling moet minstens 55%
van de bronlengte behouden.\n\n""" + json.dumps(data, ensure_ascii=False)
    attempts = [model]
    if model == "gpt-5.6-terra":
        attempts.append("gpt-5.6-sol")
    last_error: Exception | None = None
    for attempt_model in attempts:
        response = provider.client.with_options(
            timeout=180.0 if attempt_model == model else 240.0,
            max_retries=0,
        ).responses.create(
            model=attempt_model,
            input=(
                prompt
                if attempt_model == model else
                prompt + "\n\nTerra is objectief afgekeurd. Herstel de volledige "
                "vertaling zonder informatie, technische waarden of structuur te verliezen."
            ),
            text={"format": {"type": "json_object"}},
        )
        try:
            repaired = json.loads(response.output_text)
            validate_complete_product_translation(data, repaired)
            return repaired
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            last_error = exc
    raise ValueError(
        f"Strenge Tecweld-vertaalcontrole afgekeurd: {last_error}"
    )


def weldingshop_product_html(
    title: str, description: str, specifications: dict[str, Any],
    feature_icons: list[dict[str, Any]] | None = None,
    accessories: list[str] | None = None,
) -> str:
    description_parts: list[str] = []
    bullets: list[str] = []
    for block in re.split(r"\n\s*\n", description.strip()):
        value = block.strip()
        if not value:
            continue
        if value.startswith("- "):
            bullets.append(value[2:].strip())
            continue
        if bullets:
            description_parts.append(
                "<ul>" + "".join(
                    f"<li>{html.escape(item)}</li>" for item in bullets
                ) + "</ul>"
            )
            bullets = []
        if value.casefold().startswith("belangrijkste kenmerken"):
            description_parts.append(f"<h3>{html.escape(value)}</h3>")
        else:
            description_parts.append(f"<p>{html.escape(value)}</p>")
    if bullets:
        description_parts.append(
            "<ul>" + "".join(
                f"<li>{html.escape(item)}</li>" for item in bullets
            ) + "</ul>"
        )
    rows = "".join(
        f"<tr><th scope='row'>{html.escape(str(key))}</th>"
        f"<td>{html.escape(str(value))}</td></tr>"
        for key, value in specifications.items()
        if value not in (None, "", [], {})
    )
    icon_cells = "".join(
        "<td style='text-align:center;vertical-align:top;padding:10px'>"
        f"<img src='{html.escape(str(item.get('image_url') or ''))}' "
        f"alt='{html.escape(str(item.get('label_nl') or item.get('source_label') or ''))}' "
        "width='60' height='60' loading='lazy'><br>"
        f"<strong>{html.escape(str(item.get('label_nl') or item.get('source_label') or ''))}</strong>"
        "</td>"
        for item in (feature_icons or [])
        if str(item.get("image_url") or "").startswith("https://")
        and str(item.get("label_nl") or item.get("source_label") or "").strip()
    )
    accessories_html = "".join(
        f"<li>{html.escape(str(value))}</li>"
        for value in (accessories or []) if str(value).strip()
    )
    return (
        "<article class='weldingshop-productinformatie' lang='nl-NL'>"
        f"<h2>{html.escape(title)}</h2>"
        + "".join(description_parts)
        + (
            "<h3>Functies en productkenmerken</h3>"
            "<table class='weldingshop-producticonen'><tbody><tr>"
            + icon_cells + "</tr></tbody></table>"
            if icon_cells else ""
        )
        + (
            "<h3>Meegeleverde accessoires</h3><ul>" + accessories_html + "</ul>"
            if accessories_html else ""
        )
        + ("<h3>Technische specificaties</h3><table><tbody>" + rows + "</tbody></table>" if rows else "")
        + "<p><small>Tecweld/Sherman is de fabrikant. "
        "Weldingshop.nl verzorgt de Nederlandse productinformatie.</small></p>"
        "</article>"
    )


def init_content_localization_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS product_content_localization(
            sku TEXT PRIMARY KEY,profile_key TEXT NOT NULL,profile_version INTEGER NOT NULL,
            language TEXT NOT NULL,status TEXT NOT NULL,polish_signal_count INTEGER NOT NULL DEFAULT 0,
            source_hash TEXT,localized_hash TEXT,error TEXT,updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS source_product_documents(
            id INTEGER PRIMARY KEY AUTOINCREMENT,sku TEXT NOT NULL,document_type TEXT NOT NULL,
            source_url TEXT NOT NULL,source_title TEXT,source_language TEXT NOT NULL DEFAULT 'pl-PL',
            source_sha256 TEXT,localization_status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,discovered_at TEXT NOT NULL,updated_at TEXT NOT NULL,
            UNIQUE(sku,source_url)
        )"""
    )


def record_product_localization(
    connection: sqlite3.Connection, *, sku: str, title: str,
    description: str, specifications: dict[str, Any], now: str,
) -> None:
    localized = json.dumps(
        {"title": title, "description": description, "specifications": specifications},
        ensure_ascii=False, sort_keys=True,
    )
    signals = polish_score(localized)
    status = "passed" if description.strip() and not signals else "failed"
    connection.execute(
        """INSERT INTO product_content_localization(
           sku,profile_key,profile_version,language,status,polish_signal_count,
           localized_hash,error,updated_at) VALUES(?,?,?,'nl-NL',?,?,?,?,?)
           ON CONFLICT(sku) DO UPDATE SET profile_key=excluded.profile_key,
           profile_version=excluded.profile_version,language=excluded.language,
           status=excluded.status,polish_signal_count=excluded.polish_signal_count,
           localized_hash=excluded.localized_hash,error=excluded.error,
           updated_at=excluded.updated_at""",
        (
            sku, PROFILE_KEY, PROFILE_VERSION, status, signals,
            hashlib.sha256(localized.encode()).hexdigest(),
            None if status == "passed" else "Nederlandse taalcontrole afgekeurd",
            now,
        ),
    )


def _document_images(
    source_bytes: bytes, *, unique_across_document: bool = True,
) -> list[dict[str, str | int]]:
    """Extract unique source-PDF visuals as self-contained HTML data URLs."""
    images: list[dict[str, str | int]] = []
    seen: set[str] = set()
    for page_number, page in enumerate(PdfReader(BytesIO(source_bytes)).pages, 1):
        for source_image in page.images:
            payload = source_image.data
            digest = hashlib.sha256(payload).hexdigest()
            if unique_across_document and digest in seen:
                continue
            seen.add(digest)
            suffix = Path(source_image.name or "image.png").suffix.casefold()
            mime_type = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp",
            }.get(suffix)
            if not mime_type:
                continue
            images.append({
                "page": page_number,
                "name": source_image.name or f"afbeelding-{len(images) + 1}",
                "data_url": (
                    f"data:{mime_type};base64,"
                    + base64.b64encode(payload).decode("ascii")
                ),
            })
    return images


def weldingshop_document_html(
    *, title: str, source_url: str, source_revision: str,
    sections: list[dict[str, Any]], images: list[dict[str, str | int]] | None = None,
) -> str:
    page_aware = any(int(item.get("source_page") or 0) > 0 for item in sections)
    pages: dict[int, list[dict[str, Any]]] = {}
    for section in sections:
        page = int(section.get("source_page") or (1 if page_aware else 0))
        pages.setdefault(page, []).append(section)
    images_by_page: dict[int, list[dict[str, str | int]]] = {}
    for item in images or []:
        images_by_page.setdefault(int(item.get("page") or 1), []).append(item)
    if page_aware:
        for page_number in images_by_page:
            pages.setdefault(page_number, [])

    body: list[str] = []
    for page_number, page_sections in pages.items():
        page_body: list[str] = []
        page_images = images_by_page.pop(page_number, []) if page_aware else []
        if page_images:
            page_body.append("<div class='document-images'>" + "".join(
                "<figure><img src='" + str(item["data_url"]) + "' alt='Illustratie uit de "
                "officiële bron, pagina " + str(page_number) + "'><figcaption>Officiële "
                "illustratie · bronpagina " + str(page_number) + "</figcaption></figure>"
                for item in page_images
            ) + "</div>")
        for section in page_sections:
            heading = html.escape(str(section.get("heading") or "Informatie"))
            content = html.escape(str(section.get("content") or "")).replace("\n", "<br>")
            page_body.append(f"<section><h2>{heading}</h2><p>{content}</p></section>")
        css_class = "source-page" if page_aware else "document-body"
        page_attr = f" data-source-page='{page_number}'" if page_number else ""
        body.append(f"<article class='{css_class}'{page_attr}>" + "".join(page_body) + "</article>")

    remaining_images = [item for group in images_by_page.values() for item in group]
    image_gallery = "".join(
        "<figure><img src='" + str(item["data_url"]) + "' alt='Illustratie uit de "
        "officiële bron, pagina " + str(item["page"]) + "'><figcaption>Officiële "
        "illustratie · bronpagina " + str(item["page"]) + "</figcaption></figure>"
        for item in remaining_images
    )
    return """<!doctype html><html lang='nl-NL'><head><meta charset='utf-8'>
    <style>body{font:15px Arial,sans-serif;color:#172033;max-width:900px;margin:40px auto;
    line-height:1.55}header{border-bottom:5px solid #ed1c24;margin-bottom:28px}h1,h2{color:#172033}
    h2{border-left:4px solid #ed1c24;padding-left:10px}.source-page{break-before:page;
    page-break-before:always}.source-page:first-of-type{break-before:auto;page-break-before:auto}
    footer{margin-top:35px;border-top:1px solid #ccd3dc;
    padding-top:12px;color:#52606d;font-size:12px}.document-images{display:grid;
    grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.document-images figure{margin:0;
    padding:10px;border:1px solid #d9dee7;break-inside:avoid}.document-images img{display:block;
    max-width:100%;max-height:420px;margin:auto;object-fit:contain}.document-images figcaption{
    margin-top:6px;color:#52606d;font-size:11px}@media print{.document-images{display:block}
    .document-images figure{margin:0 0 16px}}</style></head><body><header><strong>Weldingshop.nl</strong>
    <h1>""" + html.escape(title) + """</h1><p>Nederlandse Weldingshop-uitgave</p></header>""" + "".join(body) + (
        ("<section class='unmatched-document-images'><h2>Overige afbeeldingen en schema’s uit de officiële uitgave</h2>"
         "<div class='document-images'>" + image_gallery + "</div></section>")
        if image_gallery else ""
    ) + (
        "<footer>Tecweld/Sherman is de fabrikant. Weldingshop.nl is de Nederlandse uitgever. "
        f"Bronrevisie: {html.escape(source_revision or 'onbekend')}. "
        f"Officiële bron: {html.escape(source_url)}</footer></body></html>"
    )


def save_localized_html_document(
    connection: sqlite3.Connection, *, sku: str, document_type: str, title: str,
    source_url: str, source_revision: str, sections: list[dict[str, str]],
    output_root: Path, now: str,
    images: list[dict[str, str | int]] | None = None,
) -> dict[str, Any]:
    rendered = weldingshop_document_html(
        title=title, source_url=source_url, source_revision=source_revision,
        sections=sections, images=images,
    )
    signals = polish_score("\n".join(
        str(item.get("heading") or "") + " " + str(item.get("content") or "")
        for item in sections
    ))
    if signals:
        raise ValueError(f"Documenttaal afgekeurd: {signals} Poolse tekstsignalen")
    folder = output_root / sku
    folder.mkdir(parents=True, exist_ok=True)
    safe_type = re.sub(r"[^a-z0-9-]+", "-", document_type.casefold()).strip("-")
    path = folder / f"{sku}-{safe_type}-NL-Weldingshop.html"
    path.write_text(rendered, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    connection.execute(
        """INSERT INTO product_documents(
           sku,document_type,title,language,local_path,filename,mime_type,size_bytes,
           sha256,source_url,source_revision,translation_status,updated_at,
           profile_key,profile_version,quality_status)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'passed')
           ON CONFLICT(sku,document_type,language) DO UPDATE SET title=excluded.title,
           local_path=excluded.local_path,filename=excluded.filename,mime_type=excluded.mime_type,
           size_bytes=excluded.size_bytes,
           shopify_file_id=CASE WHEN product_documents.sha256<>excluded.sha256
               THEN NULL ELSE product_documents.shopify_file_id END,
           shopify_cdn_url=CASE WHEN product_documents.sha256<>excluded.sha256
               THEN NULL ELSE product_documents.shopify_cdn_url END,
           sha256=excluded.sha256,source_url=excluded.source_url,
           source_revision=excluded.source_revision,translation_status=excluded.translation_status,
           updated_at=excluded.updated_at,profile_key=excluded.profile_key,
           profile_version=excluded.profile_version,quality_status='passed'""",
        (
            sku, document_type, title, "nl-NL", str(path), path.name, "text/html",
            path.stat().st_size, digest, source_url, source_revision,
            "Nederlandse Weldingshop-uitgave", now, PROFILE_KEY,
        ),
    )
    return {"path": str(path), "sha256": digest, "quality_status": "passed"}


def localize_pdf_document(
    connection: sqlite3.Connection, *, provider: Any, model: str, sku: str,
    document_type: str, source_url: str, source_title: str, output_root: Path,
    now: str,
) -> dict[str, Any]:
    response = requests.get(
        source_url, headers={"User-Agent": "Weldingshop-PIM/1.0 (+NL localization)"},
        timeout=120,
    )
    response.raise_for_status()
    source_bytes = response.content
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    source_root = output_root / "source" / sku
    source_root.mkdir(parents=True, exist_ok=True)
    source_path = source_root / f"{source_hash[:16]}.pdf"
    source_path.write_bytes(source_bytes)
    pages = [str(page.extract_text() or "").strip() for page in PdfReader(BytesIO(source_bytes)).pages]
    source_text = "\n\n".join(text for text in pages if text)
    if len(source_text) < 80:
        raise ValueError("Bron-PDF bevat onvoldoende doorzoekbare tekst voor volledige vertaling")
    sections: list[dict[str, Any]] = []
    translated_pages = [(number, text) for number, text in enumerate(pages, 1) if text]
    for page_number, page_text in translated_pages:
        prompt = f"""Vertaal bronpagina {page_number} van {len(pages)} van een officiële Tecweld/Sherman-
{document_type} volledig naar duidelijk technisch Nederlands in Weldingshop-stijl.
Dit is een volledige vertaling, geen samenvatting. Laat veiligheidswaarschuwingen, stappen,
bedieningselementen, storingen, getallen, eenheden, modellen, normen en volgorde volledig staan.
Behoud Tecweld/Sherman als fabrikant. Geef JSON met sections: een array van objecten met heading
en content. Houd alle tekst van deze pagina bijeen; neem geen tekst van andere pagina's aan.
Geen Poolse klanttekst.\n\nBRONPAGINA {page_number}:\n{page_text}"""
        translated = provider.client.with_options(timeout=180.0, max_retries=0).responses.create(
            model=model, input=prompt, text={"format": {"type": "json_object"}},
        )
        data = json.loads(translated.output_text)
        page_sections = data.get("sections") or []
        page_target = "\n".join(
            f"{item.get('heading', '')}\n{item.get('content', '')}"
            for item in page_sections if isinstance(item, dict)
        )
        for _repair_attempt in range(1):
            signals = polish_score(page_target)
            if not signals:
                break
            repair = provider.client.with_options(timeout=180.0, max_retries=0).responses.create(
                model=model,
                input=(
                    f"Herstel de vertaling van bronpagina {page_number} van deze Tecweld/"
                    f"Sherman-{document_type}. De vorige uitvoer bevat nog {signals} Poolse "
                    "taalsignalen. Vertaal werkelijk ieder Pools woord naar natuurlijk technisch "
                    "Nederlands, ook koppen, waarschuwingen en bijschriften, zonder samen te "
                    "vatten. Behoud alle getallen, eenheden, modellen, normen en volgorde. "
                    "Geef uitsluitend JSON met sections (heading en content).\n\n"
                    f"BRON:\n{page_text}\n\nVORIGE UITVOER:\n{page_target}"
                ),
                text={"format": {"type": "json_object"}},
            )
            page_sections = json.loads(repair.output_text).get("sections") or []
            page_target = "\n".join(
                f"{item.get('heading', '')}\n{item.get('content', '')}"
                for item in page_sections if isinstance(item, dict)
            )
        if polish_score(page_target) and model == "gpt-5.6-terra":
            # Sol is uitsluitend een kwaliteitsfallback nadat Terra plus één
            # begrensde Terra-herstelpoging aantoonbaar is afgekeurd.
            signals = polish_score(page_target)
            repair = provider.client.with_options(
                timeout=240.0, max_retries=0
            ).responses.create(
                model="gpt-5.6-sol",
                input=(
                    f"Vertaal bronpagina {page_number} van deze Tecweld/Sherman-"
                    f"{document_type} volledig naar technisch Nederlands. Terra is "
                    f"afgekeurd met {signals} Poolse taalsignalen. Vertaal elk Pools "
                    "woord, zonder samenvatten, en behoud alle getallen, eenheden, "
                    "modellen, normen en volgorde. Geef uitsluitend JSON met sections "
                    f"(heading en content).\n\nBRON:\n{page_text}"
                ),
                text={"format": {"type": "json_object"}},
            )
            page_sections = json.loads(repair.output_text).get("sections") or []
            page_target = "\n".join(
                f"{item.get('heading', '')}\n{item.get('content', '')}"
                for item in page_sections if isinstance(item, dict)
            )
        if polish_score(page_target):
            residual = sorted(set(POLISH_MARKERS.findall(page_target)))
            raise ValueError(
                f"Nederlandse documentcontrole afgekeurd op bronpagina {page_number}: "
                f"{polish_score(page_target)} Poolse tekstsignalen ({', '.join(residual[:8])})"
            )
        for section in page_sections:
            if isinstance(section, dict):
                section["source_page"] = page_number
                sections.append(section)
    target_text = "\n".join(
        f"{item.get('heading','')}\n{item.get('content','')}" for item in sections
    )
    if polish_score(target_text):
        raise ValueError("Nederlandse documentcontrole afgekeurd: Poolse tekst resteert")
    source_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", source_text))
    target_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", target_text))
    missing_numbers = source_numbers - target_numbers
    if source_numbers and len(missing_numbers) / len(source_numbers) > 0.05:
        # Eén begrensde herstelpoging voor regels rond ontbrekende waarden.
        # Zo wordt een vrijwel complete handleiding niet weggegooid, terwijl
        # iedere technische waarde wel aantoonbaar in de NL-uitgave terugkomt.
        contexts: list[tuple[int, str]] = []
        for value in sorted(missing_numbers):
            source_page = next(
                (number for number, page_text in enumerate(pages, 1)
                 if re.search(rf"\b{re.escape(value)}\b", page_text)),
                1,
            )
            page_text = pages[source_page - 1]
            match = re.search(
                rf"[^\n]{{0,180}}\b{re.escape(value)}\b[^\n]{{0,180}}",
                page_text,
            )
            contexts.append((source_page, match.group(0).strip() if match else value))
        repair = provider.client.with_options(
            timeout=180.0, max_retries=0
        ).responses.create(
            model=model,
            input=(
                "Vertaal onderstaande ontbrekende technische bronregels volledig naar "
                "Nederlands. Behoud elk getal en iedere eenheid exact. Geef JSON met "
                "sections (heading, content en source_page). Neem het nummer na "
                "BRONPAGINA exact over als source_page. Voeg niets toe.\n\n"
                + "\n".join(
                    f"BRONPAGINA {page_number}: {context}"
                    for page_number, context in contexts
                )
            ),
            text={"format": {"type": "json_object"}},
        )
        repaired_sections = json.loads(repair.output_text).get("sections") or []
        for section in repaired_sections:
            if isinstance(section, dict):
                page_number = int(section.get("source_page") or 0)
                section["source_page"] = (
                    page_number if 1 <= page_number <= len(pages) else contexts[0][0]
                )
                sections.append(section)
        target_text = "\n".join(
            f"{item.get('heading','')}\n{item.get('content','')}" for item in sections
        )
        remaining = source_numbers - set(
            re.findall(r"\b\d+(?:[.,]\d+)?\b", target_text)
        )
        if remaining:
            raise ValueError(
                f"Documentvolledigheid afgekeurd: {len(remaining)} technische waarden ontbreken"
            )
    clean_source_title = re.sub(
        r"(?:[-_\s]+(?:instrukcja(?:[-_\s]+obsługi)?|manual))+$",
        "", source_title.strip(), flags=re.IGNORECASE,
    ).strip(" -_") or source_title.strip()
    title = (
        f"{clean_source_title} – Nederlandse gebruikershandleiding"
        if document_type == "handleiding" else
        f"{clean_source_title} – Nederlandse {document_type}"
    )
    result = save_localized_html_document(
        connection, sku=sku, document_type=document_type, title=title,
        source_url=source_url, source_revision=source_hash[:16], sections=sections,
        output_root=output_root / "localized", now=now,
        images=_document_images(
            source_bytes,
            # In a manual, a repeated safety symbol or connection diagram is
            # meaningful on every source page where it occurs. Product-folder
            # PDFs often expose the same full image resource set on all pages,
            # so those retain document-wide deduplication.
            unique_across_document=document_type != "handleiding",
        ),
    )
    connection.execute(
        """UPDATE source_product_documents SET source_sha256=?,
           localization_status='passed',error=NULL,updated_at=?
           WHERE sku=? AND source_url=?""",
        (source_hash, now, sku, source_url),
    )
    return {**result, "source_path": str(source_path), "sections": len(sections)}
