from __future__ import annotations

import html
import json
import os
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.ai.providers.openai_provider import OpenAIProvider

from .service import ProductMakerService, normalized_host


USER_AGENT = "Weldingshop-PIM-ProductMaker/1.0 (+verified product research)"


def _identifier_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def _official_url(value: str, domains: list[str]) -> str:
    url = str(value or "").strip()
    if not url.startswith("https://"):
        return ""
    host = normalized_host(url)
    if not any(host == domain or host.endswith("." + domain) for domain in domains):
        return ""
    return url.split("#", 1)[0]


def discover_official_page(draft: dict[str, Any]) -> dict[str, Any]:
    domains = draft.get("approved_domains") or []
    if not domains:
        raise ValueError("Koppel eerst minimaal één goedgekeurd leveranciersdomein")
    identifiers = [draft.get("ean"), draft.get("manufacturer_number"), draft.get("sku")]
    identifiers = [str(value).strip() for value in identifiers if str(value or "").strip()]
    prompt = f"""Zoek uitsluitend op de officiële domeinen {domains} naar precies dit product.
Merk/leverancier: {draft['vendor']}
Identificaties (sterkste eerst): {identifiers}
Titelhint: {draft.get('title') or ''}
Geef alleen JSON: {{"candidates":[{{"url":"https://...","title":"...","matched_identifier":"...","reason":"..."}}]}}.
Een kandidaat is alleen geldig wanneer een volledige identificatie letterlijk op de pagina staat.
Gebruik geen webshops van derden, advertenties, vergelijkingssites of afgeleide technische feiten."""
    provider = OpenAIProvider()
    response = provider.client.with_options(timeout=90.0, max_retries=0).responses.create(
        model=os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-5.6-terra"),
        tools=[{"type": "web_search", "filters": {"allowed_domains": domains}}],
        input=prompt,
    )
    output = response.output_text.strip()
    if output.startswith("```"):
        output = output.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    data = json.loads(output)
    candidates = []
    for item in data.get("candidates") or []:
        url = _official_url(item.get("url"), domains)
        matched = str(item.get("matched_identifier") or "").strip()
        if url and _identifier_key(matched) in {_identifier_key(value) for value in identifiers}:
            candidates.append({**item, "url": url, "matched_identifier": matched})
    return {"candidates": candidates, "domains": domains}


def _json_ld_products(soup: BeautifulSoup) -> list[dict[str, Any]]:
    products = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text(" ", strip=True))
        except Exception:
            continue
        queue = payload if isinstance(payload, list) else [payload]
        while queue:
            item = queue.pop(0)
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("@graph"), list):
                queue.extend(item["@graph"])
            kinds = item.get("@type") or ""
            if "Product" in ([kinds] if isinstance(kinds, str) else kinds):
                products.append(item)
    return products


def _html_product_facts(soup: BeautifulSoup) -> dict[str, str]:
    """Read product facts from OpenGraph and schema.org microdata markup."""
    def text(selector: str) -> str:
        node = soup.select_one(selector)
        return node.get_text(" ", strip=True) if node else ""

    def content(selector: str) -> str:
        node = soup.select_one(selector)
        return str(node.get("content") or "").strip() if node else ""

    description_node = soup.select_one(".full-description[itemprop='description']")
    description_html = (
        str(description_node.decode_contents()).strip()
        if description_node else ""
    )
    description_text = (
        text(".short-description")
        or content("meta[property='og:description']")
        or content("meta[name='description']")
    )
    if not description_html and description_text:
        description_html = f"<p>{html.escape(description_text)}</p>"
    return {
        "sku": text("[itemprop='sku']"),
        "ean": text("[itemprop='gtin13'],[itemprop='gtin'],[itemprop='gtin14']"),
        "manufacturer_number": text("[itemprop='mpn']"),
        "vendor": text(".manufacturers .value") or text("[itemprop='brand']"),
        "title": (
            text("h1[itemprop='name']")
            or content("meta[property='og:title']")
        ),
        "description_html": description_html,
    }


def probe_product_page(
    source_url: str, identifier: str = "", identifier_kind: str = "SKU",
) -> dict[str, Any]:
    """Read the identity from one explicit HTTPS product page."""
    domain = normalized_host(source_url)
    url = _official_url(source_url, [domain] if domain else [])
    if not url:
        raise ValueError("Gebruik een geldige openbare HTTPS-productpagina")
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    if "text/html" not in response.headers.get("Content-Type", "text/html"):
        raise ValueError("De bron is geen HTML-productpagina")
    soup = BeautifulSoup(response.text, "html.parser")
    page_text = " ".join(soup.stripped_strings)
    entered_identifier = str(identifier or "").strip()
    if entered_identifier and (
        len(_identifier_key(entered_identifier)) < 4
        or _identifier_key(entered_identifier) not in _identifier_key(page_text)
    ):
        raise ValueError(
            f"De ingevulde {identifier_kind} staat niet volledig op de productpagina"
        )
    products = _json_ld_products(soup)
    product = products[0] if products else {}
    html_facts = _html_product_facts(soup)
    brand = product.get("brand") or ""
    if isinstance(brand, dict):
        brand = brand.get("name") or ""
    structured_sku = str(
        product.get("sku") or product.get("mpn") or html_facts["sku"]
        or html_facts["manufacturer_number"] or ""
    ).strip()
    structured_ean = str(
        product.get("gtin13") or product.get("gtin")
        or product.get("gtin14") or ""
        or html_facts["ean"]
    ).strip()
    sku = structured_sku or (
        entered_identifier if identifier_kind == "SKU" else structured_ean
    )
    if not sku and identifier_kind == "EAN":
        # Shopify vereist een SKU. Bij een incidenteel EAN-product zonder
        # gestructureerde SKU is de bewezen EAN een veilige unieke fallback.
        sku = entered_identifier
    if not sku:
        raise ValueError("De productpagina bevat geen bruikbare SKU")
    page_title = (soup.title.get_text(" ", strip=True) if soup.title else "")
    return {
        "url": url,
        "domain": domain,
        "sku": sku,
        "ean": structured_ean or (
            entered_identifier if identifier_kind == "EAN" else ""
        ),
        "manufacturer_number": str(
            product.get("mpn") or html_facts["manufacturer_number"] or ""
        ).strip(),
        "vendor": str(brand or html_facts["vendor"] or domain.split(".")[0]).strip(),
        "title": str(product.get("name") or html_facts["title"] or page_title or sku).strip(),
        "description_html": str(
            product.get("description") or html_facts["description_html"] or ""
        ).strip(),
    }


def inspect_official_page(
    service: ProductMakerService, draft_id: int, source_url: str, *,
    verified_domain: str = "",
) -> dict[str, Any]:
    draft = service.get_draft(draft_id)
    domains = list(draft.get("approved_domains") or [])
    # The automatic-start probe has already fetched the page and proved the
    # exact entered SKU/EAN. Permit precisely that returned HTTPS host for this
    # inspection without broadening the supplier's permanent domain allowlist.
    verified_host = normalized_host(verified_domain)
    source_host = normalized_host(source_url)
    if verified_host and source_host == verified_host and verified_host not in domains:
        domains.append(verified_host)
    url = _official_url(source_url, domains)
    if not url:
        raise ValueError("De URL staat niet op een goedgekeurd HTTPS-domein")
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    if "text/html" not in response.headers.get("Content-Type", "text/html"):
        raise ValueError("De bron is geen HTML-productpagina")
    soup = BeautifulSoup(response.text, "html.parser")
    page_text = " ".join(soup.stripped_strings)
    compact_page = _identifier_key(page_text)
    identifiers = {
        "ean": str(draft.get("ean") or ""),
        "manufacturer_number": str(draft.get("manufacturer_number") or ""),
        "sku": str(draft.get("sku") or ""),
    }
    matched_by = next(
        (key for key, value in identifiers.items()
         if value and len(_identifier_key(value)) >= 4 and _identifier_key(value) in compact_page),
        "",
    )
    if not matched_by:
        raise ValueError("Geen volledige SKU, EAN of fabrikantnummer letterlijk op de officiële pagina gevonden")
    matched_value = identifiers[matched_by]
    page_title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:300]
    excerpt_match = re.search(
        rf".{{0,180}}{re.escape(matched_value)}.{{0,180}}", page_text, re.IGNORECASE,
    )
    excerpt = excerpt_match.group(0) if excerpt_match else matched_value
    service.add_evidence(
        draft_id, "source_url", url, state="proven", source_url=url,
        source_title=page_title, source_excerpt=excerpt, matched_by=matched_by,
        confidence=1, approved=False,
    )
    products = _json_ld_products(soup)
    product = products[0] if products else {}
    html_facts = _html_product_facts(soup)
    facts: dict[str, Any] = {}
    mappings = {
        "title": product.get("name") or html_facts["title"],
        "description_html": product.get("description") or html_facts["description_html"],
        "vendor": ((product.get("brand") or {}).get("name")
                   if isinstance(product.get("brand"), dict) else product.get("brand"))
                  or html_facts["vendor"],
        "ean": product.get("gtin13") or product.get("gtin") or product.get("gtin14")
               or html_facts["ean"],
        "manufacturer_number": product.get("mpn") or html_facts["manufacturer_number"],
        "sku": product.get("sku") or html_facts["sku"],
    }
    for field, value in mappings.items():
        if value not in (None, ""):
            facts[field] = str(value).strip()
            service.add_evidence(
                draft_id, field, facts[field], state="proven", source_url=url,
                source_title=page_title, source_excerpt=excerpt, matched_by=matched_by,
                confidence=1, approved=False,
            )
    image_values = product.get("image") or []
    if isinstance(image_values, str):
        image_values = [image_values]
    if isinstance(image_values, dict):
        image_values = [image_values.get("url") or image_values.get("contentUrl")]
    image_values.extend(
        tag.get("content") for tag in soup.select('meta[property="og:image"][content]')
    )
    images = []
    for value in image_values:
        image_url = _official_url(urljoin(url, str(value or "")), domains)
        if image_url and image_url not in images:
            images.append(image_url)
            service.add_asset(
                draft_id, "image", image_url, title=page_title, source_url=url,
                official=True, identifier_verified=True,
            )
    documents = []
    for link in soup.select("a[href]"):
        href = _official_url(urljoin(url, link.get("href") or ""), domains)
        label = link.get_text(" ", strip=True)
        probe = f"{href} {label}".casefold()
        if not href or not (href.casefold().split("?", 1)[0].endswith(".pdf")):
            continue
        kind = "safety" if any(word in probe for word in ("safety", "veiligheid", "sds", "msds")) else (
            "manual" if any(word in probe for word in ("manual", "handleiding", "instruction")) else "datasheet"
        )
        service.add_asset(
            draft_id, kind, href, title=label, source_url=url,
            official=True, identifier_verified=True,
        )
        documents.append({"url": href, "title": label, "kind": kind})
    return {"url": url, "matched_by": matched_by, "matched_value": matched_value,
            "title": page_title, "facts": facts, "images": images, "documents": documents,
            "page_text": page_text[:30000]}


def enrich_from_evidence(service: ProductMakerService, draft_id: int) -> dict[str, Any]:
    draft = service.get_draft(draft_id)
    approved_or_proven = [
        item for item in draft["evidence"] if item["state"] == "proven"
    ]
    if not approved_or_proven:
        raise ValueError("Onderzoek en controleer eerst minimaal één officiële bron")
    facts = [
        {"field": item["field_name"], "value": item["value"],
         "source_url": item["source_url"], "excerpt": item["source_excerpt"]}
        for item in approved_or_proven
    ]
    prompt = f"""Je bent een Nederlandse technische productredacteur.
Gebruik uitsluitend onderstaande bewijsregels. Voeg geen technische feiten, keurmerken,
materialen, maten, toepassingen of claims toe die niet letterlijk bewezen zijn.
Je mag taal verbeteren, structureren en vertalen. Onbekende gegevens laat je weg.
SKU: {draft['sku']} Merk: {draft['vendor']}
BEWIJS: {json.dumps(facts, ensure_ascii=False)}
Geef uitsluitend JSON met title, short_description, description_html, seo_title,
seo_description, product_type, tags (array), properties (object), warnings (array).
description_html gebruikt alleen p, h3, ul, li en strong. Zet bewezen technische
eigenschappen onder een eigen kop. Noem bronnen niet in commerciële tekst."""
    provider = OpenAIProvider()
    response = provider.client.with_options(timeout=90.0, max_retries=0).responses.create(
        model=os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-5.6-terra"), input=prompt,
    )
    output = response.output_text.strip()
    if output.startswith("```"):
        output = output.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    data = json.loads(output)
    allowed = {"title", "short_description", "description_html", "seo_title",
               "seo_description", "product_type", "tags", "properties", "warnings"}
    data = {key: value for key, value in data.items() if key in allowed}
    for field in ("title", "short_description", "description_html", "seo_title",
                  "seo_description", "product_type", "tags"):
        if data.get(field):
            service.add_evidence(
                draft_id, field, data[field], state="proposed", source_url="",
                source_title="AI-voorstel op goedgekeurde bewijsregels",
                source_excerpt="; ".join(str(item["value"]) for item in facts)[:1200],
                matched_by="evidence_only", confidence=.7, approved=False,
            )
    return data
