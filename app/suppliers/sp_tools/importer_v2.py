from pathlib import Path
import pandas as pd
from sqlalchemy import delete

from app.core.database import SessionLocal, init_database
from app.models.product import Product
from app.models.image import ProductImage


BASE_DIR = Path(__file__).resolve().parents[3]
INPUT_DIR = BASE_DIR / "data" / "input"

EXCEL_FILE = INPUT_DIR / "SP Tools bruto prijslijst 2026 v3.xlsx"
CSV_FILE = INPUT_DIR / "productdetails.csv"


def clean(value):
    if pd.isna(value):
        return None
    value = str(value).strip()
    return value if value else None


def to_float(value):
    if pd.isna(value) or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def load_excel():
    df = pd.read_excel(EXCEL_FILE, header=1)
    df = df.rename(columns={
        "Item Number": "sku",
        "Desciption(English)": "title_en",
        "Description(Dutch)": "title_nl",
        "Product Group": "product_group",
        "2026 AVP €": "price",
        "weight gross in gram": "weight_g",
        "Barcode": "ean",
        "IMAGE": "image",
    })
    df["sku"] = df["sku"].astype(str).str.strip()
    return df


def load_csv():
    df = pd.read_csv(CSV_FILE, sep=None, engine="python")
    df.columns = [c.strip().replace("\ufeff", "") for c in df.columns]
    df = df.rename(columns={
        "reference": "sku",
        "name": "csv_name",
        "price": "csv_price",
        "weight": "csv_weight",
        "ean": "csv_ean",
        "description": "csv_description",
        "category": "csv_category",
        "category_full": "csv_category_full",
        "articleGroup": "csv_article_group",
    })
    df["sku"] = df["sku"].astype(str).str.strip()
    return df


def collect_images(row):
    images = []

    for col in ["primary_image", "thumbnail", "Images"] + [f"image_{i}" for i in range(12)]:
        if col in row.index:
            value = clean(row.get(col))
            if value and value.lower() != "nan":
                parts = [p.strip() for p in value.replace("|", ",").split(",")]
                for part in parts:
                    if part.startswith("http") and part not in images:
                        images.append(part)

    return images


def import_sp_tools():
    init_database()

    excel = load_excel()
    csv = load_csv()

    excel_map = {str(row["sku"]).strip(): row for _, row in excel.iterrows() if clean(row.get("sku"))}
    csv_map = {str(row["sku"]).strip(): row for _, row in csv.iterrows() if clean(row.get("sku"))}

    all_skus = sorted(set(excel_map.keys()) | set(csv_map.keys()))

    session = SessionLocal()

    imported = 0
    matched = 0
    only_excel = 0
    only_csv = 0
    image_count = 0

    for sku in all_skus:
        excel_row = excel_map.get(sku)
        csv_row = csv_map.get(sku)

        if excel_row is not None and csv_row is not None:
            matched += 1
        elif excel_row is not None:
            only_excel += 1
        elif csv_row is not None:
            only_csv += 1

        title = None
        description = None
        price = None
        ean = None
        weight = None
        category = None
        product_type = None

        if excel_row is not None:
            title = clean(excel_row.get("title_nl")) or clean(excel_row.get("title_en"))
            price = to_float(excel_row.get("price"))
            ean = clean(excel_row.get("ean"))
            product_type = clean(excel_row.get("product_group"))
            weight_g = to_float(excel_row.get("weight_g"))
            if weight_g:
                weight = weight_g / 1000

        if csv_row is not None:
            title = clean(csv_row.get("csv_name")) or title
            description = clean(csv_row.get("csv_description"))
            ean = clean(csv_row.get("csv_ean")) or ean
            category = clean(csv_row.get("csv_category_full")) or clean(csv_row.get("csv_category"))
            product_type = clean(csv_row.get("csv_article_group")) or product_type
            price = to_float(csv_row.get("csv_price")) or price
            weight = to_float(csv_row.get("csv_weight")) or weight

        existing = session.query(Product).filter(Product.sku == sku).first()

        if existing:
            product = existing
        else:
            product = Product(sku=sku)
            session.add(product)

        product.ean = ean
        product.supplier = "SP Tools"
        product.brand = "SP Tools"
        product.source_title = title
        product.source_description = description
        product.price = price
        product.weight = weight
        product.product_type = product_type
        product.category = category
        product.shopify_status = "draft"
        product.inventory_policy = "continue"

        session.execute(delete(ProductImage).where(ProductImage.sku == sku))

        if csv_row is not None:
            images = collect_images(csv_row)
            for pos, image_url in enumerate(images, start=1):
                session.add(ProductImage(
                    sku=sku,
                    url=image_url,
                    position=pos,
                    alt_text=title,
                ))
                image_count += 1

        imported += 1

    session.commit()
    session.close()

    print("SP Tools import v2 klaar")
    print(f"Totaal producten: {imported}")
    print(f"Gematcht Excel + CSV: {matched}")
    print(f"Alleen Excel: {only_excel}")
    print(f"Alleen CSV: {only_csv}")
    print(f"Afbeeldingen: {image_count}")


if __name__ == "__main__":
    import_sp_tools()
