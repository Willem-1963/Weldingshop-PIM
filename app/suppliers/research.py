from __future__ import annotations

import csv
import io
import ipaddress
import json
import socket
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any
from urllib.parse import parse_qsl, urlparse

import requests


MAX_ANALYSIS_BYTES = 12 * 1024 * 1024
FIELD_ALIASES = {
    "sku": ["sku", "reference", "model", "articleNumber", "productCode", "code"],
    "ean": ["ean", "barcode", "gtin"],
    "title": ["titleNL", "title", "nameNL", "name", "descriptionShortNL"],
    "description": ["descriptionNL", "description", "longDescriptionNL"],
    "price": ["priceINVAT", "sale_price", "price", "consumerPrice"],
    "sale_price": ["specialPrice", "offerPrice", "salePrice"],
    "stock": ["stock", "inStock", "available", "availability", "quantity"],
    "weight": ["weight", "weightGrams"],
    "product_type": ["productGroup", "articleGroup", "type"],
    "category": ["category", "categoryNL", "group"],
    "updated_at": ["date_upd", "updatedAt", "modified"],
}


def _validate_public_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Gebruik een volledige http- of https-URL.")
    for result in socket.getaddrinfo(parsed.hostname, parsed.port or 443):
        address = ipaddress.ip_address(result[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            raise ValueError("Lokale of afgeschermde netwerkadressen zijn niet toegestaan.")
    return value.strip()


def _suggest_mapping(fields: list[str]) -> dict[str, str]:
    by_folded = {field.casefold(): field for field in fields}
    result = {}
    for target, aliases in FIELD_ALIASES.items():
        result[target] = next(
            (
                by_folded[alias.casefold()]
                for alias in aliases
                if alias.casefold() in by_folded
            ),
            "",
        )
    return result


def _xml_samples(response: requests.Response) -> tuple[list[dict[str, str]], int]:
    parser = ET.XMLPullParser(events=("end",))
    samples: list[dict[str, str]] = []
    bytes_seen = 0
    tag_counts: Counter[str] = Counter()
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        bytes_seen += len(chunk)
        if bytes_seen > MAX_ANALYSIS_BYTES:
            break
        parser.feed(chunk)
        for _, node in parser.read_events():
            children = list(node)
            if children:
                tag_counts[node.tag] += 1
            if node.tag.lower().endswith("product") and children:
                record = dict(node.attrib)
                record.update({
                    child.tag: (child.text or "").strip()
                    for child in children
                })
                samples.append(record)
                node.clear()
                if len(samples) >= 10:
                    return samples, bytes_seen
    if samples:
        return samples, bytes_seen
    raise ValueError(
        "XML herkend, maar geen complete <product>-records gevonden in het analysevenster."
    )


def research_supplier_source(url: str) -> dict[str, Any]:
    checked_url = _validate_public_url(url)
    response = requests.get(
        checked_url,
        headers={
            "User-Agent": "Weldingshop-PIM-SourceResearch/1.0",
            "Accept": "application/xml,text/xml,application/json,text/csv,*/*",
        },
        stream=True,
        timeout=(10, 60),
        allow_redirects=True,
    )
    final_url = _validate_public_url(response.url)
    auth_header = response.headers.get("WWW-Authenticate", "")
    if response.status_code in {401, 403}:
        method = (
            "basic" if auth_header.lower().startswith("basic")
            else "bearer" if "bearer" in auth_header.lower()
            else "onbekend"
        )
        return {
            "ok": False,
            "http_status": response.status_code,
            "auth_type": method,
            "message": "De bron vraagt om authenticatie.",
            "www_authenticate": auth_header,
            "suggested_mapping": {},
            "warnings": [],
        }
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    prefix = next(response.iter_content(4096), b"")
    response.close()

    # Opnieuw openen zodat de streaming XML-parser ook de eerste bytes ontvangt.
    response = requests.get(
        final_url,
        headers={"User-Agent": "Weldingshop-PIM-SourceResearch/1.0"},
        stream=True,
        timeout=(10, 60),
    )
    response.raise_for_status()
    stripped = prefix.lstrip()
    if stripped.startswith(b"<") or "xml" in content_type:
        source_format = "XML"
        samples, bytes_analyzed = _xml_samples(response)
    else:
        raw = response.content[:MAX_ANALYSIS_BYTES]
        bytes_analyzed = len(raw)
        if stripped.startswith((b"{", b"[")) or "json" in content_type:
            source_format = "JSON"
            payload = json.loads(raw.decode("utf-8-sig"))
            if isinstance(payload, dict):
                payload = next(
                    (
                        payload[key] for key in ("products", "items", "data", "results")
                        if isinstance(payload.get(key), list)
                    ),
                    [payload],
                )
            samples = [dict(row) for row in payload[:10]]
        else:
            source_format = "CSV"
            samples = list(csv.DictReader(io.StringIO(
                raw.decode("utf-8-sig", errors="replace")
            )))[:10]
    fields = sorted({str(key) for row in samples for key in row})
    parsed = urlparse(final_url)
    query_keys = [key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
    warnings = []
    sensitive_keys = {
        "token", "key", "apikey", "api_key", "secret", "password", "customer", "name"
    }
    exposed = [key for key in query_keys if key.casefold() in sensitive_keys]
    if exposed:
        warnings.append(
            "De URL bevat klant- of toegangsparameters: "
            + ", ".join(exposed)
            + ". Behandel de volledige URL als vertrouwelijk."
        )
    mapping = _suggest_mapping(fields)
    notes = []
    if "priceINVAT" in fields and "priceEXVAT" in fields:
        notes.append(
            "priceINVAT is inclusief btw en voorgesteld als Shopify-verkoopprijs; "
            "priceEXVAT kan als aanvullende zakelijke/inkoopreferentie dienen."
        )
    return {
        "ok": True,
        "http_status": response.status_code,
        "final_url": final_url,
        "format": source_format,
        "content_type": content_type,
        "auth_type": "url_parameters" if query_keys else "none",
        "authentication_explanation": (
            "De server accepteert de URL zonder HTTP-login. Toegang lijkt via de "
            "queryparameters in de link te worden bepaald."
            if query_keys else
            "De server accepteert de bron zonder zichtbare authenticatie."
        ),
        "fields": fields,
        "sample_count": len(samples),
        "sample": samples[:3],
        "suggested_mapping": mapping,
        "query_parameter_names": query_keys,
        "warnings": warnings,
        "notes": notes,
        "bytes_analyzed": bytes_analyzed,
    }
