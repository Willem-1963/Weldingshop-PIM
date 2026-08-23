from __future__ import annotations

import hashlib
import re
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.suppliers.hub import supplier_database_path
from app.suppliers.hub import get_supplier, init_supplier_database, utc_now


PROCESS_LABELS = {
    "SMAW": "SMAW",
    "GMAW": "MIG/MAG",
    "GTAW": "TIG",
    "FCAW": "gevulde draad",
    "SAW": "onderpoederlasdraad",
    "ESAW": "elektroslak",
    "OXI-ACETYLENE": "autogeen",
    "SPECIALS": "speciaal",
}

PRODUCT_NAMES = {
    "SMAW": "laselektrode",
    "ESAW": "lasstrip",
    "OXI-ACETYLENE": "lasstaaf",
    "SPECIALS": "product",
}

PACKAGING_NAMES = {
    "d-100": "kleine spoel",
    "d100": "kleine spoel",
    "d-200": "kunststof spoel",
    "bs-200": "draadkorf",
    "d-300": "spoel",
    "s-300": "draadkorf",
    "bs-300": "draadkorf",
    "b-300": "draadkorf",
    "k-300": "draadkorf",
    "b-415": "grote draadkorf",
    "k-415": "grote draadkorf",
    "s-760": "grote haspel",
    "drum": "lasdraadvaten",
    "tube": "koker",
    "vacuum": "vacuümverpakking",
    "vacuüm": "vacuümverpakking",
    "can": "metalen bus",
    "coil": "coil",
    "ring": "ring",
}


PACKAGING_DISPLAY_CODES = {
    "d-100": "D100",
    "d-200": "D200",
    "d-300": "D300",
    "bs-200": "BS200",
    "bs-300": "BS300",
    "b-300": "B300",
    "k-300": "K300",
    "b-415": "B415",
    "k-415": "K415",
    "s-300": "S300",
    "s-760": "S760",
    "tube": "Koker",
    "doos": "Doos",
    "vacuum": "Vacuüm",
    "vacuüm": "Vacuüm",
    "drum": "Vat",
    "can": "Bus",
    "coil": "Coil",
    "ring": "Ring",
}


def _packaging_display_code(code: str) -> str:
    normalized = re.sub(r"\s+", " ", str(code or "")).strip().casefold()
    if normalized in PACKAGING_DISPLAY_CODES:
        return PACKAGING_DISPLAY_CODES[normalized]
    compact = re.sub(r"[^a-z0-9]+", "", normalized).upper()
    return compact or str(code or "").strip()


def _packaging_details(
    code: str, weight: Any, *, form: str = "", process_code: str = "",
) -> dict[str, Any]:
    original = re.sub(r"\s+", " ", str(code or "")).strip()
    normalized = original.casefold()
    packaging_name = ""
    matched_code = ""
    for candidate, name in PACKAGING_NAMES.items():
        if re.search(rf"(?:^|\s){re.escape(candidate)}(?:\s|$)", normalized):
            packaging_name, matched_code = name, candidate.upper()
            break
    if not packaging_name and process_code == "GTAW" and form == "staaf":
        packaging_name, matched_code = "koker", "Tube"
    if not packaging_name and process_code == "SMAW" and form == "staaf":
        packaging_name, matched_code = "doos", "Doos"
    if not packaging_name and original:
        packaging_name, matched_code = "verpakking", original
    number = _number(weight)
    weight_label = (
        f"{number:g} kg".replace(".", ",") if number > 0 else ""
    )
    label_parts = []
    if packaging_name:
        label_parts.append(
            f"{packaging_name} {original or matched_code}".strip()
        )
    if weight_label:
        label_parts.append(weight_label)
    return {
        "packaging_code": original or matched_code,
        "packaging_display_code": _packaging_display_code(
            original or matched_code
        ),
        "packaging_name": packaging_name,
        "package_weight": number or None,
        "package_weight_label": weight_label,
        "package_label": " · ".join(label_parts),
    }


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _decimal_label(value: str) -> str:
    number = float(str(value).replace(",", "."))
    return f"{number:.1f}".replace(".", ",")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _family_key(product_group: str, base: str) -> str:
    """Build a readable key while preserving punctuation-level identities."""
    identity = f"{product_group.casefold()}\0{base.casefold()}"
    suffix = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
    return "|".join([
        "certilas", _slug(product_group), _slug(base), suffix,
    ])


def _natural_key(value: Any) -> tuple:
    """Sort SKU fragments numerically where possible (9 before 10)."""
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(value or ""))
    )


def _variant_sort_key(item: dict[str, Any]) -> tuple:
    diameter = _number(str(item.get("diameter") or "").replace(",", "."))
    weight = _number(item.get("package_weight"))
    return (
        diameter,
        weight <= 0,  # Een onbekend/nulgewicht komt na bekende gewichten.
        weight if weight > 0 else float("inf"),
        _natural_key(item.get("sku")),
        str(item.get("package") or "").casefold(),
    )


def _process(product_group: str) -> tuple[str, str]:
    parts = str(product_group or "").split(None, 1)
    code = parts[0].upper() if parts else ""
    return code, PROCESS_LABELS.get(code, code or "Overig")


def _parse_wire(description: str) -> dict[str, str] | None:
    text = re.sub(r"\s+", " ", str(description or "")).strip()
    rod = re.match(
        r"^(?P<base>.+?)\s+(?P<diameter>\d+(?:[,.]\d+)?)\s*"
        r"x\s*(?P<length>\d+(?:[,.]\d+)?)\s*mm\b(?P<tail>.*)$",
        text, re.IGNORECASE,
    )
    if rod:
        data = rod.groupdict()
        data.update(form="staaf", package=data["tail"].strip())
        return data
    wire = re.match(
        r"^(?P<base>.+?)\s+(?P<diameter>\d+(?:[,.]\d+)?)\s*mm\b"
        r"(?P<tail>.*)$", text, re.IGNORECASE,
    )
    if not wire:
        return None
    data = wire.groupdict()
    tail = data["tail"].strip()
    if re.search(r"\bring\b", tail, re.IGNORECASE):
        form = "ring"
    elif re.search(r"\bdrum\b|\bK-\d+\b|\bLC\b", tail, re.IGNORECASE):
        form = "drum"
    elif re.search(r"\bD-\d+\b|\bBS-\d+\b|\bspool\b", tail, re.IGNORECASE):
        form = "spoel"
    else:
        form = "draad"
    data.update(form=form, package=tail, length="")
    return data


def certilas_family_preview(database_path: str | Path | None = None) -> list[dict]:
    path = Path(database_path) if database_path else supplier_database_path("certilas")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT p.*,
               (SELECT image_url FROM product_images i WHERE i.sku=p.sku
                ORDER BY position,id LIMIT 1) image_url
               FROM products p WHERE source_present=1"""
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        product = dict(row)
        parsed = _parse_wire(product.get("source_description") or "")
        if not parsed:
            continue
        process_code, process_label = _process(product.get("product_group_name") or "")
        if process_code not in PROCESS_LABELS:
            continue
        base = re.sub(
            rf"\s+{re.escape(process_label)}$", "", parsed["base"],
            flags=re.IGNORECASE,
        ).strip()
        if process_code == "GTAW":
            base = re.sub(r"\s+Tig$", "", base, flags=re.IGNORECASE).strip()
        # Een Certilas-familie volgt de bronhiërarchie: eerst de volledige
        # Description product group en daarbinnen de productomschrijving vóór
        # de eerste maat. Lengte, diameter en verpakking zijn variantkenmerken
        # en mogen dezelfde productsoort niet over meerdere families verdelen.
        product_group = re.sub(
            r"\s+", " ", str(product.get("product_group_name") or "")
        ).strip()
        key = (product_group.casefold(), base.casefold())
        positive_price = (
            _number(product.get("gross_purchase_price_per_kg")) > 0
            and _number(product.get("net_purchase_price_per_kg")) > 0
        )
        grouped[key].append({
            **product, **parsed, "base": base,
            "process_code": process_code, "process_label": process_label,
            "eligible": positive_price and bool(product.get("sku")),
            **_packaging_details(
                parsed.get("package") or "",
                product.get("kg_per_purchase_unit"),
                form=parsed["form"], process_code=process_code,
            ),
        })
    families = []
    for (_product_group_key, _base_key), variants in grouped.items():
        eligible = [item for item in variants if item["eligible"]]
        if len(variants) < 2:
            continue
        variants.sort(key=_variant_sort_key)
        base = variants[0]["base"]
        process_code = variants[0]["process_code"]
        process_label = variants[0]["process_label"]
        form = variants[0]["form"]
        product_group = re.sub(
            r"\s+", " ",
            str(variants[0].get("product_group_name") or ""),
        ).strip()
        diameters = [item["diameter"] for item in variants]
        minimum, maximum = _decimal_label(diameters[0]), _decimal_label(diameters[-1])
        product_name = PRODUCT_NAMES.get(process_code, "lasdraad")
        title = f"Certilas {process_label} {product_name} {base}"
        lengths = {
            str(item.get("length") or "").strip()
            for item in variants if str(item.get("length") or "").strip()
        }
        if form == "staaf" and len(lengths) == 1:
            title += f" – {next(iter(lengths))} mm"
        title += f" – {minimum} t/m {maximum} mm"
        family_key = _family_key(product_group, base)
        packages = {item["package"] for item in variants if item["package"]}
        for item in variants:
            item["diameter_label"] = (
                _decimal_label(item["diameter"]).replace(",", ".") + "mm"
            )
            item["diameter_option_label"] = (
                f"Diameter {item['diameter_label']}"
            )
            item["packaging_option_label"] = (
                " – ".join(filter(None, (
                    item.get("packaging_display_code"),
                    " ".join(filter(None, (
                        "" if item.get("packaging_display_code") in {"Koker", "Doos"}
                        else item.get("packaging_name"),
                        item.get("package_weight_label"),
                    ))),
                )))
            ).strip()
            if len(lengths) > 1 and item.get("length"):
                length_label = (
                    "Lengte "
                    + _decimal_label(item["length"]).replace(",", ".")
                    + "mm"
                )
                item["packaging_option_label"] = " · ".join(filter(None, (
                    length_label, item["packaging_option_label"],
                )))
            item["variant_title"] = " · ".join(filter(None, (
                item["diameter_option_label"],
                item["packaging_option_label"],
            )))
        families.append({
            "family_key": family_key, "title": title,
            "process": process_label, "form": form,
            "base": base,
            "length": next(iter(lengths)) if len(lengths) == 1 else "",
            "variants": variants,
            "excluded": [item for item in variants if not item["eligible"]],
            "image_url": next(
                (item.get("image_url") for item in variants if item.get("image_url")),
                "",
            ),
        })
    return sorted(families, key=lambda item: (item["process"], item["title"]))


def _ensure_family_tables(connection: sqlite3.Connection) -> None:
    """Keep standalone/test databases compatible with persisted families."""
    connection.executescript(
        """
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
                ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS manual_family_memberships (
            sku TEXT PRIMARY KEY,
            family_key TEXT NOT NULL,
            added_at TEXT NOT NULL
        );
        """
    )


def add_product_to_stored_family(
    family_key: str, sku: str, database_path: str | Path | None = None,
) -> dict[str, str]:
    """Persist a manual family membership and expose it immediately."""
    path = Path(database_path) if database_path else init_supplier_database("certilas")
    wanted_sku = str(sku or "").strip()
    if not wanted_sku:
        raise ValueError("Vul een SKU in.")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        _ensure_family_tables(connection)
        family = connection.execute(
            "SELECT family_json FROM product_families WHERE family_key=?",
            (family_key,),
        ).fetchone()
        product = connection.execute(
            "SELECT * FROM products WHERE sku=? COLLATE NOCASE LIMIT 1",
            (wanted_sku,),
        ).fetchone()
        if not family:
            raise ValueError("De geselecteerde productfamilie bestaat niet meer.")
        if not product:
            raise ValueError(f"SKU {wanted_sku} staat niet in de PIM.")
        current_family = connection.execute(
            "SELECT family_key FROM product_family_variants WHERE sku=? COLLATE NOCASE",
            (wanted_sku,),
        ).fetchone()
        if current_family and current_family["family_key"] != family_key:
            raise ValueError(
                f"SKU {wanted_sku} behoort al tot familie {current_family['family_key']}."
            )
        data = dict(product)
        parsed = _parse_wire(data.get("source_description") or "")
        if not parsed:
            raise ValueError(
                f"De maat en uitvoering van SKU {wanted_sku} konden niet worden gelezen."
            )
        process_code, process_label = _process(data.get("product_group_name") or "")
        packaging = _packaging_details(
            parsed.get("package") or "", data.get("kg_per_purchase_unit"),
            form=parsed["form"], process_code=process_code,
        )
        diameter = _decimal_label(parsed["diameter"]).replace(",", ".") + "mm"
        title_parts = [f"Diameter {diameter}"]
        if parsed.get("length"):
            title_parts.append(f"Lengte {_decimal_label(parsed['length']).replace(',', '.')}mm")
        package_text = " – ".join(filter(None, (
            packaging.get("packaging_display_code"),
            " ".join(filter(None, (
                "" if packaging.get("packaging_display_code") in {"Koker", "Doos"}
                else packaging.get("packaging_name"),
                packaging.get("package_weight_label"),
            ))),
        )))
        if package_text:
            title_parts.append(package_text)
        variant_title = " · ".join(title_parts)
        position = connection.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM product_family_variants WHERE family_key=?",
            (family_key,),
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM product_family_variants WHERE sku=? COLLATE NOCASE",
            (wanted_sku,),
        )
        connection.execute(
            "INSERT INTO product_family_variants(family_key,sku,variant_title,position) VALUES(?,?,?,?)",
            (family_key, wanted_sku, variant_title, position),
        )
        connection.execute(
            "INSERT OR REPLACE INTO manual_family_memberships(sku,family_key,added_at) VALUES(?,?,?)",
            (wanted_sku, family_key, utc_now()),
        )
        stored = json.loads(family["family_json"])
        stored["variants"] = [
            item for item in (stored.get("variants") or [])
            if str(item.get("sku") or "").casefold() != wanted_sku.casefold()
        ]
        stored["variants"].append({
            **data, **parsed, **packaging, "sku": wanted_sku,
            "process_code": process_code, "process_label": process_label,
            "diameter_label": diameter, "variant_title": variant_title,
            "manual_family_membership": True,
        })
        connection.execute(
            "UPDATE product_families SET family_json=? WHERE family_key=?",
            (json.dumps(stored, ensure_ascii=False), family_key),
        )
    return {"sku": wanted_sku, "variant_title": variant_title}


def load_stored_product_families(
    slug: str = "certilas", database_path: str | Path | None = None,
) -> list[dict]:
    path = Path(database_path) if database_path else init_supplier_database(slug)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        _ensure_family_tables(connection)
        rows = connection.execute(
            "SELECT * FROM product_families ORDER BY title COLLATE NOCASE"
        ).fetchall()
        product_columns = {
            row["name"] for row in connection.execute(
                "PRAGMA table_info(products)"
            ).fetchall()
        }
        enrichment_columns = [
            name for name in ("html_description", "raw_data_json")
            if name in product_columns
        ]
        enrichment_by_sku: dict[str, dict] = {}
        if enrichment_columns:
            product_rows = connection.execute(
                "SELECT sku," + ",".join(enrichment_columns) + " FROM products"
            ).fetchall()
            enrichment_by_sku = {row["sku"]: dict(row) for row in product_rows}
        images_by_sku: dict[str, list[dict]] = defaultdict(list)
        image_columns = {
            row["name"] for row in connection.execute(
                "PRAGMA table_info(product_images)"
            ).fetchall()
        }
        image_select = "sku,image_url,position"
        if "alt_text" in image_columns:
            image_select += ",alt_text"
        for image in connection.execute(
            f"SELECT {image_select} FROM product_images ORDER BY sku,position,id"
        ).fetchall():
            images_by_sku[image["sku"]].append(dict(image))
    families: list[dict] = []
    for row in rows:
        family = json.loads(row["family_json"])
        for variant in family.get("variants") or []:
            current = enrichment_by_sku.get(variant.get("sku"), {})
            variant["html_description"] = current.get("html_description") or ""
            try:
                variant["current_raw_data"] = json.loads(
                    current.get("raw_data_json") or "{}"
                )
            except json.JSONDecodeError:
                variant["current_raw_data"] = {}
            variant["images"] = images_by_sku.get(variant.get("sku"), [])
        family["research_source"] = {
            "website_url": row["website_url"] or "",
            "ai_research_allowed": bool(row["ai_research_allowed"]),
            "website_match_policy": row["website_match_policy"],
        }
        family["calculated_at"] = row["calculated_at"]
        families.append(family)
    return families


def rebuild_product_families(
    slug: str = "certilas", database_path: str | Path | None = None,
    supplier: dict[str, Any] | None = None,
) -> list[dict]:
    """Explicitly replace the stored snapshot with a fresh calculation."""
    from app.suppliers.routes import supplier_route

    if not supplier_route(slug).supports_product_families:
        raise ValueError(
            "Productfamilieberekening is niet beschikbaar voor deze leverancier."
        )
    path = Path(database_path) if database_path else init_supplier_database(slug)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        _ensure_family_tables(connection)
        manual_memberships = [
            dict(row) for row in connection.execute(
                "SELECT sku,family_key FROM manual_family_memberships"
            ).fetchall()
        ]
    families = certilas_family_preview(path)
    supplier = supplier if supplier is not None else (get_supplier(slug) or {})
    website_url = str(supplier.get("website_url") or "").strip()
    ai_allowed = bool(supplier.get("ai_research_allowed") and website_url)
    match_policy = str(
        supplier.get("website_match_policy") or "exact_sku_or_ean"
    )
    calculated_at = utc_now()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _ensure_family_tables(connection)
        connection.execute("DELETE FROM product_family_variants")
        connection.execute("DELETE FROM product_families")
        for family in families:
            connection.execute(
                """INSERT INTO product_families(
                   family_key,title,family_json,website_url,
                   ai_research_allowed,website_match_policy,calculated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    family["family_key"], family["title"],
                    json.dumps(family, ensure_ascii=False), website_url,
                    int(ai_allowed), match_policy, calculated_at,
                ),
            )
            connection.executemany(
                """INSERT INTO product_family_variants(
                   family_key,sku,variant_title,position) VALUES(?,?,?,?)""",
                [
                    (family["family_key"], item["sku"], item["variant_title"], position)
                    for position, item in enumerate(family["variants"], start=1)
                ],
            )
    for membership in manual_memberships:
        try:
            add_product_to_stored_family(
                membership["family_key"], membership["sku"], path
            )
        except ValueError:
            continue
    return load_stored_product_families(slug, path)


def get_or_create_product_families(
    slug: str = "certilas", database_path: str | Path | None = None,
    supplier: dict[str, Any] | None = None,
) -> list[dict]:
    """Read the fixed snapshot; calculate it only when none exists yet."""
    stored = load_stored_product_families(slug, database_path)
    if stored:
        return stored
    return rebuild_product_families(slug, database_path, supplier)
