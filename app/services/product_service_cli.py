from __future__ import annotations

import argparse

from app.services.product_service import ProductService


def show_product(sku: str) -> None:
    details = ProductService.get_details_by_sku(sku)
    if details is None:
        print(f"Product niet gevonden: {sku}")
        return

    product = details.product

    print("\nPRODUCT SERVICE VIEW")
    print("=" * 60)
    print(f"SKU:              {product.sku}")
    print(f"EAN:              {product.ean or '-'}")
    print(f"Titel bron:       {product.source_title or '-'}")
    print(f"Titel AI:         {product.ai_title or '-'}")
    print(f"Prijs:            {product.price if product.price is not None else '-'}")
    print(f"Categorie:        {product.category or '-'}")
    print(f"Producttype:      {product.product_type or '-'}")
    print(f"AI gegenereerd:   {'ja' if product.ai_generated else 'nee'}")
    print(f"Afbeeldingen:     {len(details.images)}")
    print(f"Specificaties:    {len(details.specifications)}")
    print("=" * 60)

    if details.images:
        print("\nAfbeeldingen:")
        for image in details.images[:10]:
            print(f"{image.position}. {image.url}")


def search_products(query: str, limit: int) -> None:
    products = ProductService.search(query=query, limit=limit)

    print(f"\nZoekresultaten voor: {query}")
    print("=" * 60)
    for product in products:
        print(f"{product.sku:20} | {product.source_title or product.ai_title or '-'}")
    print("=" * 60)
    print(f"Aantal getoond: {len(products)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Product Service CLI")
    parser.add_argument("sku", nargs="?", help="SKU om te tonen")
    parser.add_argument("--search", help="Zoekterm")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--missing-description", action="store_true")
    parser.add_argument("--missing-images", action="store_true")

    args = parser.parse_args()

    if args.search:
        search_products(args.search, args.limit)
        return

    if args.missing_description:
        products = ProductService.list_missing_description(limit=args.limit)
        for product in products:
            print(f"{product.sku} | {product.source_title or '-'}")
        return

    if args.missing_images:
        products = ProductService.list_missing_images(limit=args.limit)
        for product in products:
            print(f"{product.sku} | {product.source_title or '-'}")
        return

    if args.sku:
        show_product(args.sku)
        return

    print(f"Totaal producten: {ProductService.count_all()}")


if __name__ == "__main__":
    main()
