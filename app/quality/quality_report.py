from app.core.database import SessionLocal
from app.models.product import Product
from app.models.image import ProductImage


def main():
    session = SessionLocal()

    products = session.query(Product).all()

    missing_price = []
    missing_ean = []
    missing_description = []
    missing_images = []
    missing_weight = []

    for product in products:
        if product.price is None:
            missing_price.append(product.sku)

        if not product.ean:
            missing_ean.append(product.sku)

        if not product.source_description:
            missing_description.append(product.sku)

        if product.weight is None:
            missing_weight.append(product.sku)

        image_count = session.query(ProductImage).filter(ProductImage.sku == product.sku).count()
        if image_count == 0:
            missing_images.append(product.sku)

    print("\nQUALITY REPORT")
    print("=" * 40)
    print(f"Totaal producten:          {len(products)}")
    print(f"Ontbrekende prijs:         {len(missing_price)}")
    print(f"Ontbrekende EAN:           {len(missing_ean)}")
    print(f"Ontbrekende beschrijving:  {len(missing_description)}")
    print(f"Ontbrekende afbeeldingen:  {len(missing_images)}")
    print(f"Ontbrekend gewicht:        {len(missing_weight)}")
    print("=" * 40)

    session.close()


if __name__ == "__main__":
    main()
