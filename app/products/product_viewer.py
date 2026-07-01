from __future__ import annotations

import argparse
from pathlib import Path

from app.services.product_service import ProductService, ProductDetails


OUTPUT_DIR = Path("data/output/product_reports")


def yes_no(value: bool) -> str:
    return "ja" if value else "nee"


def print_snapshot(snapshot: ProductDetails) -> None:
    product = snapshot.product

    print("\nWELDINGSHOP PIM - PRODUCT VIEWER")
    print("=" * 60)
    print(f"SKU:                  {product.sku}")
    print(f"EAN:                  {product.ean or '-'}")
    print(f"Leverancier:          {product.supplier or '-'}")
    print(f"Merk:                 {product.brand or '-'}")
    print(f"Titel bron:           {product.source_title or '-'}")
    print(f"Titel AI:             {product.ai_title or '-'}")
    print(f"Prijs:                {product.price if product.price is not None else '-'}")
    print(f"Gewicht:              {product.weight if product.weight is not None else '-'}")
    print(f"Producttype:          {product.product_type or '-'}")
    print(f"Categorie:            {product.category or '-'}")
    print(f"Shopify status:       {product.shopify_status or '-'}")
    print(f"Inventory policy:     {product.inventory_policy or '-'}")
    print(f"AI gegenereerd:       {yes_no(bool(product.ai_generated))}")
    print(f"Shopify geëxporteerd: {yes_no(bool(product.shopify_exported))}")
    print(f"Afbeeldingen:         {len(snapshot.images)}")
    print(f"Specificaties:        {len(snapshot.specifications)}")
    print("=" * 60)

    if product.source_description:
        print("\nBronbeschrijving:")
        print(product.source_description[:1000])

    if product.html_description:
        print("\nHTML beschrijving:")
        print(product.html_description[:1000])

    if snapshot.images:
        print("\nAfbeeldingen:")
        for image in snapshot.images[:10]:
            print(f"{image.position}. {image.url}")

    if snapshot.specifications:
        print("\nSpecificaties:")
        for spec in snapshot.specifications[:25]:
            print(f"- {spec.name}: {spec.value}")


def export_markdown(snapshot: ProductDetails) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    product = snapshot.product
    path = OUTPUT_DIR / f"{product.sku}.md"

    lines = [
        f"# {product.sku} - {product.source_title or product.ai_title or ''}",
        "",
        f"**EAN:** {product.ean or '-'}",
        f"**Leverancier:** {product.supplier or '-'}",
        f"**Merk:** {product.brand or '-'}",
        f"**Prijs:** {product.price if product.price is not None else '-'}",
        f"**Gewicht:** {product.weight if product.weight is not None else '-'}",
        f"**Producttype:** {product.product_type or '-'}",
        f"**Categorie:** {product.category or '-'}",
        f"**Shopify status:** {product.shopify_status or '-'}",
        f"**Inventory policy:** {product.inventory_policy or '-'}",
        "",
        "## Bronbeschrijving",
        product.source_description or "-",
        "",
        "## HTML beschrijving",
        product.html_description or "-",
        "",
        "## Afbeeldingen",
    ]

    if snapshot.images:
        for image in snapshot.images:
            lines.append(f"- {image.url}")
    else:
        lines.append("-")

    lines.extend(["", "## Specificaties"])
    if snapshot.specifications:
        for spec in snapshot.specifications:
            lines.append(f"- **{spec.name}:** {spec.value}")
    else:
        lines.append("-")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_search(query: str, limit: int) -> None:
    service = ProductService()
    products = service.search(query=query, limit=limit)

    print(f"\nZoekresultaten voor: {query}")
    print("=" * 60)
    for product in products:
        print(f"{product.sku} | {product.ean or '-'} | {product.source_title or product.ai_title or '-'}")
    print("=" * 60)
    print(f"Aantal getoond: {len(products)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Weldingshop PIM product viewer")
    parser.add_argument("sku", nargs="?", help="SKU om te bekijken")
    parser.add_argument("--search", help="Zoekterm voor SKU, EAN of titel")
    parser.add_argument("--limit", type=int, default=20, help="Maximaal aantal zoekresultaten")
    parser.add_argument("--export", action="store_true", help="Exporteer product naar Markdown")
    args = parser.parse_args()

    service = ProductService()

    if args.search:
        run_search(args.search, args.limit)
        return

    if not args.sku:
        parser.error("Geef een SKU op of gebruik --search")

    snapshot = service.get_details_by_sku(args.sku)
    if not snapshot:
        print(f"Product niet gevonden: {args.sku}")
        return

    print_snapshot(snapshot)

    if args.export:
        path = export_markdown(snapshot)
        print(f"\nMarkdown rapport aangemaakt: {path}")


if __name__ == "__main__":
    main()
