"""Read-only probe of Shopify Companies for supplier-route validation."""

import json

from app.shopify.client import ShopifyClient


def main() -> None:
    client = ShopifyClient.from_settings()
    schema = client.graphql(
        """
        query PimCompanySchema {
          company: __type(name: "Company") {
            fields { name type { kind name ofType { kind name } } }
          }
          query: __type(name: "QueryRoot") { fields { name } }
          mutation: __type(name: "Mutation") { fields { name } }
        }
        """
    )
    company_fields = {
        field["name"] for field in (schema.get("company") or {}).get("fields") or []
    }
    result = {
        "company_fields": sorted(company_fields),
        "company_queries": sorted(
            field["name"]
            for field in (schema.get("query") or {}).get("fields") or []
            if "compan" in field["name"].lower()
        ),
        "company_mutations": sorted(
            field["name"]
            for field in (schema.get("mutation") or {}).get("fields") or []
            if "compan" in field["name"].lower()
        ),
    }
    if "id" in company_fields and "name" in company_fields:
        sample = client.graphql(
            """
            query PimCompanySample {
              companies(first: 10) {
                nodes { id name createdAt updatedAt }
                pageInfo { hasNextPage endCursor }
              }
            }
            """
        )
        result["sample"] = (sample.get("companies") or {}).get("nodes") or []
    unstable = ShopifyClient(
        client.shop_domain, client.access_token, api_version="unstable"
    ).graphql(
        """
        query PimInventorySupplierSchema {
          supplier: __type(name: "InventorySupplier") {
            fields { name }
          }
          query: __type(name: "QueryRoot") { fields { name } }
          mutation: __type(name: "Mutation") { fields { name } }
        }
        """
    )
    result["inventory_supplier_fields"] = sorted(
        field["name"]
        for field in (unstable.get("supplier") or {}).get("fields") or []
    )
    result["inventory_supplier_queries"] = sorted(
        field["name"]
        for field in (unstable.get("query") or {}).get("fields") or []
        if "supplier" in field["name"].lower()
        or "purchaseorder" in field["name"].lower()
    )
    result["inventory_supplier_mutations"] = sorted(
        field["name"]
        for field in (unstable.get("mutation") or {}).get("fields") or []
        if "supplier" in field["name"].lower()
        or "purchaseorder" in field["name"].lower()
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
