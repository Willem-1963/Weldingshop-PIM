"""Read-only Shopify schema probe for native purchase-order suppliers."""

import json

from app.shopify.client import ShopifyClient


def main() -> None:
    client = ShopifyClient.from_settings()
    data = client.graphql(
        """
        query PimSupplierSchema {
          query: __type(name: "QueryRoot") {
            fields { name args { name type { kind name ofType { kind name } } } }
          }
          mutation: __type(name: "Mutation") {
            fields { name args { name type { kind name ofType { kind name } } } }
          }
        }
        """
    )
    result = {}
    for group in ("query", "mutation"):
        result[group] = [
            field
            for field in (data.get(group) or {}).get("fields") or []
            if "supplier" in field["name"].lower()
            or "purchaseorder" in field["name"].lower()
        ]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
