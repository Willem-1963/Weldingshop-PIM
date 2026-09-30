import html
import json
import sqlite3
from pathlib import Path


ROOT = Path("/root/weldingshop-pim")
DB_PATH = ROOT / "data/database/suppliers/vynckier.sqlite"
RICH_PATH = ROOT / "data/imports/vynckier-rich-pim-source-2026-09-17.json"

RICH_FIELDS = {
    "specificaties",
    "specs_json",
    "afmetingen",
    "gewicht",
    "document_links",
    "document_source_links",
    "handleidingen",
    "documenten",
    "leverancier_omschrijving",
    "kenmerken",
    "voordelen",
    "toepassingen",
    "capaciteit",
    "geleverd_met",
    "bijgeleverde_accessoires",
    "opties",
    "veiligheidsopmerking",
    "vergelijkingsproducten_json",
    "image_urls",
    "source_description_excerpt",
}


def clean(value):
    text = str(value or "").strip()
    if not text or text.lower() in {"[]", "{}", "null", "none"}:
        return ""
    return text


def split_pipe(value):
    return [part.strip() for part in clean(value).split("|") if part.strip()]


def paragraph(value):
    text = clean(value)
    return f"<p>{html.escape(text)}</p>" if text else ""


def section(title, value):
    items = split_pipe(value)
    if not items:
        return ""
    if len(items) == 1:
        return f"<h3>{html.escape(title)}</h3><p>{html.escape(items[0])}</p>"
    return (
        f"<h3>{html.escape(title)}</h3><ul>"
        + "".join(f"<li>{html.escape(item)}</li>" for item in items)
        + "</ul>"
    )


def rich_html(row):
    blocks = [
        paragraph(row.get("leverancier_omschrijving") or row.get("description")),
        section("Kenmerken", row.get("kenmerken")),
        section("Voordelen", row.get("voordelen")),
        section("Toepassingen", row.get("toepassingen")),
        section("Geleverd met", row.get("geleverd_met")),
        section("Bijgeleverd", row.get("bijgeleverde_accessoires")),
        section("Opties", row.get("opties")),
        section("Let op", row.get("veiligheidsopmerking")),
    ]
    return "\n".join(block for block in blocks if block)


def main():
    rows = json.loads(RICH_PATH.read_text())
    by_sku = {str(row.get("sku") or "").upper(): row for row in rows if row.get("sku")}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    updated = 0
    missing = []
    rich_counts = {field: 0 for field in RICH_FIELDS}
    for sku, rich in sorted(by_sku.items()):
        current = conn.execute(
            "SELECT sku, raw_data_json, source_description, html_description FROM products WHERE UPPER(sku)=?",
            (sku,),
        ).fetchone()
        if not current:
            missing.append(sku)
            continue
        try:
            raw = json.loads(current["raw_data_json"] or "{}")
        except json.JSONDecodeError:
            raw = {}
        changed = False
        for field in sorted(RICH_FIELDS):
            value = rich.get(field)
            if value in (None, ""):
                continue
            raw[field] = value
            rich_counts[field] += 1
            changed = True
        description = clean(rich.get("leverancier_omschrijving") or rich.get("description"))
        html_description = rich_html(rich)
        if changed or description or html_description:
            conn.execute(
                """
                UPDATE products
                SET raw_data_json=?,
                    source_description=COALESCE(NULLIF(?, ''), source_description),
                    html_description=COALESCE(NULLIF(?, ''), html_description),
                    updated_at=datetime('now')
                WHERE UPPER(sku)=?
                """,
                (
                    json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                    description,
                    html_description,
                    sku,
                ),
            )
            updated += 1
    conn.commit()
    print(json.dumps({
        "rich_rows": len(rows),
        "updated_products": updated,
        "missing_skus": missing,
        "rich_counts": rich_counts,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
