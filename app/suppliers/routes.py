from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupplierRoute:
    slug: str
    database_filename: str
    source_adapter: str
    pim_adapter: str
    shopify_adapter: str
    supports_product_families: bool = False
    uses_certilas_filters: bool = False
    uses_certilas_dealer_pricelist: bool = False
    applies_alloy_surcharges: bool = False
    preserves_enrichment: bool = False
    infers_category_path: bool = False
    localizes_customer_content_to_dutch: bool = False
    shopify_vendor_names: tuple[str, ...] = ()
    website_product_adapter: str = "official_website"
    category_hierarchy_source: str = "supplier_feed"


ROUTES = {
    "certilas": SupplierRoute(
        "certilas", "certilas.sqlite", "certilas", "certilas", "certilas",
        supports_product_families=True,
        uses_certilas_filters=True,
        uses_certilas_dealer_pricelist=True,
        applies_alloy_surcharges=True,
        shopify_vendor_names=("Certilas", "Certilas Nederland BV"),
    ),
    "edge": SupplierRoute(
        "edge", "edge.sqlite", "safety_jogger", "safety_jogger", "safety_jogger",
    ),
    "sp-tools": SupplierRoute(
        "sp-tools", "sp-tools.sqlite", "sp_tools", "sp_tools", "sp_tools",
        shopify_vendor_names=("SP Tools",),
    ),
    "tecweld": SupplierRoute(
        "tecweld", "tecweld.sqlite", "tecweld", "tecweld", "tecweld",
        preserves_enrichment=True,
        localizes_customer_content_to_dutch=True,
        shopify_vendor_names=("Tecweld", "Sherman"),
    ),
    "valkenpower": SupplierRoute(
        "valkenpower", "valkenpower.sqlite", "valkenpower", "valkenpower",
        "valkenpower",
        preserves_enrichment=True,
        # Valkenpower's feed contains no dependable hierarchy. The category
        # breadcrumb on the matching official product page is authoritative.
        # Never infer it from a SKU family, title or another supplier.
        infers_category_path=False,
        shopify_vendor_names=("Valkenpower",),
        category_hierarchy_source="official_product_page_breadcrumb",
    ),
}


def supplier_route(slug: str) -> SupplierRoute:
    key = str(slug or "").strip().casefold()
    if key in ROUTES:
        return ROUTES[key]
    # Ook een nieuw aangemaakte leverancier krijgt direct een fysiek eigen
    # database en route; nooit een gedeelde producttabel.
    return SupplierRoute(key, f"{key}.sqlite", key, key, key)
