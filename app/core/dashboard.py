from sqlalchemy import func

from app.core.database import SessionLocal
from app.models.product import Product
from app.models.image import ProductImage


def main():
    session = SessionLocal()

    total_products = session.query(Product).count()
    total_images = session.query(ProductImage).count()

    with_ean = session.query(Product).filter(Product.ean.isnot(None), Product.ean != "").count()
    with_price = session.query(Product).filter(Product.price.isnot(None)).count()
    with_description = session.query(Product).filter(Product.source_description.isnot(None), Product.source_description != "").count()
    with_images = session.query(ProductImage.sku).distinct().count()

    print("\nWELDINGSHOP PRODUCT PLATFORM")
    print("=" * 40)
    print(f"Totaal producten:        {total_products}")
    print(f"Totaal afbeeldingen:     {total_images}")
    print(f"Producten met EAN:       {with_ean}")
    print(f"Producten met prijs:     {with_price}")
    print(f"Producten met tekst:     {with_description}")
    print(f"Producten met afbeelding:{with_images}")
    print("=" * 40)

    print("\nEerste 10 producten:")
    products = session.query(Product).order_by(Product.sku).limit(10).all()

    for p in products:
        img_count = session.query(ProductImage).filter(ProductImage.sku == p.sku).count()
        print(f"{p.sku} | {p.source_title} | €{p.price} | images: {img_count}")

    session.close()


if __name__ == "__main__":
    main()
