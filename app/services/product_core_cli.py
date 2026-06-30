import argparse
from app.services.product_core_service import ProductCoreService


def print_record(record):
    print("\nPRODUCT CORE RECORD")
    print("=" * 70)
    print(f"SKU:                  {record.sku}")
    print(f"Titel:                {record.display_title}")
    print(f"EAN:                  {record.ean or '-'}")
    print(f"Leverancier:          {record.supplier or '-'}")
    print(f"Merk:                 {record.brand or '-'}")
    print(f"Prijs:                {record.price if record.price is not None else '-'}")
    print(f"Gewicht:              {record.weight if record.weight is not None else '-'}")
    print(f"Producttype:          {record.product_type or '-'}")
    print(f"Categorie:            {record.category or '-'}")
    print(f"AI gegenereerd:       {'ja' if record.ai_generated else 'nee'}")
    print(f"Shopify geëxporteerd: {'ja' if record.shopify_exported else 'nee'}")
    print(f"Shopify klaar:        {'ja' if record.is_shopify_ready else 'nee'}")
    print(f"Afbeeldingen:         {record.image_count}")
    print(f"Specificaties:        {record.specification_count}")
    print("=" * 70)

    if record.primary_image:
        print("\nHoofdafbeelding:")
        print(record.primary_image.url)

    if record.html_description:
        print("\nHTML beschrijving:")
        print(record.html_description[:1000])
    elif record.source_description:
        print("\nBronbeschrijving:")
        print(record.source_description[:1000])


def main():
    parser = argparse.ArgumentParser(description="Product Core CLI")
    parser.add_argument("sku", nargs="?", help="SKU/referentie van het product")
    parser.add_argument("--search", help="Zoekterm")
    parser.add_argument("--missing-content", action="store_true", help="Toon producten zonder beschrijving")
    parser.add_argument("--shopify-ready", action="store_true", help="Toon producten die klaar lijken voor Shopify")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    if args.search:
        records = ProductCoreService.search_records(args.search, args.limit)
        print(f"\nZoekresultaten voor: {args.search}")
        print("=" * 70)
        for record in records:
            print(f"{record.sku:<25} | {record.display_title[:80]} | images: {record.image_count}")
        print(f"Aantal getoond: {len(records)}")
        return

    if args.missing_content:
        records = ProductCoreService.get_missing_content_records(args.limit)
        print("\nProducten zonder content")
        print("=" * 70)
        for record in records:
            print(f"{record.sku:<25} | {record.display_title[:80]}")
        return

    if args.shopify_ready:
        records = ProductCoreService.get_shopify_ready_records(args.limit)
        print("\nProducten klaar voor Shopify")
        print("=" * 70)
        for record in records:
            print(f"{record.sku:<25} | {record.display_title[:80]} | images: {record.image_count}")
        return

    if not args.sku:
        parser.error("Geef een SKU op of gebruik --search / --missing-content / --shopify-ready")

    record = ProductCoreService.get_product_record(args.sku)
    if not record:
        print(f"Product niet gevonden: {args.sku}")
        return

    print_record(record)


if __name__ == "__main__":
    main()
