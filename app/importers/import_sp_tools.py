from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

from app.database.database import get_connection, init_database, log

console = Console()

INPUT_DIR = Path("data/input")
EXCEL_FILE = INPUT_DIR / "SP Tools bruto prijslijst 2026 v3.xlsx"
CSV_FILE = INPUT_DIR / "productdetails.csv"


def clean_sku(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip().upper()


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def to_float(value: Any) -> float | None:
    if value is None or pd.isna(value) or value == "":
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", ".").strip()
        return float(value)
    except Exception:
        return None


def html_to_clean_html(raw_html: str, fallback_title: str = "") -> str:
    """Houdt bestaande HTML bruikbaar, maar maakt er een nette basisbeschrijving van."""
    if not raw_html:
        return f"<h2>{fallback_title}</h2>\n<p>Professioneel SP Tools product voor werkplaats en industrie.</p>"

    soup = BeautifulSoup(raw_html, "html.parser")
    text_items = [li.get_text(" ", strip=True) for li in soup.find_all("li")]
    clean_text_value = soup.get_text(" ", strip=True)

    parts = []
    if fallback_title:
        parts.append(f"<h2>{fallback_title}</h2>")
    if clean_text_value:
        parts.append(f"<p>{clean_text_value}</p>")
    if text_items:
        parts.append("<h3>Productdetails</h3>")
        parts.append("<ul>")
        for item in text_items:
            if item:
                parts.append(f"<li>{item}</li>")
        parts.append("</ul>")
    return "\n".join(parts)


def read_excel() -> pd.DataFrame:
    if not EXCEL_FILE.exists():
        raise FileNotFoundError(f"Excelbestand ontbreekt: {EXCEL_FILE}")

    raw = pd.read_excel(EXCEL_FILE, sheet_name=0, header=None)
    header_row = None
    for idx in range(min(20, len(raw))):
        values = [str(v).strip() for v in raw.iloc[idx].tolist()]
        if "Item Number" in values:
            header_row = idx
            break

    if header_row is None:
        raise ValueError("Kon de Excel-header 'Item Number' niet vinden.")

    df = pd.read_excel(EXCEL_FILE, sheet_name=0, header=header_row)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(
        columns={
            "Item Number": "sku",
            "Desciption(English)": "description_en",
            "Description(Dutch)": "description_nl",
            "Product Group": "product_group",
            "2026 AVP €": "price",
            "Length": "length_mm",
            "Width": "width_mm",
            "Height": "height_mm",
            "weight gross in gram": "weight_grams",
            "Barcode": "ean",
            "IMAGE": "excel_image",
        }
    )
    df["sku"] = df["sku"].apply(clean_sku)
    df = df[df["sku"] != ""].copy()
    return df


def read_productdetails() -> pd.DataFrame:
    if not CSV_FILE.exists():
        raise FileNotFoundError(f"CSV-bestand ontbreekt: {CSV_FILE}")

    df = pd.read_csv(CSV_FILE, sep=None, engine="python", encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]
    if "reference" not in df.columns:
        raise ValueError("CSV mist kolom 'reference'.")

    df["sku"] = df["reference"].apply(clean_sku)
    df = df[df["sku"] != ""].copy()
    return df


def image_urls_from_row(row: pd.Series) -> list[str]:
    urls: list[str] = []
    for col in ["Images", "primary_image"] + [f"image_{i}" for i in range(0, 20)] + ["excel_image"]:
        if col in row.index:
            value = clean_text(row.get(col))
            if value and value.lower() != "nan" and value.startswith("http"):
                for part in re.split(r"[|,;]\s*", value):
                    part = part.strip()
                    if part.startswith("http") and part not in urls:
                        urls.append(part)
    return urls


def upsert_product(row: dict[str, Any], images: list[str]) -> None:
    sku = clean_sku(row.get("sku"))
    if not sku:
        return

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO products (
                sku, name, title_nl, description_html, price, sale_price,
                ean, weight_grams, length_mm, width_mm, height_mm,
                category, category_full, product_group,
                source_csv, source_excel, status, inventory_policy, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 'continue', CURRENT_TIMESTAMP)
            ON CONFLICT(sku) DO UPDATE SET
                name=excluded.name,
                title_nl=excluded.title_nl,
                description_html=excluded.description_html,
                price=excluded.price,
                sale_price=excluded.sale_price,
                ean=excluded.ean,
                weight_grams=excluded.weight_grams,
                length_mm=excluded.length_mm,
                width_mm=excluded.width_mm,
                height_mm=excluded.height_mm,
                category=excluded.category,
                category_full=excluded.category_full,
                product_group=excluded.product_group,
                source_csv=MAX(products.source_csv, excluded.source_csv),
                source_excel=MAX(products.source_excel, excluded.source_excel),
                status='draft',
                inventory_policy='continue',
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                sku,
                clean_text(row.get("name")),
                clean_text(row.get("title_nl") or row.get("description_nl") or row.get("name")),
                row.get("description_html") or "",
                to_float(row.get("price")),
                to_float(row.get("sale_price")),
                clean_text(row.get("ean")),
                to_float(row.get("weight_grams") or row.get("weight")),
                to_float(row.get("length_mm")),
                to_float(row.get("width_mm")),
                to_float(row.get("height_mm")),
                clean_text(row.get("category")),
                clean_text(row.get("category_full")),
                clean_text(row.get("product_group")),
                int(row.get("source_csv", 0)),
                int(row.get("source_excel", 0)),
            ),
        )

        for pos, url in enumerate(images, start=1):
            conn.execute(
                "INSERT OR IGNORE INTO images(sku, image_url, position) VALUES (?, ?, ?)",
                (sku, url, pos),
            )


def run_import() -> None:
    init_database()

    excel = read_excel()
    csv = read_productdetails()

    console.print(f"[green]Excel regels:[/green] {len(excel)}")
    console.print(f"[green]Productdetails regels:[/green] {len(csv)}")

    excel["source_excel"] = 1
    csv["source_csv"] = 1

    csv_small = csv.copy()
    csv_small["description_html"] = [
        html_to_clean_html(clean_text(v), clean_text(n))
        for v, n in zip(csv_small.get("description", ""), csv_small.get("name", ""))
    ]

    merged = pd.merge(
        csv_small,
        excel,
        on="sku",
        how="outer",
        suffixes=("_csv", "_excel"),
    )

    count = 0
    for _, r in merged.iterrows():
        row = r.to_dict()

        # Kies beste velden uit CSV en Excel
        unified = {
            "sku": row.get("sku"),
            "name": row.get("name") or row.get("description_en"),
            "title_nl": row.get("description_nl") or row.get("name") or row.get("description_en"),
            "description_html": row.get("description_html") or "",
            "price": row.get("price_excel") if "price_excel" in row else row.get("price"),
            "sale_price": row.get("sale_price"),
            "ean": row.get("ean_csv") or row.get("ean_excel") or row.get("Barcode"),
            "weight_grams": row.get("weight_grams") or row.get("weight"),
            "length_mm": row.get("length_mm"),
            "width_mm": row.get("width_mm"),
            "height_mm": row.get("height_mm"),
            "category": row.get("category"),
            "category_full": row.get("category_full"),
            "product_group": row.get("product_group"),
            "source_csv": 0 if pd.isna(row.get("source_csv")) else 1,
            "source_excel": 0 if pd.isna(row.get("source_excel")) else 1,
        }

        images = image_urls_from_row(pd.Series(row))
        upsert_product(unified, images)
        count += 1

    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        matched = conn.execute("SELECT COUNT(*) FROM products WHERE source_csv=1 AND source_excel=1").fetchone()[0]
        only_csv = conn.execute("SELECT COUNT(*) FROM products WHERE source_csv=1 AND source_excel=0").fetchone()[0]
        only_excel = conn.execute("SELECT COUNT(*) FROM products WHERE source_csv=0 AND source_excel=1").fetchone()[0]
        images = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]

    table = Table(title="SP Tools import resultaat")
    table.add_column("Onderdeel")
    table.add_column("Aantal", justify="right")
    table.add_row("Totaal producten", str(total))
    table.add_row("Gematcht CSV + Excel", str(matched))
    table.add_row("Alleen CSV", str(only_csv))
    table.add_row("Alleen Excel", str(only_excel))
    table.add_row("Afbeeldingen", str(images))
    console.print(table)

    Path("data/output").mkdir(parents=True, exist_ok=True)
    Path("data/output/import_audit_summary.txt").write_text(
        f"""SP Tools import audit

Totaal producten: {total}
Gematcht CSV + Excel: {matched}
Alleen CSV: {only_csv}
Alleen Excel: {only_excel}
Afbeeldingen: {images}
""",
        encoding="utf-8",
    )
    log("INFO", f"SP Tools import voltooid. Totaal: {total}")


if __name__ == "__main__":
    run_import()
