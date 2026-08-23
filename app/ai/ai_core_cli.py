from __future__ import annotations

import argparse
import json

from app.services.product_core_service import ProductCoreService
from app.ai.prompt_engine.prompt_builder import PromptBuilder
from app.ai.validators.ai_response_validator import AIResponseValidator
from app.ai.providers.openai_provider import OpenAIProvider
from app.services.product_service import ProductService


def build_prompt(sku: str):
    product = ProductCoreService.get_product_record(sku)
    if not product:
        print(f"Product niet gevonden: {sku}")
        return

    print(PromptBuilder.build_product_prompt(product))


def generate_ai_content(sku: str):
    product = ProductCoreService.get_product_record(sku)
    if not product:
        print(f"Product niet gevonden: {sku}")
        return

    prompt = PromptBuilder.build_product_prompt(product)
    provider = OpenAIProvider()

    print("OpenAI wordt aangeroepen...\n")

    raw = provider.generate_json(prompt)

    valid, data, errors = AIResponseValidator.parse_and_validate(raw)

    if valid:
        print("✅ AI JSON is geldig.\n")
        print(json.dumps(data, ensure_ascii=False, indent=2))

        saved = ProductService.mark_ai_generated(
            sku=sku,
            ai_title=data.get("title"),
            html_description=data.get("html_description"),
        )

        if saved:
            print("\n✅ AI-resultaat opgeslagen in database.")
        else:
            print("\n❌ Opslaan mislukt.")
    else:
        print("❌ AI JSON is ongeldig.")
        for error in errors:
            print(f"- {error}")
        print("\nRuwe output:\n")
        print(raw)


def validate_file(path: str):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    valid, data, errors = AIResponseValidator.parse_and_validate(raw)

    if valid:
        print("AI JSON is geldig.")
        print(json.dumps(data, ensure_ascii=False, indent=2))

        saved = ProductService.mark_ai_generated(
            sku=sku,
            ai_title=data.get("title"),
            html_description=data.get("html_description"),
        )

        if saved:
            print("\n✅ AI-resultaat opgeslagen in database.")
        else:
            print("\n❌ Opslaan mislukt.")
    else:
        print("AI JSON is ongeldig:")
        for error in errors:
            print(f"- {error}")


def main():
    parser = argparse.ArgumentParser(description="Weldingshop PIM AI Core CLI")
    parser.add_argument("sku", nargs="?", help="SKU")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--validate-json")

    args = parser.parse_args()

    if args.validate_json:
        validate_file(args.validate_json)
        return

    if args.sku and args.generate:
        generate_ai_content(args.sku)
        return

    if args.sku:
        build_prompt(args.sku)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
