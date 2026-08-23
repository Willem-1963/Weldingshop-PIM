from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.shopify.client import ShopifyClient
from app.suppliers.hub import init_supplier_database, utc_now


def _tokens(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]


def search_kentie_family_candidates(
    query: str, database_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Zoek exact op een SKU-lijst of breed op één gelijk productnaamdeel."""
    path = Path(database_path) if database_path else init_supplier_database("kentie")
    terms = _tokens(query)
    if not terms:
        return []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        if len(terms) > 1:
            placeholders = ",".join("?" for _ in terms)
            normalized = [term.casefold() for term in terms]
            rows = connection.execute(
                f"""SELECT * FROM products WHERE source_present=1 AND (
                    lower(sku) IN ({placeholders}) OR
                    lower(COALESCE(supplier_sku,'')) IN ({placeholders}))
                    ORDER BY source_title COLLATE NOCASE,sku""",
                (*normalized, *normalized),
            ).fetchall()
        else:
            needle = f"%{terms[0].casefold()}%"
            rows = connection.execute(
                """SELECT * FROM products WHERE source_present=1 AND (
                   lower(sku)=? OR lower(COALESCE(supplier_sku,''))=? OR
                   lower(COALESCE(source_title,'')) LIKE ? OR
                   lower(COALESCE(ai_title,'')) LIKE ?)
                   ORDER BY source_title COLLATE NOCASE,sku LIMIT 250""",
                (terms[0].casefold(), terms[0].casefold(), needle, needle),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["images"] = [
                dict(image) for image in connection.execute(
                    """SELECT image_url,position,alt_text FROM product_images
                       WHERE sku=? ORDER BY position,id""", (item["sku"],)
                ).fetchall()
            ]
            result.append(item)
    return result


def _common_title(products: list[dict[str, Any]]) -> str:
    titles = [
        " ".join(str(item.get("ai_title") or item.get("source_title") or "").split())
        for item in products
    ]
    words = titles[0].split() if titles else []
    for title in titles[1:]:
        other = title.split()
        matching = []
        for index, word in enumerate(words):
            if index >= len(other) or word.casefold() != other[index].casefold():
                break
            matching.append(word)
        words = matching
        if not words:
            break
    common = " ".join(words).strip(" ,-–/")
    return common if len(common) >= 4 else (titles[0] if titles else "Kentie productfamilie")


def save_kentie_product_family(
    skus: list[str], title: str = "", database_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(database_path) if database_path else init_supplier_database("kentie")
    wanted = list(dict.fromkeys(str(sku).strip() for sku in skus if str(sku).strip()))
    if len(wanted) < 2:
        raise ValueError("Selecteer minimaal twee Kentie-producten als varianten.")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in wanted)
        rows = connection.execute(
            f"SELECT * FROM products WHERE sku IN ({placeholders})", wanted
        ).fetchall()
        if len(rows) != len(wanted):
            found = {row["sku"] for row in rows}
            raise ValueError("Niet alle geselecteerde SKU's staan in de Kentie-PIM: " + ", ".join(sku for sku in wanted if sku not in found))
        products_by_sku = {row["sku"]: dict(row) for row in rows}
        products = [products_by_sku[sku] for sku in wanted]
        family_title = " ".join(str(title or _common_title(products)).split()).strip()
        if not family_title:
            raise ValueError("Vul een productfamilienaam in.")
        identity = "\0".join(sorted(sku.casefold() for sku in wanted))
        family_key = "kentie|manual|" + hashlib.sha1(identity.encode()).hexdigest()[:12]
        variants = []
        for position, product in enumerate(products, 1):
            variant_title = str(
                product.get("ai_title") or product.get("source_title") or product["sku"]
            ).strip()
            variants.append({
                **product, "variant_title": variant_title, "position": position,
            })
        family = {
            "family_key": family_key, "title": family_title,
            "base": family_title, "process": "Kentie", "form": "product",
            "variants": variants, "excluded": [],
            "image_url": next((
                image[0] for product in products
                if (image := connection.execute(
                    "SELECT image_url FROM product_images WHERE sku=? ORDER BY position,id LIMIT 1",
                    (product["sku"],),
                ).fetchone())
            ), ""),
            "manual_selection": True,
        }
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS product_families(
              family_key TEXT PRIMARY KEY,title TEXT NOT NULL,family_json TEXT NOT NULL,
              website_url TEXT,ai_research_allowed INTEGER NOT NULL DEFAULT 0,
              website_match_policy TEXT NOT NULL DEFAULT 'exact_sku_or_ean',
              calculated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS product_family_variants(
              family_key TEXT NOT NULL,sku TEXT NOT NULL,variant_title TEXT NOT NULL,
              position INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(family_key,sku));
        """)
        connection.execute(
            """INSERT OR REPLACE INTO product_families(
               family_key,title,family_json,calculated_at) VALUES(?,?,?,?)""",
            (family_key, family_title, json.dumps(family, ensure_ascii=False), utc_now()),
        )
        connection.execute("DELETE FROM product_family_variants WHERE family_key=?", (family_key,))
        connection.executemany(
            "INSERT INTO product_family_variants VALUES(?,?,?,?)",
            [(family_key, item["sku"], item["variant_title"], item["position"]) for item in variants],
        )
    return family


def preview_kentie_shopify_removals(skus: list[str]) -> dict[str, Any]:
    """Maak een alleen-lezen voorstel voor exact geselecteerde Kentie-SKU's."""
    wanted = list(dict.fromkeys(str(sku).strip() for sku in skus if str(sku).strip()))
    client = ShopifyClient.from_settings()
    found = []
    missing = []
    for sku in wanted:
        match = client.find_variant_by_sku(sku)
        if not match:
            missing.append(sku)
            continue
        product = match.get("product") or {}
        found.append({
            "sku": sku,
            "variant_id": str(match.get("id") or ""),
            "variant_title": str(match.get("title") or ""),
            "product_id": str(product.get("id") or ""),
            "product_title": str(product.get("title") or ""),
            "product_handle": str(product.get("handle") or ""),
            "vendor": str(product.get("vendor") or ""),
            "status": str(product.get("status") or ""),
        })
    return {
        "supplier_slug": "kentie",
        "selected_skus": wanted,
        "existing_variants": found,
        "not_in_shopify": missing,
        "safe_sequence": [
            "Maak eerst een Shopify-snapshot van de gevonden producten en varianten.",
            "Bouw de nieuwe Kentie-familie als concept met tijdelijke migratie-SKU's.",
            "Controleer titel, tekst, foto's, prijzen en het exacte variantaantal.",
            "Verwijder daarna uitsluitend de exact geselecteerde oude varianten.",
            "Zet de tijdelijke varianten om naar de oorspronkelijke Kentie-SKU's.",
            "Controleer opnieuw en activeer het familieproduct pas na aparte bevestiging.",
        ],
        "destructive_action_performed": False,
    }


def create_kentie_shopify_family_draft(family_key: str) -> dict[str, Any]:
    """Maak één nieuw Shopify-concept met de opgeslagen Kentie-varianten."""
    from app.shopify.sync import _input, _selling_price, _source_products
    from app.suppliers.hub import get_supplier

    path = init_supplier_database("kentie")
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT family_json FROM product_families WHERE family_key=?", (family_key,)
        ).fetchone()
    if not row:
        raise ValueError("De geselecteerde Kentie-productfamilie bestaat niet meer.")
    family = json.loads(row[0])
    sources = {item["sku"]: item for item in _source_products("kentie")}
    members = family.get("variants") or []
    missing = [item["sku"] for item in members if item["sku"] not in sources]
    if missing:
        raise ValueError("PIM-varianten ontbreken: " + ", ".join(missing))
    selected = [dict(sources[item["sku"]]) for item in members]
    invalid = [
        item["sku"] for item in selected
        if (_selling_price(item) or 0) <= 0
    ]
    if invalid:
        raise ValueError("Verkoopprijs ontbreekt voor: " + ", ".join(invalid))
    client = ShopifyClient.from_settings()
    existing = [item["sku"] for item in selected if client.find_variant_by_sku(item["sku"])]
    if existing:
        raise ValueError("Deze SKU's bestaan al in Shopify: " + ", ".join(existing))
    supplier = get_supplier("kentie") or {}
    files: list[dict[str, Any]] = []
    variants = []
    option_names: list[str] = []
    for member, source in zip(members, selected):
        source["_supplier_slug"] = "kentie"
        source["_raw_data"] = json.loads(source.get("raw_data_json") or "{}")
        source["_shopify_field_mapping"] = supplier.get("shopify_field_mapping") or {}
        source["_shopify_metafield_mapping"] = supplier.get("shopify_metafield_mapping") or {}
        built = _input(source, None)
        for file in built.get("files") or []:
            if file.get("originalSource") and file["originalSource"] not in {saved.get("originalSource") for saved in files}:
                files.append(file)
        variant = built["variants"][0]
        variant.pop("file", None)
        option = str(member.get("variant_title") or source["sku"]).strip()[:255]
        if option in option_names:
            option = f"{option} · {source['sku']}"[:255]
        option_names.append(option)
        variant["optionValues"] = [{"optionName": "Uitvoering", "name": option}]
        variants.append(variant)
    anchor = max(selected, key=lambda item: (len(str(item.get("html_description") or "")), len(item.get("images") or [])))
    anchor["_supplier_slug"] = "kentie"
    anchor["_raw_data"] = json.loads(anchor.get("raw_data_json") or "{}")
    anchor["_shopify_field_mapping"] = supplier.get("shopify_field_mapping") or {}
    anchor["_shopify_metafield_mapping"] = supplier.get("shopify_metafield_mapping") or {}
    product = _input(anchor, None)
    product.update({
        "title": family["title"][:255], "status": "DRAFT",
        "vendor": str(supplier.get("name") or "Kentie"),
        "productOptions": [{"name": "Uitvoering", "position": 1, "values": [{"name": name} for name in option_names]}],
        "variants": variants, "files": files,
    })
    payload = client.graphql(
        """mutation($input:ProductSetInput!){productSet(synchronous:true,input:$input){
        product{id title handle status variants(first:100){nodes{sku}}}
        userErrors{field message code}}}""", {"input": product},
    )["productSet"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
    saved = payload.get("product") or {}
    actual = [item["sku"] for item in (saved.get("variants") or {}).get("nodes") or []]
    expected = [item["sku"] for item in selected]
    if set(actual) != set(expected):
        raise RuntimeError("Shopify-variantcontrole mislukt.")
    return {
        "id": saved["id"], "title": saved["title"], "status": saved["status"],
        "variants": actual,
        "admin_url": f"https://{client.shop_domain}/admin/products/{saved['id'].rsplit('/', 1)[-1]}",
    }
