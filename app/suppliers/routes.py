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
    "rhodius-abrasives-gmbh": SupplierRoute(
        "rhodius-abrasives-gmbh", "rhodius-abrasives-gmbh.sqlite",
        "rhodius-abrasives-gmbh", "rhodius-abrasives-gmbh",
        "rhodius-abrasives-gmbh", shopify_vendor_names=("Rhodius",),
    ),
    "certilas": SupplierRoute(
        "certilas", "certilas.sqlite", "certilas", "certilas", "certilas",
        supports_product_families=True,
        uses_certilas_filters=True,
        uses_certilas_dealer_pricelist=True,
        applies_alloy_surcharges=True,
        shopify_vendor_names=(
            "Certilas", "Certilas Nederland BV", "Certilas-Legeringstoeslag",
            "Certilas-LME",
        ),
    ),
    "edge": SupplierRoute(
        "edge", "edge.sqlite", "safety_jogger", "safety_jogger", "safety_jogger",
        shopify_vendor_names=("Safety Jogger", "Pres-Safety", "presidentsafety"),
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

SHOPIFY_VENDOR_ALIASES = {
    "corlido-project-supply-b-v": ("Corlido",),
    "dimax-international-poland-sp-z-o-o": (
        "Dimax - Konner & Sohnen", "Könner & Söhnen",
    ),
    "euroboor-b-v": ("Euroboor",),
    "gasflessen-nl": (
        "Benegas", "Primagaz", "Air-Liquide-Gas", "Air Liquide",
        "Airliquide Welding", "Campinggaz",
    ),
    "gasflessen-nl-72784841": (
        "Benegas", "Primagaz", "Air-Liquide-Gas", "Air Liquide",
        "Airliquide Welding", "Campinggaz",
    ),
    "h-l-products": ("H&L Products",),
    "harder-lastechniek": (
        "Harder Lastechniek", "Harder LasTechniek", "Harder", "Harder - Trotec",
    ),
    "hatek": ("Hatek", "HATEK", "Promotech", "Promotech/HATEK"),
    "hwts-heuvels-welding-tool-service": ("HWTS",),
    "ibs-scherer-gmbh": ("IBS",),
    "karsten-tenten-b-v-tentworld-unlimited": ("Karsten Tenten",),
    "kentie": ("Kentie", "Kentie Apparatenfabriek B.V."),
    "lyreco-nederland-b-v": ("Lyreco",),
    "macknights": ("Macknights", "Macknight", "mack"),
    "metaaltechniek-handelsonderneming-b-v": (
        "Metaaltechniek Handelsonderneming B.V.", "Metaaltechniek",
    ),
    "nische-europe-b-v": ("Nische",),
    "parker-torchology-b-v": ("Parker",),
    "pipeq-pipework-equipment": ("Pipeq Pipework Equipment", "Pipeq"),
    "stv-las-en-snijtechniek-b-v": ("STV",),
    "telwin": ("Telwin",),
    "vynckier": ("Vynckier", "Vynckiers"),
    "weldas-europe-b-v": ("Weldas",),
    "wilkinson-star-ltd": ("Wilkinson Star",),
}


def supplier_route(slug: str) -> SupplierRoute:
    key = str(slug or "").strip().casefold()
    if key in ROUTES:
        return ROUTES[key]
    # Ook een nieuw aangemaakte leverancier krijgt direct een fysiek eigen
    # database en route; nooit een gedeelde producttabel.
    return SupplierRoute(
        key, f"{key}.sqlite", key, key, key,
        shopify_vendor_names=SHOPIFY_VENDOR_ALIASES.get(key, ()),
    )
