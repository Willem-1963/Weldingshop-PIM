#!/usr/bin/env python3
"""Voltooi resterende families met veilige PIM-tekst en lokale illustraties."""

import html
import json
import sqlite3

from app.product_families import rebuild_product_families
from app.shopify.client import ShopifyClient
from app.suppliers.hub import supplier_database_path, utc_now
from scripts.create_welding_knowledge_blogs import ASSET_DIR, upload_file
from scripts.repair_incomplete_certilas_families import incomplete_families


ASSETS = {
    "aluminum_spool": ("certilas-illustration-aluminum-spool.png", "Illustratieve afbeelding van aluminium MIG-lasdraad op spoel"),
    "aluminum_rods": ("certilas-illustration-aluminum-rods.png", "Illustratieve afbeelding van aluminium TIG-lasstaven in koker"),
    "alloy_rods": ("certilas-illustration-alloy-rods.png", "Illustratieve afbeelding van TIG-lasstaven in koker"),
    "steel_spool": ("certilas-illustration-steel-spool.png", "Illustratieve afbeelding van gelegeerde MIG-lasdraad op spoel"),
    "saw_wire": ("certilas-illustration-saw-wire.png", "Illustratieve afbeelding van onderpoederlasdraad op haspel"),
}


def asset_for(family: dict) -> str:
    key = family["family_key"]
    title = family["title"].casefold()
    if "|saw|" in key:
        return "saw_wire"
    if "|gtaw|" in key:
        return "aluminum_rods" if any(x in title for x in (" al ", "almg", "alsi")) else "alloy_rods"
    if any(x in title for x in (" al ", "almg", "alsi")):
        return "aluminum_spool"
    return "steel_spool"


def safe_description(family_json: dict) -> str:
    diameters = [str(v.get("diameter_label") or "") for v in family_json.get("variants", [])]
    diameter = " t/m ".join([diameters[0], diameters[-1]]) if diameters else ""
    process = str(family_json.get("process") or "lassen")
    form = "TIG-lasstaven" if family_json.get("form") == "staaf" else "lasdraad"
    length = str(family_json.get("length") or "")
    details = [f"Lasproces: {process}", f"Uitvoering: {form}"]
    if length:
        details.append(f"Lengte: {length} mm")
    if diameter:
        details.append(f"Beschikbare diameters: {diameter}")
    return (
        f"<h2>{html.escape(family_json['title'])}</h2>"
        f"<h3>Productfamilie</h3><p>{html.escape(family_json.get('base') or '')} "
        f"{html.escape(form)} voor het {html.escape(process)}. Kies hieronder de gewenste diameter en verpakking.</p>"
        "<h3>Uitvoeringen</h3><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in details) + "</ul>"
        "<p>Gebruik voor gekwalificeerd of kritisch laswerk altijd de geldende WPS, het productdatablad en het materiaalcertificaat.</p>"
    )


def main() -> dict:
    path = supplier_database_path("certilas")
    targets = incomplete_families(path)
    client = ShopifyClient.from_settings()
    urls = {key: upload_file(client, ASSET_DIR / filename, alt) for key, (filename, alt) in ASSETS.items()}
    text_added = images_added = 0
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        for family in targets:
            stored = connection.execute("SELECT family_json FROM product_families WHERE family_key=?", (family["family_key"],)).fetchone()
            data = json.loads(stored[0])
            image_key = asset_for(family)
            filename, alt = ASSETS[image_key]
            rows = connection.execute("SELECT sku,html_description FROM products WHERE sku IN (SELECT sku FROM product_family_variants WHERE family_key=?)", (family["family_key"],)).fetchall()
            for row in rows:
                if not row["html_description"]:
                    connection.execute("UPDATE products SET html_description=?,updated_at=? WHERE sku=?", (safe_description(data), utc_now(), row["sku"]))
                    text_added += 1
                if not connection.execute("SELECT 1 FROM product_images WHERE sku=? LIMIT 1", (row["sku"],)).fetchone():
                    connection.execute("INSERT OR IGNORE INTO product_images(sku,image_url,position,alt_text) VALUES(?,?,1,?)", (row["sku"], urls[image_key], alt))
                    images_added += 1
    rebuild_product_families("certilas", path)
    remaining = incomplete_families(path)
    with sqlite3.connect(path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return {"families_completed":len(targets)-len(remaining),"remaining":len(remaining),"texts_added":text_added,"images_added":images_added,"integrity":integrity}


if __name__ == "__main__": print(json.dumps(main(),ensure_ascii=False))
