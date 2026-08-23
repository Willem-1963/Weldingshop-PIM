from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.product_families import load_stored_product_families
from app.suppliers.hub import get_supplier


PLAYWRIGHT_SITE_PACKAGES = "/opt/weldingshop-browser/venv/lib/python3.12/site-packages"
CDP_URL = "http://127.0.0.1:9222"
PROJECT_DIR = Path(__file__).resolve().parents[2]


def portal_product_name(family: dict[str, Any]) -> str:
    base = str(family.get("base") or "").strip()
    if family.get("process") == "TIG" and not base.casefold().endswith(" tig"):
        return f"{base} Tig"
    return base


async def _scrape(family_key: str) -> dict[str, Any]:
    if PLAYWRIGHT_SITE_PACKAGES not in sys.path:
        sys.path.append(PLAYWRIGHT_SITE_PACKAGES)
    from playwright.async_api import async_playwright

    supplier = get_supplier("certilas", include_credentials=True) or {}
    username = supplier.get("dealer_username") or ""
    password = supplier.get("dealer_secret") or ""
    portal_url = supplier.get("dealer_portal_url") or ""
    if not (username and password and portal_url.startswith("https://portaal.certilas.nl/")):
        raise ValueError("De beveiligde Certilas-dealerlogin is niet compleet ingesteld.")
    family = next(
        (item for item in load_stored_product_families("certilas")
         if item["family_key"] == family_key),
        None,
    )
    if not family:
        raise ValueError("Onbekende opgeslagen productfamilie.")
    requested_eans = {
        str(item.get("ean") or "").strip()
        for item in family["variants"] if str(item.get("ean") or "").strip()
    }
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(CDP_URL)
        if not browser.contexts:
            raise RuntimeError("De beveiligde browsercontext is niet beschikbaar.")
        page = await browser.contexts[0].new_page()
        try:
            await page.goto(portal_url, wait_until="domcontentloaded", timeout=30_000)
            user_field = page.locator('input[name="txtBIZ_UN"]')
            password_field = page.locator('input[name="txtBIZ_PW"]')
            if await user_field.count() and await password_field.count():
                await user_field.fill(username)
                await password_field.fill(password)
                await page.locator('button[type="submit"],input[type="submit"]').first.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30_000)
            product_name = portal_product_name(family)
            if family.get("variants"):
                matched_eans = set()
                matched_source_urls = []
                bodies = []
                all_images = []
                for item in family["variants"]:
                    sku = str(item.get("sku") or "").strip()
                    ean = str(item.get("ean") or "").strip()
                    if not sku or not ean:
                        continue
                    await page.goto(
                        "https://portaal.certilas.nl/nl/",
                        wait_until="domcontentloaded", timeout=30_000,
                    )
                    search = page.locator("#txtSearchArticles")
                    await search.fill(sku)
                    await search.press("Enter")
                    await page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    result_body = await page.locator("body").inner_text()
                    result = page.get_by_role(
                        "link", name=product_name, exact=True
                    ).first
                    if (
                        await result.count()
                        and f"U heeft gezocht: '{sku}'" in result_body
                    ):
                        matched_eans.add(ean)
                        if not bodies:
                            await result.click()
                            await page.wait_for_load_state(
                                "domcontentloaded", timeout=30_000
                            )
                            matched_source_urls.append(page.url)
                            bodies.append(await page.locator("body").inner_text())
                            page_images = await page.locator("img").evaluate_all(
                                """els => els.map(e => ({src:e.src,alt:e.alt}))
                                   .filter(x => x.src.includes('pro.cdn.certilas.com') &&
                                                x.alt.toLowerCase().includes('ceweld'))"""
                            )
                            all_images.extend(
                                image["src"] for image in page_images
                            )
                            position_images = await page.locator(
                                'img[src*="Web_welding-positions"]'
                            ).evaluate_all("els => els.map(e => e.src)")
                if not matched_eans:
                    raise ValueError(
                        "Geen exacte familie-SKU in het dealerportaal gevonden."
                    )
                return {
                    "source_url": matched_source_urls[0],
                    "source_urls": matched_source_urls,
                    "matched_eans": sorted(matched_eans),
                    "matched_by": "sku",
                    "text": "\n\n".join(bodies)[:24_000],
                    "image_urls": list(dict.fromkeys(all_images)),
                    "welding_positions": list(dict.fromkeys(
                        Path(url.split("?", 1)[0]).stem.upper()
                        for url in position_images
                    )),
                    "welding_position_images": {
                        Path(url.split("?", 1)[0]).stem.upper(): url
                        for url in position_images
                    },
                }
            link = page.get_by_role("link", name=product_name, exact=True).first
            if not await link.count():
                search = page.locator('#txtSearchArticles')
                if await search.count():
                    await search.fill(product_name)
                    await search.press("Enter")
                    await page.wait_for_load_state("domcontentloaded", timeout=30_000)
                    link = page.get_by_role("link", name=product_name, exact=True).first
            if not await link.count():
                raise ValueError("De familie is niet exact in het dealerportaal gevonden.")
            await link.click()
            await page.wait_for_load_state("domcontentloaded", timeout=30_000)
            product_urls = [page.url]
            bodies = []
            all_images = []
            matched_eans = set()
            matched_source_urls = []
            position_images = []
            for product_url in product_urls:
                await page.goto(
                    product_url, wait_until="domcontentloaded", timeout=30_000
                )
                body = await page.locator("body").inner_text()
                page_matches = {ean for ean in requested_eans if ean in body}
                if page_matches:
                    matched_eans.update(page_matches)
                    matched_source_urls.append(page.url)
                    bodies.append(body)
                page_images = await page.locator("img").evaluate_all(
                    """els => els.map(e => ({src:e.src,alt:e.alt}))
                       .filter(x => x.src.includes('pro.cdn.certilas.com') &&
                                    x.alt.toLowerCase().includes('ceweld'))"""
                )
                all_images.extend(item["src"] for item in page_images)
                position_images.extend(
                    await page.locator(
                        'img[src*="Web_welding-positions"]'
                    ).evaluate_all("els => els.map(e => e.src)")
                )
            if not matched_eans:
                raise ValueError("Geen exacte familie-EAN op de portaalpagina gevonden.")
            return {
                "source_url": matched_source_urls[0],
                "source_urls": matched_source_urls,
                "matched_eans": sorted(matched_eans),
                "text": "\n\n".join(bodies)[:24_000],
                "image_urls": list(dict.fromkeys(all_images)),
                "welding_positions": list(dict.fromkeys(
                    Path(url.split("?", 1)[0]).stem.upper()
                    for url in position_images
                )),
                "welding_position_images": {
                    Path(url.split("?", 1)[0]).stem.upper(): url
                    for url in position_images
                },
            }
        finally:
            await page.close()


def scrape_certilas_portal_family(family_key: str) -> dict[str, Any]:
    return asyncio.run(_scrape(family_key))


async def _download_pricelist() -> dict[str, Any]:
    if PLAYWRIGHT_SITE_PACKAGES not in sys.path:
        sys.path.append(PLAYWRIGHT_SITE_PACKAGES)
    from playwright.async_api import async_playwright

    supplier = get_supplier("certilas", include_credentials=True) or {}
    username = supplier.get("dealer_username") or ""
    password = supplier.get("dealer_secret") or ""
    if not username or not password:
        raise ValueError("De beveiligde Certilas-dealerlogin is niet compleet ingesteld.")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(CDP_URL)
        if not browser.contexts:
            raise RuntimeError("De beveiligde browsercontext is niet beschikbaar.")
        page = await browser.contexts[0].new_page()
        try:
            target = "https://portaal.certilas.nl/nl/prijzen-toeslagen/"
            await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            user_field = page.locator('input[name="txtBIZ_UN"]')
            if await user_field.count():
                await user_field.fill(username)
                await page.locator('input[name="txtBIZ_PW"]').fill(password)
                await page.locator('button[type="submit"],input[type="submit"]').first.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30_000)
                await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
            buttons = page.get_by_text("Downloaden", exact=True)
            if await buttons.count() < 1:
                raise RuntimeError("De knop Prijslijst downloaden is niet gevonden.")
            async with page.expect_download(timeout=60_000) as pending:
                await buttons.nth(0).click()
            download = await pending.value
            filename = download.suggested_filename
            if not filename.lower().endswith(".xlsx"):
                raise ValueError("Het dealerportaal leverde geen Excel-prijslijst.")
            directory = PROJECT_DIR / "data" / "imports" / "certilas" / "dealer"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / filename
            response = await browser.contexts[0].request.get(download.url)
            if not response.ok:
                raise RuntimeError(
                    f"Prijslijstdownload gaf HTTP-status {response.status}."
                )
            payload = await response.body()
            if not payload.startswith(b"PK"):
                raise ValueError("De dealerprijslijst is geen geldig XLSX-bestand.")
            path.write_bytes(payload)
            return {
                "path": str(path), "filename": filename,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload), "source_url": target,
            }
        finally:
            await page.close()


def download_certilas_pricelist() -> dict[str, Any]:
    return asyncio.run(_download_pricelist())


if __name__ == "__main__":
    result = (
        download_certilas_pricelist()
        if sys.argv[1] == "--download-pricelist"
        else scrape_certilas_portal_family(sys.argv[1])
    )
    print(json.dumps(result, ensure_ascii=False))
