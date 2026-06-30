import argparse
from app.services.image_service import ImageService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sku")
    args = parser.parse_args()

    images = ImageService.get_images_by_sku(args.sku)

    print(f"\nAfbeeldingen voor SKU: {args.sku}")
    print("=" * 60)

    if not images:
        print("Geen afbeeldingen gevonden.")
        return

    for image in images:
        print(f"{image.position}. {image.url}")

    primary = ImageService.get_primary_image(args.sku)
    if primary:
        print("\nHoofdafbeelding:")
        print(primary.url)


if __name__ == "__main__":
    main()