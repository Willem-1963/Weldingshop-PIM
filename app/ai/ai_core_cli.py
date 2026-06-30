import argparse
import json

from app.services.product_core_service import ProductCoreService
from app.ai.prompt_engine.prompt_builder import PromptBuilder
from app.ai.validators.ai_response_validator import AIResponseValidator


def build_prompt(sku: str):
    product = ProductCoreService.get_product_record(sku)
    if not product:
        print(f"Product niet gevonden: {sku}")
        return

    print(PromptBuilder.build_product_prompt(product))


def validate_file(path: str):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    valid, data, errors = AIResponseValidator.parse_and_validate(raw)
    if valid:
        print("AI JSON is geldig.")
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("AI JSON is ongeldig:")
        for error in errors:
            print(f"- {error}")


def main():
    parser = argparse.ArgumentParser(description="Weldingshop PIM AI Core CLI")
    parser.add_argument("sku", nargs="?", help="SKU waarvoor een prompt moet worden gebouwd")
    parser.add_argument("--validate-json", help="Pad naar JSON-bestand om te valideren")

    args = parser.parse_args()

    if args.validate_json:
        validate_file(args.validate_json)
        return

    if args.sku:
        build_prompt(args.sku)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
