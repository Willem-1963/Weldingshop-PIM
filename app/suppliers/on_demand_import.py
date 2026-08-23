from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from app.ai.providers.openai_provider import OpenAIProvider
from app.suppliers.hub import (
    _connect,
    get_supplier,
    init_supplier_database,
    utc_now,
)
from app.suppliers.routes import supplier_route
from app.suppliers.enrichment_profiles import get_enrichment_profile
from app.suppliers.dutch_content import (
    assert_dutch_product_content,
    init_content_localization_tables,
    localize_pdf_document,
    polish_score,
    record_product_localization,
    repair_product_dutch,
    weldingshop_product_html,
)


TECWELD_CONTENT_ROOT = Path("/srv/weldingshop-pim/data/content/tecweld")


def _official_hosts(supplier: dict[str, Any]) -> set[str]:
    hosts: set[str] = set()
    for key in ("website_url", "catalogue_url", "dealer_portal_url"):
        host = (urlparse(str(supplier.get(key) or "")).hostname or "").casefold()
        if host:
            # De kale host, www-host en eigen media-subdomeinen zijn dezelfde
            # leverancierssite. Verwijder uitsluitend de conventionele www-prefix;
            # maak de grens niet ruimer naar een ander hoofddomein.
            hosts.add(host.removeprefix("www."))
    return hosts


def _official_url(value: Any, hosts: set[str]) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not host:
        return ""
    if not any(host.removeprefix("www.") == allowed or host.endswith("." + allowed) for allowed in hosts):
        return ""
    return url


def _description_html(title: str, description: str, specifications: Any) -> str:
    sections = [f"<h2>{html.escape(title)}</h2>"]
    if description.strip():
        sections.append(f"<p>{html.escape(description.strip())}</p>")
    if isinstance(specifications, dict) and specifications:
        rows = "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in specifications.items()
            if value not in (None, "", [], {})
        )
        if rows:
            sections.append(f"<h3>Technische specificaties</h3><table><tbody>{rows}</tbody></table>")
    return "".join(sections)


def _usable_source_value(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in {"nan", "none", "null"} else text


def _deterministic_kentie_data(
    source_url: str, article: str, existing_raw: dict[str, Any],
    fallback_title: str, pim_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build verified Kentie content without asking AI to re-judge the match."""
    page_content = _page_kentie_content(source_url, article)
    title = page_content.get("title") or _page_product_title(source_url) or fallback_title or article
    official_description = _usable_source_value(page_content.get("description"))
    description_parts: list[str] = []
    if official_description:
        description_parts.append(official_description)
    else:
        for key in ("Omschrijving", "Toevoeging 1", "Toevoeging 2"):
            value = _usable_source_value(existing_raw.get(key))
            if value and value not in description_parts:
                description_parts.append(value)
    context = pim_context or {}
    description = ". ".join(part.rstrip(". ") for part in description_parts)
    if not description:
        description = title.rstrip(". ")
    group = _usable_source_value(
        existing_raw.get("Productgroep - naam")
        or context.get("product_group_name") or context.get("product_type")
    )
    category = _usable_source_value(
        existing_raw.get("Hoofdcategorie") or existing_raw.get("Categorie")
        or context.get("category")
    )
    execution = _usable_source_value(existing_raw.get("Uitvoering"))
    if not official_description and not execution:
        execution = _usable_source_value(context.get("execution"))
    facts = []
    if group and group.casefold() not in description.casefold():
        facts.append(f"Productgroep: {group}")
    if category and category.casefold() not in description.casefold():
        facts.append(f"Categorie: {category}")
    if execution and execution.casefold() not in description.casefold():
        facts.append(f"Uitvoering: {execution}")
    if facts and not official_description:
        description = ". ".join([description, *facts])
    if description:
        description += "."
    specifications = {} if official_description else {
        label: value
        for label, key in (
            ("Uitvoering", "Uitvoering"), ("Gas", "Gas"),
            ("Aansluiting", "Aansluiting"), ("Slangmaat", "Slangmaat"),
            ("Type aansluiting", "Type aansluiting"),
            ("Toepassing", "Toepassing"), ("Serie", "Serie"),
            ("Materiaal", "Materiaal"), ("Drukbereik", "Drukbereik"),
        )
        if (value := _usable_source_value(existing_raw.get(key)))
    }
    specifications = {
        "Artikelnummer": article,
        **({"Productgroep": group} if group else {}),
        **({"Categorie": category} if category else {}),
        **({"Uitvoering": execution} if execution else {}),
        **specifications,
    }
    ean = _usable_source_value(existing_raw.get("EAN"))
    if ean.endswith(".0") and ean[:-2].isdigit():
        ean = ean[:-2]
    return {
        "exact_match": True,
        "matched_article_number": article,
        "product_page_url": source_url,
        "title": title,
        "brand": _usable_source_value(existing_raw.get("Merk")) or "Kentie",
        "ean": ean,
        "description": description,
        "product_type": (
            _usable_source_value(existing_raw.get("Producttype"))
            or _usable_source_value(existing_raw.get("Productgroep - naam"))
        ),
        "category": (
            _usable_source_value(existing_raw.get("Hoofdcategorie"))
            or _usable_source_value(existing_raw.get("Categorie"))
        ),
        "technical_specifications": specifications,
        "feature_icons": [], "accessories": [], "image_urls": [],
        "source_summary": (
            f"Artikelnummer {article} is rechtstreeks en letterlijk bevestigd "
            "op de officiële Kentie-productpagina."
        ),
    }


def _page_contains_article(source_url: str, article: str) -> bool:
    """Verify the exact article in the official page independently of the AI answer."""
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "Weldingshop-PIM/1.0 (+product verification)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content_type = str(response.headers.get("Content-Type") or "").casefold()
            if response.status >= 400 or "html" not in content_type:
                return False
            page = response.read(4_000_000).decode(
                response.headers.get_content_charset() or "utf-8", errors="replace"
            )
    except Exception:
        return False
    return article.casefold() in html.unescape(page).casefold()


def _page_product_title(source_url: str) -> str:
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "Weldingshop-PIM/1.0 (+product title)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            soup = BeautifulSoup(response.read(4_000_000), "html.parser")
    except Exception:
        return ""
    title = (
        soup.select_one("h1.product_title")
        or soup.select_one(".product__title h1")
        or soup.select_one(".product-name h1")
        or soup.select_one("h1")
    )
    return title.get_text(" ", strip=True) if title else ""


def _page_kentie_content(source_url: str, article: str) -> dict[str, str]:
    """Read customer-facing content from one exact Kentie product page."""
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "Weldingshop-PIM/1.0 (+Kentie product content)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            soup = BeautifulSoup(response.read(4_000_000), "html.parser")
    except Exception:
        return {}
    sku = soup.select_one('[itemprop="sku"]')
    if not sku or sku.get_text(" ", strip=True).casefold() != article.casefold():
        return {}
    title_node = soup.select_one('.product-name h1, h1[itemprop="name"], h1')
    description_node = soup.select_one(
        '.full-description[itemprop="description"], .full-description, .short-description'
    )
    description = (
        description_node.get_text(" ", strip=True) if description_node else ""
    )
    if not description:
        meta = soup.select_one('meta[name="description"], meta[property="og:description"]')
        description = str(meta.get("content") or "").strip() if meta else ""
    return {
        "title": title_node.get_text(" ", strip=True) if title_node else "",
        "description": description,
    }


def _page_documents(source_url: str, hosts: set[str]) -> list[dict[str, str]]:
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "Weldingshop-PIM/1.0 (+product documents)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            soup = BeautifulSoup(response.read(4_000_000), "html.parser")
    except Exception:
        return []
    found: list[dict[str, str]] = []
    for link in soup.select("a[href]"):
        url = _official_url(urljoin(source_url, link.get("href") or ""), hosts)
        parsed = urlparse(url)
        is_pdf_path = parsed.path.casefold().endswith(".pdf")
        is_product_folder = "print-products=pdf" in parsed.query.casefold()
        if not url or not (is_pdf_path or is_product_folder):
            continue
        label = link.get_text(" ", strip=True) or Path(urlparse(url).path).name
        clue = f"{label} {url}".casefold()
        document_type = (
            "productfolder" if is_product_folder
            else
            "handleiding" if any(word in clue for word in ("instruk", "manual", "obsług"))
            else "brochure" if any(word in clue for word in ("broszur", "katalog", "folder"))
            else "productinformatie"
        )
        item = {"document_type": document_type, "source_url": url, "source_title": label}
        if item not in found:
            found.append(item)
    return found


def _page_tecweld_details(
    source_url: str, hosts: set[str]
) -> dict[str, Any]:
    """Read exact, customer-visible Tecweld assets from one product page."""
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "Weldingshop-PIM/1.0 (+Tecweld details)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            soup = BeautifulSoup(response.read(4_000_000), "html.parser")
    except Exception:
        return {
            "description_paragraphs": [], "feature_bullets": [],
            "feature_icons": [], "technical_specifications": {}, "accessories": [],
        }

    description_paragraphs = list(dict.fromkeys(
        node.get_text(" ", strip=True)
        for node in soup.select("#tab-description p")
        if node.get_text(" ", strip=True)
        and "najważniejsze cechy" not in node.get_text(" ", strip=True).casefold()
    ))
    feature_bullets = list(dict.fromkeys(
        node.get_text(" ", strip=True)
        for node in soup.select("#tab-description li")
        if node.get_text(" ", strip=True)
    ))

    feature_icons: list[dict[str, str]] = []
    for image in soup.select(".product__piktograms img"):
        image_url = _official_url(
            urljoin(source_url, image.get("src") or ""), hosts
        )
        source_label = str(
            image.get("title") or image.get("alt") or ""
        ).strip()
        if image_url and source_label:
            feature_icons.append({
                "source_label": source_label,
                "image_url": image_url,
            })

    specifications: dict[str, str] = {}
    for row in soup.select("#tab-additional_information tr"):
        label_node = row.select_one(
            ".woocommerce-product-attributes-item__label, .param"
        )
        value_node = row.select_one(
            ".woocommerce-product-attributes-item__value, .value"
        )
        label = label_node.get_text(" ", strip=True) if label_node else ""
        value = value_node.get_text(" ", strip=True) if value_node else ""
        if label and value:
            specifications[label] = value

    accessories = list(dict.fromkeys(
        node.get_text(" ", strip=True)
        for node in soup.select("#tab-standard_accessories .accessories-list li")
        if node.get_text(" ", strip=True)
    ))
    return {
        "description_paragraphs": description_paragraphs,
        "feature_bullets": feature_bullets,
        "feature_icons": feature_icons,
        "technical_specifications": specifications,
        "accessories": accessories,
    }


def _translate_complete_tecweld_description(
    provider: OpenAIProvider, model: str, details: dict[str, Any]
) -> str:
    """Translate the exact page copy without permitting summarization."""
    source_paragraphs = details.get("description_paragraphs") or []
    source_features = details.get("feature_bullets") or []
    if not source_paragraphs:
        raise ValueError("De volledige officiële Tecweld-producttekst ontbreekt.")
    prompt = """Vertaal de onderstaande officiële Tecweld-producttekst volledig en nauwkeurig
van Pools naar natuurlijk technisch Nederlands. Dit is nadrukkelijk geen samenvatting.
Behoud iedere alinea en ieder opsommingsteken afzonderlijk en in dezelfde volgorde. Laat
merken, modellen, functies, normen, getallen en eenheden exact staan. Voeg niets toe.
Geef uitsluitend JSON met description_paragraphs en feature_bullets; beide arrays moeten
exact evenveel elementen bevatten als de gelijknamige bronarrays.\n\nBRON:\n""" + json.dumps(
        {
            "description_paragraphs": source_paragraphs,
            "feature_bullets": source_features,
        },
        ensure_ascii=False,
    )
    response = provider.client.with_options(
        timeout=180.0, max_retries=0
    ).responses.create(
        model=model,
        input=prompt,
        text={"format": {"type": "json_object"}},
    )
    translated = json.loads(response.output_text)
    paragraphs = translated.get("description_paragraphs") or []
    features = translated.get("feature_bullets") or []
    if len(paragraphs) != len(source_paragraphs):
        raise ValueError("Volledigheidscontrole mislukt: niet alle alinea's zijn vertaald.")
    if len(features) != len(source_features):
        raise ValueError("Volledigheidscontrole mislukt: niet alle kenmerken zijn vertaald.")
    source_length = sum(len(str(value)) for value in [*source_paragraphs, *source_features])
    target_length = sum(len(str(value)) for value in [*paragraphs, *features])
    if source_length and target_length < source_length * 0.55:
        raise ValueError("Volledigheidscontrole mislukt: de vertaling is te sterk ingekort.")
    remaining_polish = polish_score("\n".join([*paragraphs, *features]))
    if remaining_polish:
        raise ValueError(
            f"Nederlandse taalcontrole afgekeurd: {remaining_polish} Poolse tekstsignalen"
        )
    parts = [str(value).strip() for value in paragraphs if str(value).strip()]
    if features:
        parts.extend([
            "Belangrijkste kenmerken en eigenschappen:",
            *[f"- {str(value).strip()}" for value in features if str(value).strip()],
        ])
    return "\n\n".join(parts)


def _official_product_search_url(
    website_url: str, article: str, hosts: set[str]
) -> str:
    """Resolve an exact SKU through the supplier's own shop before AI research."""
    website_host = (urlparse(website_url).hostname or "").removeprefix("www.")
    if website_host == "tecweld.pl":
        search_url = (
            f"https://tecweld.pl/?s={quote_plus(article)}"
            "&post_type=product&type_aws=true"
        )
        request = urllib.request.Request(
            search_url,
            headers={"User-Agent": "Weldingshop-PIM/1.0 (+exact Tecweld search)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                final_url = _official_url(response.geturl(), hosts)
                page = response.read(4_000_000)
        except Exception:
            return ""
        # Tecweld stuurt een unieke exacte zoekhit rechtstreeks door naar de
        # productpagina. Accepteer hem pas als de SKU ook letterlijk voorkomt.
        if final_url and "/produkty/" in urlparse(final_url).path.casefold():
            text = html.unescape(page.decode("utf-8", errors="replace"))
            if article.casefold() in text.casefold():
                return final_url
        return ""
    search_url = urljoin(
        website_url.rstrip("/") + "/", f"search?q={quote_plus(article)}"
    )
    request = urllib.request.Request(
        search_url,
        headers={"User-Agent": "Weldingshop-PIM/1.0 (+exact product search)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            page = response.read(4_000_000)
    except Exception:
        return ""
    soup = BeautifulSoup(page, "html.parser")
    wanted = article.strip().casefold()
    for item in soup.select(".item-box"):
        sku = item.select_one(".sku")
        if not sku or sku.get_text(" ", strip=True).casefold() != wanted:
            continue
        link = item.select_one(".product-title a[href]") or item.select_one("a[href]")
        if not link:
            continue
        product_url = _official_url(urljoin(search_url, link.get("href")), hosts)
        if product_url:
            return product_url
    return ""


def _page_images_for_article(source_url: str, article: str, hosts: set[str]) -> list[str]:
    """Extract images from the exact product card/detail page on the official site."""
    request = urllib.request.Request(
        source_url, headers={"User-Agent": "Weldingshop-PIM/1.0 (+product media)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            page = response.read(4_000_000)
    except Exception:
        return []
    soup = BeautifulSoup(page, "html.parser")
    source_host = (urlparse(source_url).hostname or "").removeprefix("www.")
    if source_host == "tecweld.pl":
        # Tecweld toont onder de productdetails ook gerelateerde producten.
        # Lees uitsluitend de hoofdgalerij, anders worden foto's van andere
        # artikelen aan deze SKU gekoppeld.
        urls: list[str] = []
        gallery = soup.select_one(".product__images")
        if gallery:
            for image in gallery.select("img"):
                for attribute in ("data-full", "data-parent-image", "src"):
                    image_url = _official_url(
                        urljoin(source_url, image.get(attribute) or ""), hosts
                    )
                    if image_url and urlparse(image_url).path.casefold().endswith(
                        (".jpg", ".jpeg", ".png", ".webp")
                    ):
                        urls.append(image_url)
                        break
        meta_image = soup.find("meta", attrs={"property": "og:image"})
        if meta_image and meta_image.get("content"):
            image_url = _official_url(meta_image["content"], hosts)
            if image_url and urlparse(image_url).path.casefold().endswith(
                (".jpg", ".jpeg", ".png", ".webp")
            ):
                urls.append(image_url)
        # De eerste URL is de volledige hoofdafbeelding. Verwijder alleen
        # echte afmetingen-suffixen van duplicaten; behoud verschillende foto's.
        unique: dict[str, str] = {}
        for url in urls:
            parsed = urlparse(url)
            identity = re.sub(r"-\d+x\d+(?=\.[^.]+$)", "", parsed.path.casefold())
            unique.setdefault(f"{parsed.hostname}{identity}", url)
        return list(unique.values())
    # Kentie (nopCommerce) productpagina's bevatten onder het eigen product
    # lange carrousels met andere artikelen. Beperk detailpagina's daarom tot
    # de hoofdgalerij en de productspecifieke og:image. De oude generieke
    # zoekactie kon vanaf een los artikelnummer naar de hele pagina omhoog
    # klimmen en zo foto's van gerelateerde producten meenemen.
    product_scope = soup.select_one(".product-essential")
    if product_scope:
        urls: list[str] = []
        meta_image = soup.find("meta", attrs={"property": "og:image"})
        if meta_image and meta_image.get("content"):
            image_url = _official_url(meta_image["content"], hosts)
            image_path = urlparse(image_url).path.casefold() if image_url else ""
            if image_url and not any(
                part in image_path for part in ("/stockimages/", "logo", "icon")
            ):
                urls.append(image_url)
        for link in product_scope.find_all("a", href=True):
            image_url = _official_url(urljoin(source_url, link["href"]), hosts)
            image_path = urlparse(image_url).path.casefold() if image_url else ""
            if (
                image_url
                and not any(part in image_path for part in ("/stockimages/", "logo", "icon"))
                and image_path.endswith((".jpg", ".jpeg", ".png", ".webp"))
            ):
                urls.append(image_url)
        for image in product_scope.find_all("img"):
            for attribute in ("data-zoom-image", "data-src", "src"):
                image_url = _official_url(
                    urljoin(source_url, image.get(attribute) or ""), hosts
                )
                image_path = urlparse(image_url).path.casefold() if image_url else ""
                if (
                    image_url
                    and not any(part in image_path for part in ("/stockimages/", "logo", "icon"))
                    and image_path.endswith((".jpg", ".jpeg", ".png", ".webp"))
                ):
                    urls.append(image_url)
                    break
        unique: dict[str, str] = {}
        for url in sorted(
            dict.fromkeys(urls),
            key=lambda value: bool(
                re.search(r"_\d{2,4}(?=\.[^.]+$)", urlparse(value).path)
            ),
        ):
            parsed = urlparse(url)
            identity = re.sub(r"_\d{2,4}(?=\.[^.]+$)", "", parsed.path.casefold())
            unique.setdefault(f"{parsed.hostname}{identity}", url)
        return list(unique.values())
    article_node = soup.find(string=lambda value: str(value or "").strip().casefold() == article.casefold())
    if not article_node:
        return []
    container = article_node.parent
    for _ in range(8):
        if container is None:
            break
        if container.find("img") and container.find("a", href=True):
            break
        container = container.parent
    if container is None:
        return []
    urls: list[str] = []
    detail_link = container.find("a", href=True)
    candidates: list[tuple[Any, bool]] = [(container, False)]
    if detail_link:
        detail_url = _official_url(urljoin(source_url, detail_link["href"]), hosts)
        if detail_url and detail_url != source_url:
            try:
                detail_request = urllib.request.Request(
                    detail_url, headers={"User-Agent": "Weldingshop-PIM/1.0 (+product media)"}
                )
                with urllib.request.urlopen(detail_request, timeout=20) as response:
                    candidates.insert(0, (BeautifulSoup(response.read(4_000_000), "html.parser"), True))
            except Exception:
                pass
    for candidate, is_detail_page in candidates:
        if is_detail_page:
            product_scope = candidate.select_one(".product-essential")
            image_tags = (product_scope or candidate).find_all("img")
            meta_image = candidate.find("meta", attrs={"property": "og:image"})
            if meta_image and meta_image.get("content"):
                image_url = _official_url(meta_image["content"], hosts)
                if image_url:
                    urls.append(image_url)
            for link in (product_scope or candidate).find_all("a", href=True):
                image_url = _official_url(urljoin(source_url, link["href"]), hosts)
                if image_url and urlparse(image_url).path.casefold().endswith(
                    (".jpg", ".jpeg", ".png", ".webp")
                ):
                    urls.append(image_url)
        else:
            image_tags = candidate.find_all("img")
        for image in image_tags:
            for attribute in ("data-zoom-image", "data-src", "src"):
                image_url = _official_url(urljoin(source_url, image.get(attribute) or ""), hosts)
                image_path = urlparse(image_url).path.casefold() if image_url else ""
                if (
                    image_url
                    and not any(part in image_path for part in ("/stockimages/", "logo", "icon"))
                    and image_path.endswith(
                    (".jpg", ".jpeg", ".png", ".webp")
                    )
                ):
                    urls.append(image_url)
                    break
    # NopCommerce levert dezelfde foto vaak als _415, _550 én origineel.
    # Bewaar per bronfoto de originele/grootste variant, niet drie thumbnails.
    unique: dict[str, str] = {}
    for url in sorted(
        dict.fromkeys(urls),
        key=lambda value: bool(re.search(r"_\d+(?=\.[^.]+$)", urlparse(value).path)),
    ):
        parsed = urlparse(url)
        identity = re.sub(r"_\d+(?=\.[^.]+$)", "", parsed.path.casefold())
        unique.setdefault(f"{parsed.hostname}{identity}", url)
    return list(unique.values())


def import_official_website_product(
    slug: str,
    supplier_article_number: str,
    *,
    provider: OpenAIProvider | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    execution_context: str = "selected_product",
) -> dict[str, Any]:
    """Find one exact supplier article and persist it in that supplier's PIM only."""
    total_steps = 7

    def progress(step: int, message: str) -> None:
        if progress_callback:
            progress_callback(step, total_steps, message)

    progress(1, "Geselecteerd artikel controleren in de Tecweld-PIM")
    slug = str(slug or "").strip().casefold()
    article = str(supplier_article_number or "").strip()
    if not slug or not article or len(article) > 120 or not re.search(r"[A-Za-z0-9]", article):
        raise ValueError("Vul een geldig leveranciersartikelnummer in.")
    supplier = get_supplier(slug)
    if not supplier:
        raise ValueError("Onbekende leverancier.")
    route = supplier_route(slug)
    enrichment_profile = get_enrichment_profile(slug)
    if not bool(
        (enrichment_profile.get("execution") or {}).get(execution_context)
    ):
        raise ValueError(
            f"Verrijking voor ‘{execution_context}’ staat uit in tab 8 Verrijkingsregels."
        )
    enrichment_content = enrichment_profile.get("content") or {}
    translation_rules = enrichment_profile.get("translation") or {}
    if route.website_product_adapter != "official_website":
        raise ValueError(f"Geen website-importadapter voor {supplier['name']} geconfigureerd.")
    hosts = _official_hosts(supplier)
    website_url = str(supplier.get("website_url") or "").strip()
    if not supplier.get("ai_research_allowed") or not hosts or not website_url:
        raise ValueError(
            "Koppel eerst de officiële website en sta AI-webonderzoek toe bij deze leverancier."
        )

    path: Path = init_supplier_database(slug)
    with _connect(path) as connection:
        pim_match = connection.execute(
            """SELECT sku,source_title,shopify_status,product_type,category,
                      product_group_name,execution,raw_data_json FROM products
               WHERE sku=? COLLATE NOCASE OR supplier_sku=? COLLATE NOCASE
               LIMIT 1""",
            (article, article),
        ).fetchone()
    pim_source_url = ""
    existing_raw: dict[str, Any] = {}
    if pim_match:
        try:
            pim_raw = json.loads(pim_match["raw_data_json"] or "{}")
            if isinstance(pim_raw, dict):
                existing_raw = pim_raw
            pim_source_url = _official_url(
                (pim_raw.get("website_import") or {}).get("source_url"), hosts
            )
        except (json.JSONDecodeError, TypeError):
            pass
    resolved_product_url = _official_product_search_url(
        website_url, article, hosts
    )
    # Kentie-records uit oudere verrijkingsruns kunnen een categoriepagina als
    # source_url bevatten. Zoek bij iedere nieuwe verrijking daarom eerst exact
    # op SKU en laat zo'n brede, opgeslagen URL nooit de productpaginaresolutie
    # overslaan. Voor andere leveranciers blijft de eerder geverifieerde URL
    # leidend om onnodige zoekverzoeken te voorkomen.
    exact_product_url = (
        resolved_product_url
        if slug == "kentie"
        else pim_source_url or resolved_product_url
    )
    progress(2, "Exacte officiële productpagina vaststellen")
    official_details = (
        _page_tecweld_details(exact_product_url, hosts)
        if slug == "tecweld" and exact_product_url else {}
    )
    if slug == "kentie" and pim_match and not exact_product_url:
        # Het product is al betrouwbaar afkomstig uit de ingelezen
        # leveranciersbron. Een openbare productpagina is aanvullende data en
        # mag zo'n geldige PIM-match niet alsnog als mislukking markeren.
        with _connect(path) as connection:
            image_count = connection.execute(
                "SELECT COUNT(*) FROM product_images WHERE sku=?",
                (str(pim_match["sku"]),),
            ).fetchone()[0]
        return {
            "sku": str(pim_match["sku"]),
            "created": False,
            "images": int(image_count),
            "source_url": "",
            "database": str(path),
            "shopify_status": "draft",
            "available_on_all_channels": True,
            "enrichment_skipped": True,
            "enrichment_message": (
                "Geen exacte openbare Kentie-productpagina gevonden; "
                "het product uit de ingelezen leveranciersbron is behouden."
            ),
        }

    kentie_page_confirmed = bool(
        slug == "kentie" and exact_product_url
        and _page_contains_article(exact_product_url, article)
    )
    prompt = f"""Doorzoek uitsluitend de officiële website van {supplier['name']}:
{website_url}
Zoek exact leveranciersartikelnummer: {article}
{f'De eigen zoekfunctie van de leverancier vond deze exacte kandidaat: {exact_product_url}' if exact_product_url else 'De eigen zoekfunctie leverde geen exacte kandidaat op; zoek binnen de officiële website.'}
Gebruik geen gegevens van wederverkopers en combineer geen varianten. Bevestig de match alleen
wanneer het volledige artikelnummer letterlijk op de officiële productpagina staat. Verzamel
zoveel mogelijk echte productfoto's (geen logo's of placeholders) en alle aantoonbare
productgegevens. Vertaal ook de labels bij functie-iconen, de namen van technische eigenschappen
en de meegeleverde uitrusting. Schrijf een zakelijke Nederlandse beschrijving en vertaal alleen gewone tekst;
laat merk, type, normen, maten en codes exact staan. Verzin niets. Prijzen zijn verboden.
Rechtstreeks uit de exacte officiële pagina gelezen gegevens die je moet behouden en vertalen:
{json.dumps(official_details, ensure_ascii=False)}
Antwoord uitsluitend als JSON met: exact_match (boolean), matched_article_number,
product_page_url, title, brand, ean, description, product_type, category,
technical_specifications (object), feature_icons (array met image_url en label_nl),
accessories (array met Nederlandse namen), image_urls (array), source_summary.
"""
    progress(3, "Officiële productgegevens onderzoeken en vertalen")
    provider = provider or OpenAIProvider()
    model = os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-5.6-terra")
    if kentie_page_confirmed:
        # De leverancierspagina en ingelezen Kentie-bron zijn hier leidend.
        # Een AI-webreader mag een letterlijk aanwezige SKU niet terugdraaien.
        data = _deterministic_kentie_data(
            exact_product_url, article, existing_raw,
            str(pim_match["source_title"] or "") if pim_match else "",
            dict(pim_match) if pim_match else {},
        )
    else:
        try:
            response = provider.client.with_options(timeout=120.0, max_retries=0).responses.create(
                model=model,
                tools=[{"type": "web_search", "filters": {"allowed_domains": sorted(hosts)}}],
                input=prompt,
            )
            output = response.output_text.strip()
            if output.startswith("```"):
                output = output.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = json.loads(output)
        except Exception as exc:
        # Een uitgeput AI-tegoed mag de exact gevonden officiële Tecweld-foto's
        # en bronkoppeling niet blokkeren. Bewaar dan alleen verifieerbare feiten;
        # vertaling en uitgebreidere tekst kunnen in een latere run volgen.
            quota_error = (
                "insufficient_quota" in str(exc)
                or "credit_balance_exhausted" in str(exc)
            )
            if not (slug == "tecweld" and exact_product_url and quota_error):
                raise
            data = {
            "exact_match": True,
            "matched_article_number": article,
            "product_page_url": exact_product_url,
            "title": str(pim_match["source_title"] or article) if pim_match else article,
            "brand": supplier["name"],
            "ean": "",
            "description": "",
            "product_type": "",
            "category": "",
            "technical_specifications": {"Artikelnummer": article},
            "image_urls": [],
            "source_summary": (
                "Officiële Tecweld-productpagina exact op artikelnummer gekoppeld; "
                "AI-tekstverrijking uitgesteld wegens onvoldoende API-tegoed."
            ),
            }
    if slug == "tecweld":
        try:
            assert_dutch_product_content(data)
        except ValueError:
            data = repair_product_dutch(provider, model, data)
        # Webonderzoek is nuttig voor identificatie en metadata, maar mag de
        # volledige officiële producttekst nooit reduceren tot een samenvatting.
        if (
            enrichment_content.get("full_description", True)
            and translation_rules.get("enabled", True)
        ):
            data["description"] = _translate_complete_tecweld_description(
                provider, model, official_details
            )
    source_url = _official_url(data.get("product_page_url"), hosts)
    if exact_product_url and not (
        source_url and _page_contains_article(source_url, article)
    ):
        source_url = exact_product_url
    if not source_url:
        raise ValueError("De gevonden productpagina hoort niet bij de gekoppelde officiële website.")
    ai_confirmed = (
        bool(data.get("exact_match"))
        and str(data.get("matched_article_number") or "").strip().casefold()
        == article.casefold()
    )
    page_confirmed = kentie_page_confirmed or _page_contains_article(source_url, article)
    if not ai_confirmed and not page_confirmed:
        raise ValueError(
            "Het artikelnummer is niet letterlijk aangetroffen op de gevonden officiële productpagina."
        )
    progress(4, "Productfoto's en officiële documenten verzamelen")
    ai_images = list(dict.fromkeys(
        url for url in (_official_url(value, hosts) for value in data.get("image_urls") or []) if url
    ))
    page_images = (
        _page_images_for_article(source_url, article, hosts)
        if enrichment_content.get("product_images", True) else []
    )
    # Bij Kentie is uitsluitend de exact afgebakende hoofdgalerij betrouwbaar.
    # Web search kan daarnaast thumbnails uit zoekresultaten/aanbevelingen
    # retourneren die wel op kentie.shop staan maar bij andere SKU's horen.
    images = (
        page_images
        if slug == "kentie"
        else list(dict.fromkeys([*page_images, *ai_images]))
    )
    source_documents = (
        _page_documents(source_url, hosts)
        if slug == "tecweld" and enrichment_content.get("documents", True)
        else []
    )
    if slug == "tecweld" and not official_details:
        official_details = _page_tecweld_details(source_url, hosts)
    translated_icons = data.get("feature_icons") or []
    official_icons = official_details.get("feature_icons") or []
    # URL en volgorde komen uitsluitend uit de exact geverifieerde pagina.
    # Het model mag alleen het zichtbare label vertalen.
    feature_icons = [
        {
            **item,
            "label_nl": str(
                (
                    (translated_icons[index] if index < len(translated_icons) else {}).get(
                        "label_nl"
                    )
                    if enrichment_content.get("translate_icon_labels", True)
                    else ""
                )
                or item["source_label"]
            ).strip(),
        }
        for index, item in enumerate(official_icons)
    ] if enrichment_content.get("feature_icons", True) else []
    accessories = (
        data.get("accessories") or official_details.get("accessories") or []
    ) if enrichment_content.get("accessories", True) else []
    title = str(data.get("title") or "").strip()
    if not title or title.casefold() == article.casefold():
        title = _page_product_title(source_url) or article
    description = str(data.get("description") or "").strip()
    specifications = data.get("technical_specifications") or {}
    # De rechtstreeks gelezen officiële tabel is leidend voor volledigheid.
    # De AI mag alleen de klantgerichte labels vertalen, nooit waarden verliezen.
    official_specifications = (
        official_details.get("technical_specifications") or {}
        if enrichment_content.get("technical_specifications", True) else {}
    )
    if len(specifications) < len(official_specifications):
        specifications = {**official_specifications, **specifications}
        data["technical_specifications"] = specifications
        if slug == "tecweld":
            data = repair_product_dutch(provider, model, data)
            title = str(data.get("title") or title).strip()
            description = str(data.get("description") or description).strip()
            specifications = data.get("technical_specifications") or specifications
    now = utc_now()
    # Verrijk de bestaande bronregistratie. Tecweld bewaart hier onder meer
    # Nederlandse documenten en publicatie-audits; die mogen bij heronderzoek
    # van dezelfde SKU nooit verdwijnen.
    raw = dict(existing_raw)
    raw.update({
        "website_import": {
            "adapter": route.website_product_adapter,
            "source_adapter": route.source_adapter,
            "source_url": source_url,
            "matched_by": "supplier_article_number",
            "matched_value": article,
            "verification": "official_page" if page_confirmed else "ai_exact_match",
            "researched_at": now,
            "source_summary": str(data.get("source_summary") or ""),
            "technical_specifications": specifications,
            "image_urls": images,
            "feature_icons": feature_icons,
            "accessories": accessories,
        },
    })
    publication = dict(existing_raw.get("publication") or {})
    publication.update({
        "concept": True,
        "available_on_all_channels": True,
        "price_pending": True,
    })
    raw["publication"] = publication
    normalized = {
        "title": title,
        "description": description,
        "specifications": specifications,
        "images": images,
        "source_url": source_url,
    }
    content_hash = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    progress(5, "Foto's, iconen en technische informatie in de PIM opslaan")
    localized_documents: list[dict[str, Any]] = []
    document_errors: list[str] = []
    with _connect(path) as connection:
        init_content_localization_tables(connection)
        existing = connection.execute(
            "SELECT sku FROM products WHERE sku=?", (article,)
        ).fetchone()
        connection.execute(
            """INSERT INTO products(
                sku,supplier_sku,ean,vendor,brand,source_title,source_description,
                price,sale_price,cost_price,stock_quantity,available,product_type,
                category,source_present,ai_title,html_description,shopify_status,
                inventory_policy,raw_data_json,content_hash,first_seen_at,last_seen_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL,0,1,?,?,1,?,?,'draft','continue',?,?,?,?,?)
            ON CONFLICT(sku) DO UPDATE SET
                supplier_sku=excluded.supplier_sku,ean=COALESCE(NULLIF(excluded.ean,''),products.ean),
                vendor=excluded.vendor,brand=excluded.brand,source_title=excluded.source_title,
                source_description=excluded.source_description,available=1,
                product_type=excluded.product_type,category=excluded.category,source_present=1,
                ai_title=excluded.ai_title,html_description=excluded.html_description,
                shopify_status=products.shopify_status,inventory_policy='continue',raw_data_json=excluded.raw_data_json,
                content_hash=excluded.content_hash,last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at""",
            (
                article, article, str(data.get("ean") or "").strip(), supplier["name"],
                str(data.get("brand") or supplier["name"]).strip(), title, description,
                str(data.get("product_type") or "").strip(), str(data.get("category") or "").strip(),
                title, (
                    weldingshop_product_html(
                        title, description, specifications,
                        feature_icons=(
                            feature_icons
                            if enrichment_content.get(
                                "render_icons_in_product_html", True
                            ) and enrichment_content.get(
                                "include_icons_in_shopify", True
                            ) else []
                        ),
                        accessories=accessories,
                    )
                    if slug == "tecweld" else
                    _description_html(title, description, specifications)
                ),
                json.dumps(raw, ensure_ascii=False), content_hash, now, now, now,
            ),
        )
        connection.execute("DELETE FROM product_images WHERE sku=?", (article,))
        for position, image_url in enumerate(images, 1):
            connection.execute(
                "INSERT INTO product_images(sku,image_url,position,alt_text) VALUES(?,?,?,?)",
                (article, image_url, position, title),
            )
        if slug == "tecweld":
            record_product_localization(
                connection, sku=article, title=title,
                description=description, specifications=specifications, now=now,
            )
            for document in source_documents:
                connection.execute(
                    """INSERT INTO source_product_documents(
                       sku,document_type,source_url,source_title,source_language,
                       localization_status,discovered_at,updated_at)
                       VALUES(?,?,?,?,'pl-PL','pending',?,?)
                       ON CONFLICT(sku,source_url) DO UPDATE SET
                       document_type=excluded.document_type,
                       source_title=excluded.source_title,updated_at=excluded.updated_at""",
                    (
                        article, document["document_type"], document["source_url"],
                        document["source_title"], now, now,
                    ),
                )
            for index, document in enumerate(source_documents, 1):
                progress(
                    6,
                    f"Document {index} van {len(source_documents)} vertalen in Weldingshop-huisstijl",
                )
                try:
                    localized_documents.append(localize_pdf_document(
                        connection,
                        provider=provider,
                        model=model,
                        sku=article,
                        document_type=document["document_type"],
                        source_url=document["source_url"],
                        source_title=document["source_title"],
                        output_root=TECWELD_CONTENT_ROOT,
                        now=now,
                    ))
                except Exception as exc:
                    message = f"{document['source_title']}: {exc}"
                    document_errors.append(message)
                    connection.execute(
                        """UPDATE source_product_documents SET localization_status='failed',
                           error=?,updated_at=? WHERE sku=? AND source_url=?""",
                        (str(exc), now, article, document["source_url"]),
                    )
    progress(7, "Geselecteerd Tecweld-product is volledig verwerkt en opgeslagen")
    return {
        "sku": article,
        "created": not bool(existing),
        "images": len(images),
        "source_url": source_url,
        "database": str(path),
        "shopify_status": "draft",
        "available_on_all_channels": True,
        "feature_icons": len(feature_icons),
        "documents_found": len(source_documents),
        "documents_translated": len(localized_documents),
        "document_errors": document_errors,
    }
