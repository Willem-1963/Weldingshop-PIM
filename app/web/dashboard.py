from __future__ import annotations

import io
import json
import os
import re
import sys
import html
import hashlib
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup

# Streamlit voegt alleen de map van dit script gegarandeerd aan sys.path toe.
# Maak imports daarom onafhankelijk van de map waaruit de service is gestart.
PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.suppliers.hub import (
    DEFAULT_MAPPING,
    apply_source_transformations,
    cleanup_missing_supplier_products,
    get_supplier,
    get_supplier_product,
    import_records,
    init_registry,
    list_products,
    list_suppliers,
    read_source,
    save_excel_header_row,
    save_inventory_mapping,
    save_missing_product_policy,
    save_shopify_field_mapping,
    save_shopify_metafield_mapping,
    save_source_field_mapping,
    save_source_field_mapping_locks,
    save_source_transformations,
    save_unified_source_mappings,
    save_shopify_export,
    save_supplier,
    search_supplier_products,
    shopify_csv_bytes,
    suggest_source_field_mapping,
    supplier_stats,
    supplier_database_cleanup_preview,
)
from app.suppliers.invoice_catalog import (
    invoice_evidence_counts,
    list_product_invoice_evidence,
)
from app.server_backup import (
    DEFAULT_BACKUP_DIR,
    backup_storage_summary,
    create_server_backup,
    estimate_server_backup_size,
    list_server_backups,
    verify_server_backup,
)
from app.shopify_backup import (
    DEFAULT_SHOPIFY_BACKUP_DIR,
    create_shopify_catalog_backup,
    get_product_from_shopify_backup,
    list_shopify_catalog_backups,
    list_products_in_shopify_backup,
    restore_product_from_shopify_backup,
    verify_shopify_catalog_backup,
)
from app.shopify.client import (
    DEFAULT_API_VERSION,
    get_metafield_definitions,
    ensure_derived_inventory_webhooks,
    get_shopify_collections,
    get_shopify_locations,
    get_shopify_settings,
    get_shopify_writable_fields,
    preview_supplier_matches,
    save_shopify_settings,
    test_shopify_connection,
)
from app.shopify.sync import (
    add_pim_product_as_shopify_variant,
    get_shopify_variant_target,
    upload_pim_product_draft,
    upload_test_product,
)
from app.shopify.family_rebuild import (
    FINAL_CONFIRMATION,
    create_family_rebuild_backup_and_plan,
    finalize_family_rebuild,
    get_family_rebuild_state,
    start_family_rebuild,
)
from app.suppliers.discounts import (
    BASIS_FIELDS,
    MATCH_FIELDS,
    SALES_RULE_TYPES,
    apply_purchase_costs,
    apply_sales_prices,
    delete_discount_rule,
    delete_sales_price_rule,
    distinct_match_values,
    list_discount_rules,
    list_sales_price_rules,
    lookup_discount_product,
    preview_purchase_costs,
    preview_sales_prices,
    save_discount_rule,
    save_scoped_sales_price_rule,
    save_sales_price_rule,
    set_discount_rule_basis,
    set_discount_rule_enabled,
    set_sales_price_rule_enabled,
    update_sales_price_rule,
)
from app.suppliers.scheduler import (
    ACTIVE_JOB_STATUSES,
    DEFAULT_TIMEZONE,
    FREQUENCIES,
    get_background_sync,
    list_sync_history,
    save_sync_schedule,
    start_background_sync,
    stop_background_sync,
)
from app.suppliers.complementary_products import (
    MAX_LINKS as COMPLEMENTARY_MAX_LINKS,
    build_complementary_preview,
    complementary_stats,
    synchronize_complementary_products,
)
from app.suppliers.valkenpower_collection_index import (
    ACTIVE as COLLECTION_INDEX_ACTIVE,
    collection_counts,
    collection_products,
    collection_index_status,
    official_breadcrumb_status,
    start_collection_index,
)
from app.suppliers.research import research_supplier_source
from app.suppliers.field_learning import (
    ai_field_mapping_proposals,
    learned_source_field_mapping,
    record_field_mapping_decisions,
)
from app.bundles import bundle_stats
from app.product_families import (
    get_or_create_product_families,
    rebuild_product_families,
)
from app.kentie_product_families import (
    create_kentie_shopify_family_draft,
    preview_kentie_shopify_removals,
    save_kentie_product_family,
    search_kentie_family_candidates,
)
from app.suppliers.website_enrichment import (
    catalogue_enrichment_status,
    research_certilas_family,
    start_catalogue_enrichment,
)
from app.suppliers.on_demand_import import import_official_website_product
from app.suppliers.enrichment_profiles import (
    enrichment_enabled,
    get_enrichment_profile,
    reset_enrichment_profile,
    run_profile_enrichment,
    save_enrichment_profile,
)
from app.suppliers.kentie_bulk_enrichment import (
    kentie_enrichment_status,
    restart_kentie_enrichment,
    resume_kentie_enrichment,
    start_kentie_enrichment,
    stop_kentie_enrichment,
)
from app.suppliers.tecweld_bulk_enrichment import (
    restart_tecweld_enrichment,
    resume_tecweld_enrichment,
    stop_tecweld_enrichment,
    tecweld_enrichment_status,
    start_tecweld_enrichment,
)
from app.suppliers.tecweld_dutch_chain import (
    start_product_translation,
    translation_status,
)
from app.suppliers.enrichment_recovery import (
    enrichment_recovery_status,
    resume_enrichment_recovery,
    start_enrichment_recovery,
    stop_enrichment_recovery,
)
from app.web.bundle_page import show_bundle_builder
from app.web.label_page import show_label_page
from app.product_maker_standalone import show_product_maker


st.set_page_config(page_title="Weldingshop PIM", page_icon="🧰", layout="wide")
init_registry()

st.markdown(
    """
    <style>
    div[data-baseweb="tab-list"] {
        gap: 0.45rem;
        border-bottom: 2px solid #d8dee6;
    }
    button[data-baseweb="tab"] {
        min-height: 3.2rem;
        padding: 0.7rem 1.25rem;
        border: 1px solid #cbd3dc;
        border-bottom: 0;
        border-radius: 0.65rem 0.65rem 0 0;
        background: #f1f4f7;
        font-size: 1.12rem;
        font-weight: 700;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #ffffff;
        border-color: #c62828;
        background: #c62828;
    }
    button[data-baseweb="tab"] p {
        font-size: inherit;
        font-weight: inherit;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def show_linked_field_status(widget_key: str, linked: bool) -> None:
    """Kleur een gekoppelde bronveld-selectie herkenbaar groen."""
    if not linked:
        return
    selector = f'[class~="st-key-{widget_key}"]'
    st.markdown(
        f"""
        <style>
        {selector} [data-baseweb="select"] > div {{
            background-color: #dff3e4 !important;
            border-color: #46a35e !important;
            box-shadow: 0 0 0 1px #46a35e inset !important;
        }}
        {selector} label p {{
            color: #14532d !important;
            font-weight: 700 !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def source_record_product_name(
    record: dict,
    title_field: str,
    description_field: str = "",
) -> str:
    """Return the best available product name from an unanalyzed source row."""
    candidate_fields = [
        title_field,
        description_field,
        "Product description",
        "product_description",
        "description",
        "title",
        "name",
    ]
    for field in candidate_fields:
        if not field:
            continue
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return "Naamloos"


def show_fixed_field_status(widget_key: str, fixed: bool) -> None:
    """Kleur een handmatig vastgezette veldkoppeling blauw."""
    if not fixed:
        return
    selector = f'[class~="st-key-{widget_key}"]'
    st.markdown(
        f"""
        <style>
        {selector} [data-baseweb="select"] > div {{
            background-color: #dbeafe !important;
            border-color: #3b82f6 !important;
            box-shadow: 0 0 0 1px #3b82f6 inset !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def show_rules_button_status(widget_key: str, configured: bool) -> None:
    """Kleur een Regels-knop blauw zodra regels zijn opgeslagen."""
    if not configured:
        return
    selector = f'[class~="st-key-{widget_key}"]'
    st.markdown(
        f"""
        <style>
        {selector} button {{
            background-color: #2563eb !important;
            border-color: #1d4ed8 !important;
            color: #ffffff !important;
            font-weight: 700 !important;
        }}
        {selector} button:hover {{
            background-color: #1d4ed8 !important;
            border-color: #1e40af !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def show_save_button_status(widget_key: str, status: str) -> None:
    """Kleur een opslagknop rood bij wijzigingen en groen na opslag."""
    colors = {
        "changed": ("#dc2626", "#b91c1c", "#991b1b"),
        "saved": ("#16a34a", "#15803d", "#166534"),
    }
    if status not in colors:
        return
    background, border, hover = colors[status]
    selector = f'[class~="st-key-{widget_key}"]'
    st.markdown(
        f"""
        <style>
        {selector} button {{
            background-color: {background} !important;
            border-color: {border} !important;
            color: #ffffff !important;
            font-weight: 700 !important;
        }}
        {selector} button:hover {{
            background-color: {hover} !important;
            border-color: {hover} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


PROCESSING_TYPE_LABELS = {
    "none": "Geen bewerking",
    "text": "Tekst",
    "values": "Waarden / keuzelijst",
    "calculation": "Getal en berekening",
    "collection": "Samengesteld veld",
    "nested": "Genest veld",
    "stock": "Voorraad / stock",
    "images": "Afbeeldingen",
}


@st.cache_data(ttl=300, show_spinner=False)
def cached_shopify_locations() -> list[dict]:
    return get_shopify_locations()


@st.cache_data(ttl=300, show_spinner=False)
def cached_shopify_collections() -> list[dict]:
    return get_shopify_collections()


@st.cache_data(ttl=300, show_spinner=False)
def cached_server_backup_estimate() -> dict[str, int]:
    return estimate_server_backup_size()


def infer_processing_type(config: dict) -> str:
    configured = config.get("processing_type")
    if configured in PROCESSING_TYPE_LABELS:
        return configured
    if config.get("content_type") in {
        "primary_image", "additional_images",
    }:
        return "images"
    if config.get("calculation") or config.get("multiply_fields"):
        return "calculation"
    if config.get("field_collection") or config.get("custom_field"):
        return "collection"
    if config.get("nested_field"):
        return "nested"
    if config.get("value_map"):
        return "values"
    if any(config.get(key) for key in (
        "replacements", "find", "trim", "strip_html",
        "normalize_spaces", "remove_control", "decimal_comma_to_point",
        "case", "prefix", "suffix", "html_cleanup",
    )):
        return "text"
    return "none"


def transformation_has_rules(config: dict) -> bool:
    return any(
        value not in (None, "", False, [], {})
        for key, value in config.items()
        if key not in {
            "processing_type", "custom_field",
            "suggested_target_kind", "suggested_target",
        }
    )


def numeric_input_default(value: object) -> float:
    """Return a safe number when a calculation row changes field type."""
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def render_typed_transformation_rules(
    supplier_slug: str,
    source_field: str,
    available_fields: list[str],
    example_record: dict,
    processing_type: str,
    current: dict,
    transformations: dict,
    test_product_sku: str = "",
) -> None:
    proposed: dict = {"processing_type": processing_type}
    if current.get("custom_field"):
        proposed["custom_field"] = True
    for key in ("suggested_target_kind", "suggested_target"):
        if current.get(key):
            proposed[key] = current[key]
    output_field = ""
    rules_valid = True
    state_root = f"typed_rules_{supplier_slug}_{source_field}"

    if processing_type == "images":
        content_type = st.selectbox(
            "Gebruik als",
            ["primary_image", "additional_images"],
            index=(
                0 if current.get("content_type") == "primary_image"
                else 1
            ),
            format_func=lambda value: (
                "Hoofdafbeelding"
                if value == "primary_image"
                else "Aanvullende productafbeelding(en)"
            ),
            help=(
                "Een veld mag één URL, meerdere URL’s of een XML-lijst "
                "bevatten. Nieuwe regels, komma’s, puntkomma’s en | worden "
                "automatisch als scheidingsteken herkend."
            ),
        )
        proposed["content_type"] = content_type
        st.info(
            "Lege URL’s en duplicaten worden overgeslagen. Meerdere losse "
            "fotovel­den mogen ieder als Afbeeldingen worden ingesteld."
        )

    elif processing_type == "values":
        st.caption(
            "Zet vaste bronwaarden om. Eén omzetting per regel."
        )
        value_map_text = st.text_area(
            "Waarde omzetten",
            value="\n".join(
                f"{source} = {target}"
                for source, target in (
                    current.get("value_map") or {}
                ).items()
            ),
            placeholder="in stock = 1\nout of stock = 0",
        )
        value_map = {}
        for line in value_map_text.splitlines():
            if "=" in line:
                source, target = line.split("=", 1)
                if source.strip():
                    value_map[source.strip()] = target.strip()
        proposed["value_map"] = value_map

    elif processing_type == "text":
        current_html_cleanup = current.get("html_cleanup") or {}
        columns = st.columns(2)
        proposed["trim"] = columns[0].checkbox(
            "Begin- en eindspaties verwijderen",
            value=bool(current.get("trim", True)),
        )
        proposed["normalize_spaces"] = columns[1].checkbox(
            "Spaties normaliseren",
            value=bool(current.get("normalize_spaces")),
        )
        proposed["remove_control"] = columns[0].checkbox(
            "Onzichtbare stuurtekens verwijderen",
            value=bool(current.get("remove_control")),
        )
        proposed["strip_html"] = columns[1].checkbox(
            "HTML verwijderen",
            value=bool(current.get("strip_html")),
        )
        st.markdown("##### HTML-productomschrijving")
        cleanup_enabled = st.checkbox(
            "HTML-productomschrijving structureel opschonen",
            value=bool(current_html_cleanup.get("enabled")),
            help=(
                "Past dit alleen toe wanneer de bronwaarde HTML bevat. "
                "Gewone tekst blijft ongewijzigd."
            ),
        )
        cleanup_columns = st.columns(2)
        cleanup_remove_attributes = cleanup_columns[0].checkbox(
            "Leveranciersopmaak en attributen verwijderen",
            value=bool(
                current_html_cleanup.get("remove_attributes", True)
            ),
            disabled=not cleanup_enabled,
        )
        cleanup_remove_empty = cleanup_columns[1].checkbox(
            "Lege alinea’s en overbodige regels verwijderen",
            value=bool(current_html_cleanup.get("remove_empty", True)),
            disabled=not cleanup_enabled,
        )
        cleanup_breaks = cleanup_columns[0].checkbox(
            "Tekstblokken met regeleinden naar alinea’s omzetten",
            value=bool(
                current_html_cleanup.get("breaks_to_paragraphs", True)
            ),
            disabled=not cleanup_enabled,
        )
        cleanup_lists = cleanup_columns[1].checkbox(
            "Losse kenmerken tot één opsomming samenvoegen",
            value=bool(
                current_html_cleanup.get("merge_feature_lists", True)
            ),
            disabled=not cleanup_enabled,
        )
        proposed["html_cleanup"] = {
            "enabled": cleanup_enabled,
            "remove_attributes": cleanup_remove_attributes,
            "remove_empty": cleanup_remove_empty,
            "breaks_to_paragraphs": cleanup_breaks,
            "merge_feature_lists": cleanup_lists,
        }
        if cleanup_enabled and proposed["strip_html"]:
            st.warning(
                "‘HTML verwijderen’ staat ook aan. Zet die optie uit om de "
                "opgeschoonde alinea’s en opsommingen te behouden."
            )
        case_values = ["unchanged", "upper", "lower", "title"]
        selected_case = columns[0].selectbox(
            "Hoofdletters",
            case_values,
            index=case_values.index(
                current.get("case") or "unchanged"
            ),
            format_func=lambda value: {
                "unchanged": "Niet wijzigen",
                "upper": "ALLES HOOFDLETTERS",
                "lower": "alles kleine letters",
                "title": "Ieder Woord Met Hoofdletter",
            }[value],
        )
        proposed["case"] = (
            "" if selected_case == "unchanged" else selected_case
        )
        rows_key = f"{state_root}_replacements"
        if rows_key not in st.session_state:
            saved = current.get("replacements") or [{
                "find": current.get("find") or "",
                "replace": current.get("replace") or "",
            }]
            st.session_state[rows_key] = [
                {"id": index, **row}
                for index, row in enumerate(saved)
            ]
        replacements = []
        rows = st.session_state[rows_key]
        st.markdown("##### Zoeken en vervangen")
        for index, row in enumerate(rows):
            row_id = row["id"]
            row_columns = st.columns([3, 3, 0.55])
            find_value = row_columns[0].text_input(
                "Zoek tekst/teken",
                value=str(row.get("find") or ""),
                key=f"{rows_key}_find_{row_id}",
                label_visibility=(
                    "visible" if index == 0 else "collapsed"
                ),
            )
            replace_value = row_columns[1].text_input(
                "Vervang door",
                value=str(row.get("replace") or ""),
                key=f"{rows_key}_replace_{row_id}",
                label_visibility=(
                    "visible" if index == 0 else "collapsed"
                ),
            )
            replacements.append({
                "find": find_value, "replace": replace_value,
            })
            if row_columns[2].button(
                "🗑️", key=f"{rows_key}_delete_{row_id}"
            ):
                rows.pop(index)
                st.rerun(scope="fragment")
        if st.button("＋ Zoek/vervangregel toevoegen", key=f"{rows_key}_add"):
            next_id = max((row["id"] for row in rows), default=-1) + 1
            rows.append({"id": next_id, "find": "", "replace": ""})
            st.rerun(scope="fragment")
        proposed["replacements"] = [
            row for row in replacements if row["find"]
        ]
        affix_columns = st.columns(2)
        for name, column, label in (
            ("prefix", affix_columns[0], "voorvoegsel"),
            ("suffix", affix_columns[1], "achtervoegsel"),
        ):
            mode = column.selectbox(
                f"Actie voor {label}",
                ["add", "remove"],
                index=(
                    1 if current.get(f"{name}_mode") == "remove"
                    else 0
                ),
                format_func=lambda value, label=label: (
                    f"{label.capitalize()} toevoegen"
                    if value == "add"
                    else f"{label.capitalize()} verwijderen"
                ),
            )
            proposed[f"{name}_mode"] = mode
            proposed[name] = column.text_input(
                label.capitalize(),
                value=str(current.get(name) or ""),
            )

    elif processing_type == "collection":
        proposed["custom_field"] = bool(current.get("custom_field", True))
        collection = current.get("field_collection") or {}
        saved_items = collection.get("items") or []
        saved_fields = [
            item.get("field") for item in saved_items
            if item.get("field") in available_fields
        ]
        selected_fields = st.multiselect(
            "Bronvelden in dit samengestelde veld",
            available_fields,
            default=saved_fields,
            help=(
                "De volgorde van de geselecteerde velden wordt ook de "
                "volgorde in het resultaat."
            ),
        )
        saved_by_field = {
            item.get("field"): item for item in saved_items
        }
        collection_items = []
        if selected_fields:
            st.markdown("##### Labels en eenheden")
        for index, field_name in enumerate(selected_fields):
            saved_item = saved_by_field.get(field_name) or {}
            item_columns = st.columns([2.2, 2.2, 1.2])
            item_columns[0].code(field_name)
            item_label = item_columns[1].text_input(
                f"Label voor {field_name}",
                value=str(saved_item.get("label") or field_name),
                label_visibility="collapsed",
                key=f"{state_root}_collection_label_{index}",
            )
            item_unit = item_columns[2].text_input(
                f"Eenheid voor {field_name}",
                value=str(saved_item.get("unit") or ""),
                placeholder="mm, kg…",
                label_visibility="collapsed",
                key=f"{state_root}_collection_unit_{index}",
            )
            collection_items.append({
                "field": field_name,
                "label": item_label.strip() or field_name,
                "unit": item_unit.strip(),
            })
        format_options = [
            "dimensions", "lines", "text_symbols", "text", "html_list",
            "template",
        ]
        output_format = st.selectbox(
            "Weergave",
            format_options,
            index=(
                format_options.index(collection.get("format"))
                if collection.get("format") in format_options else 0
            ),
            format_func=lambda value: {
                "dimensions": "Matenregel: lengte × breedte × hoogte",
                "lines": "Iedere waarde op een nieuwe regel",
                "text_symbols": (
                    "Tekstlijst met symbool (voor multi-line metavelden)"
                ),
                "text": "Tekstregel met scheidingsteken",
                "html_list": "HTML-opsomming voor technische informatie",
                "template": "Vrije tekst / HTML-sjabloon",
            }[value],
        )
        collection_config = {
            "items": collection_items,
            "format": output_format,
        }
        if output_format == "dimensions":
            collection_config["unit"] = st.text_input(
                "Eenheid achter de volledige matenregel",
                value=str(collection.get("unit") or "mm"),
            ).strip()
        elif output_format == "text":
            collection_config["separator"] = st.text_input(
                "Scheidingsteken",
                value=str(collection.get("separator") or " · "),
            )
        elif output_format == "text_symbols":
            symbol_options = {
                "small_check": "✓ Kleine zwarte vink",
                "large_green_check": "✅ Groot groen vinkvak",
                "green_checkbox": "✅ Groen vinkvak",
                "green_dot": "🟢 Groen bolletje",
                "blue_arrow": "➡️ Blauwe pijl",
                "gold_star": "⭐ Gouden ster",
                "bullet": "• Zwart opsommingsteken",
                "none": "Geen symbool",
            }
            stored_symbol = str(
                collection.get("symbol") or "small_check"
            )
            collection_config["symbol"] = st.selectbox(
                "Symbool voor iedere samengestelde regel",
                list(symbol_options),
                index=(
                    list(symbol_options).index(stored_symbol)
                    if stored_symbol in symbol_options else 0
                ),
                format_func=lambda value: symbol_options[value],
                help=(
                    "Deze uitvoer bevat geen HTML en past daarom in een "
                    "Shopify multi_line_text_field."
                ),
            )
        elif output_format == "template":
            placeholders = " · ".join(
                f"{{{{ {field_name} }}}}"
                for field_name in selected_fields
            )
            if placeholders:
                st.caption(f"Beschikbare veldplaatsen: {placeholders}")
            st.caption(
                "Voorwaarde: `{% if Veldnaam %}...{% endif %}`. "
                "Ondersteunde opmaak: alinea’s, koppen, vet/cursief, links, "
                "lijsten, definitielijsten en tabellen."
            )
            default_template = "".join(
                (
                    f"{{% if {item['field']} %}}"
                    f"<li><strong>{html.escape(item['label'])}:</strong> "
                    f"{{{{ {item['field']} }}}}"
                    f"{(' ' + html.escape(item['unit'])) if item['unit'] else ''}"
                    "</li>{% endif %}"
                )
                for item in collection_items
            )
            if default_template:
                default_template = f"<ul>{default_template}</ul>"
            collection_config["template"] = st.text_area(
                "Vrije tekst en HTML",
                value=str(
                    collection.get("template") or default_template
                ),
                height=240,
                placeholder=(
                    "<h3>Productmaten</h3>\n<ul>\n"
                    "{% if ProdLength %}<li>Lengte: "
                    "{{ ProdLength }} mm</li>{% endif %}\n"
                    "</ul>"
                ),
            )
        proposed["field_collection"] = collection_config

    elif processing_type == "stock":
        st.caption(
            "Kies het Shopify-magazijn waarop de voorraadwaarde van dit "
            "bronveld wordt bijgewerkt."
        )
        try:
            locations = cached_shopify_locations()
        except Exception as exc:
            locations = []
            st.error(f"Shopify-magazijnen konden niet worden geladen: {exc}")
        location_by_id = {
            str(location.get("id")): location
            for location in locations if location.get("id")
        }
        stored_location_id = str(
            current.get("inventory_location_id") or ""
        )
        location_options = ["", *location_by_id]
        selected_location_id = st.selectbox(
            "Shopify-magazijn *",
            location_options,
            index=(
                location_options.index(stored_location_id)
                if stored_location_id in location_options else 0
            ),
            format_func=lambda location_id: (
                "Selecteer een magazijn"
                if not location_id else
                str(location_by_id[location_id].get("name") or location_id)
            ),
            help="Verplicht: voorraad kan maar naar een concrete locatie.",
        )
        rules_valid = bool(selected_location_id)
        if not rules_valid:
            st.warning("Selecteer een Shopify-magazijn om op te slaan.")
        proposed["inventory_location_id"] = selected_location_id
        proposed["inventory_location_name"] = (
            str(
                location_by_id.get(selected_location_id, {}).get("name")
                or ""
            )
        )

    elif processing_type == "nested":
        nested_config = current.get("nested_field") or {}
        st.caption(
            "Voor bronvelden met herhaalde onderliggende gegevens, zoals "
            "Attributes → Attribute → Name → Values → Value."
        )
        nested_formats = [
            "html_list", "checklist", "text_symbols", "lines", "custom",
        ]
        nested_format = st.selectbox(
            "Weergave van de geneste regels",
            nested_formats,
            index=(
                nested_formats.index(nested_config.get("format"))
                if nested_config.get("format") in nested_formats else 0
            ),
            format_func=lambda value: {
                "html_list": "HTML-lijst met naam en waarde",
                "checklist": "HTML-lijst met een gekozen symbool",
                "text_symbols": (
                    "Tekstlijst met symbool (voor multi-line metavelden)"
                ),
                "lines": "Platte tekst, iedere waarde op een nieuwe regel",
                "custom": "Vrije HTML per geneste regel",
            }[value],
        )
        proposed_nested = {"format": nested_format}
        if nested_format in {"checklist", "text_symbols"}:
            symbol_options = {
                "small_check": "✓ Kleine zwarte vink",
                "large_green_check": "✔ Grote groene vink",
                "green_checkbox": "✅ Groen vinkvak",
                "green_dot": "● Groen bolletje",
                "blue_arrow": "➜ Blauwe pijl",
                "gold_star": "★ Gouden ster",
                "bullet": "• Zwart opsommingsteken",
                "none": "Geen symbool",
            }
            stored_symbol = str(
                nested_config.get("symbol") or "small_check"
            )
            proposed_nested["symbol"] = st.selectbox(
                "Symbool voor iedere regel",
                list(symbol_options),
                index=(
                    list(symbol_options).index(stored_symbol)
                    if stored_symbol in symbol_options else 0
                ),
                format_func=lambda value: symbol_options[value],
                help=(
                    "Het gekozen symbool wordt voor iedere specificatie "
                    "geplaatst. Gebruik Tekstlijst voor een Shopify-veld van "
                    "het type multi_line_text_field."
                ),
            )
        if nested_format == "custom":
            st.caption(
                "Gebruik `{{ name }}` voor Attribute/Name en "
                "`{{ value }}` voor Values/Value."
            )
            proposed_nested["before"] = st.text_area(
                "Vrije tekst/HTML vóór de regels",
                value=str(
                    nested_config.get("before")
                    or "<h3>Technische gegevens</h3><ul>"
                ),
                height=100,
            )
            proposed_nested["item_template"] = st.text_area(
                "HTML voor iedere regel",
                value=str(
                    nested_config.get("item_template")
                    or (
                        "<li><strong>{{ name }}:</strong> "
                        "{{ value }}</li>"
                    )
                ),
                height=120,
            )
            proposed_nested["after"] = st.text_area(
                "Vrije tekst/HTML na de regels",
                value=str(nested_config.get("after") or "</ul>"),
                height=100,
            )
        proposed["nested_field"] = proposed_nested

    elif processing_type == "calculation":
        calculation = current.get("calculation") or {}
        start = calculation.get("start") or {
            "kind": "field", "value": source_field,
        }
        start_columns = st.columns([1.4, 3])
        start_kind = start_columns[0].selectbox(
            "Startwaarde",
            ["field", "number"],
            index=0 if start.get("kind") != "number" else 1,
            format_func=lambda value: (
                "Bronveld" if value == "field" else "Vrij getal"
            ),
        )
        if start_kind == "field":
            start_value = start_columns[1].selectbox(
                "Startveld",
                available_fields,
                index=(
                    available_fields.index(start.get("value"))
                    if start.get("value") in available_fields else 0
                ),
            )
        else:
            start_value = start_columns[1].number_input(
                "Vrij startgetal",
                value=numeric_input_default(start.get("value")),
                format="%.6f",
            )
        steps_key = f"{state_root}_steps"
        if steps_key not in st.session_state:
            st.session_state[steps_key] = [
                {"id": index, **step}
                for index, step in enumerate(
                    calculation.get("steps") or []
                )
            ]
        steps = st.session_state[steps_key]
        calculated_steps = []
        for index, step in enumerate(steps):
            step_id = step["id"]
            operand = step.get("operand") or {}
            columns = st.columns([1, 1.5, 3, 0.55])
            operator = columns[0].selectbox(
                "Operator", ["+", "-", "*", "/"],
                index=(
                    ["+", "-", "*", "/"].index(step.get("operator"))
                    if step.get("operator") in {"+", "-", "*", "/"}
                    else 0
                ),
                key=f"{steps_key}_operator_{step_id}",
                label_visibility=(
                    "visible" if index == 0 else "collapsed"
                ),
            )
            kind = columns[1].selectbox(
                "Soort waarde", ["field", "number"],
                index=0 if operand.get("kind") != "number" else 1,
                format_func=lambda value: (
                    "Bronveld" if value == "field" else "Vrij getal"
                ),
                key=f"{steps_key}_kind_{step_id}",
                label_visibility=(
                    "visible" if index == 0 else "collapsed"
                ),
            )
            if kind == "field":
                value = columns[2].selectbox(
                    "Veld", available_fields,
                    index=(
                        available_fields.index(operand.get("value"))
                        if operand.get("value") in available_fields else 0
                    ),
                    key=f"{steps_key}_field_{step_id}",
                    label_visibility=(
                        "visible" if index == 0 else "collapsed"
                    ),
                )
            else:
                value = columns[2].number_input(
                    "Vrij getal",
                    value=numeric_input_default(operand.get("value")),
                    format="%.6f",
                    key=f"{steps_key}_number_{step_id}",
                    label_visibility=(
                        "visible" if index == 0 else "collapsed"
                    ),
                )
            calculated_steps.append({
                "operator": operator,
                "operand": {"kind": kind, "value": value},
            })
            if columns[3].button(
                "🗑️", key=f"{steps_key}_delete_{step_id}"
            ):
                steps.pop(index)
                st.rerun(scope="fragment")
        if st.button("＋ Rekenstap toevoegen", key=f"{steps_key}_add"):
            next_id = max((step["id"] for step in steps), default=-1) + 1
            steps.append({
                "id": next_id, "operator": "+",
                "operand": {"kind": "field", "value": source_field},
            })
            st.rerun(scope="fragment")
        proposed["calculation"] = {
            "start": {"kind": start_kind, "value": start_value},
            "steps": calculated_steps,
        }
        output_field = st.text_input(
            "Resultaat opslaan als nieuw bronveld",
            value=str(current.get("output_field") or ""),
            placeholder="Bijvoorbeeld shipzip_volumetric_weight",
        )
        proposed["output_field"] = output_field.strip()

    preview_record = apply_source_transformations(
        example_record, {**transformations, source_field: proposed}
    )
    preview_field = output_field or source_field
    preview = preview_record.get(preview_field)
    line_based_preview = (
        processing_type == "collection"
        and proposed.get("field_collection", {}).get("format")
        in {"lines", "text_symbols"}
    ) or (
        processing_type == "nested"
        and proposed.get("nested_field", {}).get("format")
        in {"lines", "text_symbols"}
    )
    if line_based_preview and preview not in (None, ""):
        st.markdown("**Voorbeeld na verwerking:**")
        # st.info rendert Markdown en vouwt enkele regeleinden samen tot
        # spaties. st.text bewaart de bedoelde multi-line-indeling letterlijk.
        st.text(str(preview))
    else:
        st.info(
            f"Voorbeeld na verwerking: "
            f"{preview if preview not in (None, '') else '—'}"
        )
    if (
        processing_type == "text"
        and proposed.get("html_cleanup", {}).get("enabled")
        and preview
    ):
        with st.expander(
            "Opgeschoonde productomschrijving bekijken",
            expanded=True,
        ):
            st.markdown(str(preview), unsafe_allow_html=True)
    elif (
        processing_type == "collection"
        and (
            proposed.get("field_collection", {}).get("format")
            in {"html_list", "template"}
        )
        and preview
    ):
        with st.expander(
            "Samengesteld veld als opgemaakte HTML bekijken",
            expanded=True,
        ):
            st.markdown(str(preview), unsafe_allow_html=True)
    elif (
        processing_type == "nested"
        and proposed.get("nested_field", {}).get("format")
        in {"html_list", "checklist", "custom"}
        and preview
    ):
        with st.expander(
            "Genest veld als opgemaakte lijst bekijken",
            expanded=True,
        ):
            st.markdown(str(preview), unsafe_allow_html=True)
    action_columns = st.columns(2)
    if action_columns[0].button(
        "Bewerkingsregels opslaan", type="primary",
        key=f"{state_root}_save",
        disabled=not rules_valid,
    ):
        transformations[source_field] = proposed
        save_source_transformations(supplier_slug, transformations)
        st.session_state.pop(f"{state_root}_replacements", None)
        st.session_state.pop(f"{state_root}_steps", None)
        st.rerun()
    if action_columns[1].button(
        (
            "Extra veld verwijderen"
            if current.get("custom_field")
            else "Bewerkingsregels verwijderen"
        ),
        key=f"{state_root}_remove",
    ):
        if current.get("custom_field"):
            supplier_config = get_supplier(supplier_slug) or {}
            save_unified_source_mappings(
                supplier_slug,
                pim_mapping={
                    target: source
                    for target, source in (
                        supplier_config.get("field_mapping") or {}
                    ).items()
                    if source != source_field
                },
                shopify_mapping={
                    target: source
                    for target, source in (
                        supplier_config.get(
                            "shopify_field_mapping"
                        ) or {}
                    ).items()
                    if source != source_field
                },
                metafield_mapping={
                    identifier: config
                    for identifier, config in (
                        supplier_config.get(
                            "shopify_metafield_mapping"
                        ) or {}
                    ).items()
                    if config.get("source_field") != source_field
                },
            )
        transformations.pop(source_field, None)
        save_source_transformations(supplier_slug, transformations)
        st.session_state.pop(f"{state_root}_replacements", None)
        st.session_state.pop(f"{state_root}_steps", None)
        st.rerun()
    st.divider()
    st.caption(
        "De testupload maakt of vernieuwt uitsluitend een afzonderlijk "
        "Shopify-concept met een TEST-SKU en de tag "
        "`testproduct_verwijder_deze`. Het echte product wordt niet gewijzigd."
    )
    if st.button(
        "Geselecteerd product als testproduct uploaden",
        type="secondary",
        disabled=not test_product_sku,
        key=f"{state_root}_test_upload",
    ):
        try:
            with st.spinner("Eén testproduct naar Shopify uploaden…"):
                result = upload_test_product(
                    supplier_slug,
                    test_product_sku,
                    description_html=(
                        str(preview)
                        if processing_type == "text" else None
                    ),
                )
            st.success(
                f"Testproduct {result['sku']} als Shopify-concept "
                f"opgeslagen met tag {result['tag']}."
            )
            st.markdown(
                f"[Testproduct openen in Shopify]({result['admin_url']})"
            )
        except Exception as exc:
            st.error(f"Testupload mislukt: {exc}")


@st.dialog("Bewerkingsregels voor bronveld", width="large")
def show_transformation_dialog(
    supplier_slug: str,
    source_field: str,
    available_fields: list[str],
    example_record: dict,
    processing_type: str = "text",
    available_records: list[dict] | None = None,
) -> None:
    current_supplier = get_supplier(supplier_slug) or {}
    transformations = dict(
        current_supplier.get("source_transformations") or {}
    )
    current = transformations.get(source_field, {})
    selected_example = example_record
    test_product_sku = ""
    records = available_records or ([example_record] if example_record else [])
    field_mapping = current_supplier.get("field_mapping") or {}
    sku_field = field_mapping.get("sku") or "sku"
    title_field = field_mapping.get("title") or "title"
    description_field = field_mapping.get("description") or "description"
    if records:
        record_options = list(range(len(records)))
        selected_record_index = st.selectbox(
            "Zoek en selecteer een voorbeeldproduct",
            record_options,
            format_func=lambda index: (
                f"{records[index].get(sku_field) or 'Geen SKU'} · "
                f"{source_record_product_name(records[index], title_field, description_field)[:90]}"
            ),
            key=f"rule_example_{supplier_slug}_{source_field}",
            help=(
                "Klik in het veld en typ een SKU of een deel van de "
                "producttitel."
            ),
        )
        selected_example = records[selected_record_index]
        supplier_sku = str(selected_example.get(sku_field) or "").strip()
        sku_prefix = str(
            (current_supplier.get("request_options") or {}).get(
                "sku_prefix"
            ) or ""
        ).strip()
        test_product_sku = (
            supplier_sku
            if supplier_sku.upper().startswith(sku_prefix.upper())
            else f"{sku_prefix}{supplier_sku}"
        )
    st.markdown(f"**Bronveld:** `{source_field}`")
    st.caption(
        "Voorbeeld vóór bewerking: "
        f"{str(selected_example.get(source_field) or '—')[:500]}"
    )
    if processing_type in PROCESSING_TYPE_LABELS:
        st.caption(
            f"Veldtype: **{PROCESSING_TYPE_LABELS[processing_type]}**"
        )
        render_typed_transformation_rules(
            supplier_slug,
            source_field,
            available_fields,
            selected_example,
            processing_type,
            current,
            transformations,
            test_product_sku,
        )
        return
    content_type = st.selectbox(
        "Soort inhoud",
        ["normal", "primary_image", "additional_images"],
        index=[
            "normal", "primary_image", "additional_images",
        ].index(current.get("content_type") or "normal"),
        format_func=lambda value: {
            "normal": "Normaal bronveld",
            "primary_image": "Hoofdafbeelding",
            "additional_images": (
                "Aanvullende productafbeelding(en)"
            ),
        }[value],
        help=(
            "Gebruik aanvullende productafbeeldingen voor één veld met "
            "meerdere URL’s of markeer meerdere losse fotovelden afzonderlijk. "
            "Lege en dubbele URL’s worden automatisch overgeslagen."
        ),
    )
    columns = st.columns(2)
    trim = columns[0].checkbox(
        "Spaties aan begin en einde verwijderen",
        value=bool(
            current.get("trim", content_type == "normal")
        ),
    )
    normalize_spaces = columns[1].checkbox(
        "Dubbele/willekeurige spaties normaliseren",
        value=bool(current.get("normalize_spaces")),
    )
    remove_control = columns[0].checkbox(
        "Onzichtbare en vreemde stuurtekens verwijderen",
        value=bool(current.get("remove_control")),
    )
    strip_html = columns[1].checkbox(
        "HTML-opmaak verwijderen",
        value=bool(current.get("strip_html")),
    )
    decimal_comma = columns[0].checkbox(
        "Decimale komma omzetten naar punt",
        value=bool(current.get("decimal_comma_to_point")),
    )
    case_options = ["unchanged", "upper", "lower", "title"]
    case_mode = columns[1].selectbox(
        "Hoofdletters",
        case_options,
        index=case_options.index(current.get("case") or "unchanged"),
        format_func=lambda value: {
            "unchanged": "Niet wijzigen",
            "upper": "ALLES HOOFDLETTERS",
            "lower": "alles kleine letters",
            "title": "Ieder Woord Met Hoofdletter",
        }[value],
    )
    st.markdown("##### Zoeken en vervangen")
    replacement_state_key = (
        f"replacement_rows_{supplier_slug}_{source_field}"
    )
    if replacement_state_key not in st.session_state:
        saved_replacements = current.get("replacements")
        if not isinstance(saved_replacements, list):
            saved_replacements = [{
                "find": current.get("find") or "",
                "replace": current.get("replace") or "",
            }]
        st.session_state[replacement_state_key] = [
            {
                "id": index,
                "find": str(item.get("find") or ""),
                "replace": str(item.get("replace") or ""),
            }
            for index, item in enumerate(saved_replacements)
        ] or [{"id": 0, "find": "", "replace": ""}]
    replacement_rows = st.session_state[replacement_state_key]
    replacements = []
    for row_index, replacement_row in enumerate(replacement_rows):
        row_id = replacement_row["id"]
        replace_columns = st.columns([3, 3, 0.55])
        find_value = replace_columns[0].text_input(
            "Zoek tekst/teken",
            value=replacement_row["find"],
            key=f"{replacement_state_key}_find_{row_id}",
            label_visibility=(
                "visible" if row_index == 0 else "collapsed"
            ),
        )
        replace_value = replace_columns[1].text_input(
            "Vervang door",
            value=replacement_row["replace"],
            key=f"{replacement_state_key}_replace_{row_id}",
            label_visibility=(
                "visible" if row_index == 0 else "collapsed"
            ),
        )
        replacements.append({
            "find": find_value,
            "replace": replace_value,
        })
        if replace_columns[2].button(
            "🗑️",
            key=f"{replacement_state_key}_delete_{row_id}",
            help="Deze zoek/vervangregel verwijderen",
        ):
            replacement_rows.pop(row_index)
            st.session_state.pop(
                f"{replacement_state_key}_find_{row_id}", None
            )
            st.session_state.pop(
                f"{replacement_state_key}_replace_{row_id}", None
            )
            st.rerun(scope="fragment")
    if st.button(
        "＋ Zoek/vervangregel toevoegen",
        key=f"{replacement_state_key}_add",
    ):
        next_id = max(
            (row["id"] for row in replacement_rows), default=-1
        ) + 1
        replacement_rows.append({
            "id": next_id, "find": "", "replace": "",
        })
        st.rerun(scope="fragment")

    affix_columns = st.columns(2)
    prefix_mode = affix_columns[0].selectbox(
        "Actie voor voorvoegsel",
        ["add", "remove"],
        index=0 if current.get("prefix_mode") != "remove" else 1,
        format_func=lambda value: (
            "Voorvoegsel toevoegen"
            if value == "add" else "Voorvoegsel verwijderen"
        ),
    )
    prefix = affix_columns[0].text_input(
        (
            "Toe te voegen voorvoegsel"
            if prefix_mode == "add"
            else "Te verwijderen voorvoegsel"
        ),
        value=str(current.get("prefix") or ""),
        help=(
            "Toevoegen plaatst de tekst aan het begin. Verwijderen haalt "
            "de tekst alleen weg wanneer het veld ermee begint."
        ),
    )
    suffix_mode = affix_columns[1].selectbox(
        "Actie voor achtervoegsel",
        ["add", "remove"],
        index=0 if current.get("suffix_mode") != "remove" else 1,
        format_func=lambda value: (
            "Achtervoegsel toevoegen"
            if value == "add" else "Achtervoegsel verwijderen"
        ),
    )
    suffix = affix_columns[1].text_input(
        (
            "Toe te voegen achtervoegsel"
            if suffix_mode == "add"
            else "Te verwijderen achtervoegsel"
        ),
        value=str(current.get("suffix") or ""),
        help=(
            "Toevoegen plaatst de tekst aan het einde. Verwijderen haalt "
            "de tekst alleen weg wanneer het veld ermee eindigt."
        ),
    )
    st.markdown("##### Waarden omzetten")
    st.caption("Eén omzetting per regel, bijvoorbeeld: in stock = 1")
    value_map_text = st.text_area(
        "Vaste waardemapping",
        value="\n".join(
            f"{source} = {target}"
            for source, target in (current.get("value_map") or {}).items()
        ),
        placeholder="in stock = 1\nout of stock = 0\nplease call = 0",
    )
    value_map = {}
    for mapping_line in value_map_text.splitlines():
        if "=" in mapping_line:
            source_value, target_value = mapping_line.split("=", 1)
            if source_value.strip():
                value_map[source_value.strip()] = target_value.strip()
    st.markdown("##### Formuleberekening")
    st.caption(
        "Kies een startveld of vrij getal en voeg daarna zoveel stappen met "
        "+, −, × en ÷ toe als nodig."
    )
    current_calculation = current.get("calculation") or {}
    start_config = current_calculation.get("start") or {
        "kind": "field", "value": source_field,
    }
    calculation_key = f"calculation_{supplier_slug}_{source_field}"
    start_columns = st.columns([1.4, 3])
    start_kind = start_columns[0].selectbox(
        "Startwaarde",
        ["field", "number"],
        index=0 if start_config.get("kind") != "number" else 1,
        format_func=lambda value: (
            "Bronveld" if value == "field" else "Vrij getal"
        ),
        key=f"{calculation_key}_start_kind",
    )
    if start_kind == "field":
        start_default = str(start_config.get("value") or source_field)
        start_value = start_columns[1].selectbox(
            "Startveld",
            available_fields,
            index=(
                available_fields.index(start_default)
                if start_default in available_fields else 0
            ),
            key=f"{calculation_key}_start_field",
        )
    else:
        start_value = start_columns[1].number_input(
            "Vrij startgetal",
            value=numeric_input_default(start_config.get("value")),
            format="%.6f",
            key=f"{calculation_key}_start_number",
        )

    calculation_rows_key = f"{calculation_key}_steps"
    if calculation_rows_key not in st.session_state:
        st.session_state[calculation_rows_key] = [
            {"id": index, **step}
            for index, step in enumerate(
                current_calculation.get("steps") or []
            )
        ]
    calculation_rows = st.session_state[calculation_rows_key]
    calculation_steps = []
    for step_index, step in enumerate(calculation_rows):
        step_id = step["id"]
        operand = step.get("operand") or {}
        step_columns = st.columns([1, 1.5, 3, 0.55])
        operator = step_columns[0].selectbox(
            "Operator",
            ["+", "-", "*", "/"],
            index=(
                ["+", "-", "*", "/"].index(step.get("operator"))
                if step.get("operator") in {"+", "-", "*", "/"} else 0
            ),
            key=f"{calculation_key}_operator_{step_id}",
            label_visibility=(
                "visible" if step_index == 0 else "collapsed"
            ),
        )
        operand_kind = step_columns[1].selectbox(
            "Soort waarde",
            ["field", "number"],
            index=0 if operand.get("kind") != "number" else 1,
            format_func=lambda value: (
                "Bronveld" if value == "field" else "Vrij getal"
            ),
            key=f"{calculation_key}_kind_{step_id}",
            label_visibility=(
                "visible" if step_index == 0 else "collapsed"
            ),
        )
        if operand_kind == "field":
            operand_default = str(operand.get("value") or source_field)
            operand_value = step_columns[2].selectbox(
                "Veld",
                available_fields,
                index=(
                    available_fields.index(operand_default)
                    if operand_default in available_fields else 0
                ),
                key=f"{calculation_key}_field_{step_id}",
                label_visibility=(
                    "visible" if step_index == 0 else "collapsed"
                ),
            )
        else:
            operand_value = step_columns[2].number_input(
                "Vrij getal",
                value=numeric_input_default(operand.get("value")),
                format="%.6f",
                key=f"{calculation_key}_number_{step_id}",
                label_visibility=(
                    "visible" if step_index == 0 else "collapsed"
                ),
            )
        calculation_steps.append({
            "operator": operator,
            "operand": {
                "kind": operand_kind, "value": operand_value,
            },
        })
        if step_columns[3].button(
            "🗑️",
            key=f"{calculation_key}_delete_{step_id}",
            help="Deze rekenstap verwijderen",
        ):
            calculation_rows.pop(step_index)
            st.rerun(scope="fragment")
    if st.button(
        "＋ Rekenstap toevoegen",
        key=f"{calculation_key}_add",
    ):
        next_id = max(
            (row["id"] for row in calculation_rows), default=-1
        ) + 1
        calculation_rows.append({
            "id": next_id,
            "operator": "+",
            "operand": {"kind": "field", "value": source_field},
        })
        st.rerun(scope="fragment")
    calculation_enabled = st.checkbox(
        "Deze formule uitvoeren",
        value=bool(current_calculation),
        key=f"{calculation_key}_enabled",
    )
    output_field = st.text_input(
        "Resultaat opslaan als nieuw bronveld",
        value=str(current.get("output_field") or ""),
        placeholder="Bijvoorbeeld shipzip_volumetric_weight",
        help=(
            "Leeg betekent dat het gekozen bronveld wordt overschreven. "
            "Gebruik voor berekeningen bij voorkeur een nieuwe veldnaam."
        ),
    )
    proposed = {
        "content_type": (
            "" if content_type == "normal" else content_type
        ),
        "trim": trim,
        "normalize_spaces": normalize_spaces,
        "remove_control": remove_control,
        "strip_html": strip_html,
        "decimal_comma_to_point": decimal_comma,
        "case": "" if case_mode == "unchanged" else case_mode,
        "replacements": [
            item for item in replacements if item["find"]
        ],
        "prefix": prefix,
        "prefix_mode": prefix_mode,
        "suffix": suffix,
        "suffix_mode": suffix_mode,
        "value_map": value_map,
        "calculation": (
            {
                "start": {"kind": start_kind, "value": start_value},
                "steps": calculation_steps,
            }
            if calculation_enabled else {}
        ),
        "output_field": output_field.strip(),
    }
    preview_record = apply_source_transformations(
        example_record, {source_field: proposed}
    )
    preview = preview_record.get(output_field.strip() or source_field)
    st.info(f"Voorbeeld na bewerking: {preview if preview != '' else '—'}")
    action_columns = st.columns(2)
    if action_columns[0].button("Bewerkingsregels opslaan", type="primary"):
        transformations[source_field] = proposed
        save_source_transformations(supplier_slug, transformations)
        st.session_state.pop(replacement_state_key, None)
        st.session_state.pop(calculation_rows_key, None)
        st.rerun()
    if action_columns[1].button("Bewerkingsregels verwijderen"):
        transformations.pop(source_field, None)
        save_source_transformations(supplier_slug, transformations)
        st.session_state.pop(replacement_state_key, None)
        st.session_state.pop(calculation_rows_key, None)
        st.rerun()


def reset_product_table_selection(supplier_slug: str) -> None:
    revision_key = f"products_table_revision_{supplier_slug}"
    st.session_state[revision_key] = int(
        st.session_state.get(revision_key, 0)
    ) + 1
    st.session_state.get(
        "selected_products_by_supplier", {}
    ).pop(supplier_slug, None)


def analyze_supplier_source(
    supplier_slug: str, uploaded_file=None
) -> None:
    try:
        full_supplier = get_supplier(
            supplier_slug, include_credentials=True
        )
        analysis = read_source(
            full_supplier,
            uploaded_file.getvalue() if uploaded_file else None,
            uploaded_file.name if uploaded_file else "",
        )
        st.session_state[f"analysis_{supplier_slug}"] = analysis
        st.session_state.pop(
            f"central_example_product_{supplier_slug}", None
        )
        # Streamlit bewaart selectboxwaarden op widgetsleutel. Na een nieuwe
        # bronanalyse konden daardoor oude lege keuzes het actuele automatische
        # PIM-voorstel overschrijven. Verwijder alleen de koppelingswidgets van
        # deze leverancier; opgeslagen mappings blijven in de database staan en
        # vullen de widgets bij de rerun opnieuw met de actuele bronvelden.
        mapping_widget_markers = (
            f"lock_auto_mapping_{supplier_slug}_",
            f"compose_auto_mapping_{supplier_slug}_",
            f"editable_auto_mapping_{supplier_slug}_",
            f"compose_fields_{supplier_slug}_",
            f"compose_template_{supplier_slug}_",
            f"processing_type_{supplier_slug}_",
            f"unified_kind_{supplier_slug}_",
            f"unified_target_{supplier_slug}_",
        )
        for state_key in list(st.session_state):
            if any(
                str(state_key).startswith(marker)
                for marker in mapping_widget_markers
            ):
                st.session_state.pop(state_key, None)
        known_mapping = suggest_source_field_mapping(analysis.fields)
        learned_mapping = learned_source_field_mapping(
            analysis.fields,
            claimed_sources=set(known_mapping.values()),
            claimed_targets=set(known_mapping),
        )
        combined_mapping = {**known_mapping, **learned_mapping}
        try:
            with st.spinner(
                "AI analyseert onbekende velden en voorbeeldwaarden…"
            ):
                st.session_state[
                    f"ai_mapping_proposals_{supplier_slug}"
                ] = ai_field_mapping_proposals(
                    supplier_slug,
                    analysis.fields,
                    analysis.records,
                    combined_mapping,
                )
            st.session_state.pop(
                f"ai_mapping_error_{supplier_slug}", None
            )
        except Exception as exc:
            st.session_state[
                f"ai_mapping_proposals_{supplier_slug}"
            ] = []
            st.session_state[
                f"ai_mapping_error_{supplier_slug}"
            ] = str(exc)
        reset_product_table_selection(supplier_slug)
        st.rerun()
    except Exception as exc:
        st.error(f"Bron kon niet worden gelezen: {exc}")


st.markdown(
    """
    <style>
    :root { --ws-orange: #f47b20; --ws-charcoal: #202124; --ws-soft: #f5f5f3; }
    .ws-breadcrumb { color:#777; font-size:.88rem; margin-bottom:.7rem; }
    .ws-brand { text-transform:uppercase; letter-spacing:.08em; color:#777; font-size:.78rem; font-weight:700; }
    .ws-product-title { color:var(--ws-charcoal); font-size:2rem; font-weight:750; line-height:1.13; margin:.35rem 0 .7rem; }
    .ws-price { font-size:1.65rem; font-weight:750; color:var(--ws-charcoal); margin:.6rem 0; }
    .ws-old-price { color:#888; text-decoration:line-through; font-size:1rem; margin-right:.5rem; }
    .ws-stock-ok,.ws-stock-no { display:inline-block; border-radius:99px; padding:.3rem .7rem; font-weight:700; font-size:.83rem; }
    .ws-stock-ok { background:#eaf6ed; color:#187536; }
    .ws-stock-no { background:#fbecec; color:#a52b2b; }
    .ws-meta { background:var(--ws-soft); border-left:4px solid var(--ws-orange); padding:.8rem 1rem; margin:1rem 0; }
    .ws-meta div { padding:.18rem 0; }
    .ws-section { border-top:2px solid #ecebe7; margin-top:1.4rem; padding-top:1rem; }
    .ws-section h2 { font-size:1.35rem; color:var(--ws-charcoal); }
    .ws-thumb-label { color:#777; font-size:.78rem; }
    .pim-hero { padding:1.2rem 0 1.5rem; }
    .pim-hero h1 { font-size:2.65rem; margin-bottom:.3rem; color:var(--ws-charcoal); }
    .pim-hero p { color:#666; font-size:1.08rem; max-width:760px; }
    .pim-card { height:240px; box-sizing:border-box; overflow:hidden;
      padding:1.25rem; border:1px solid #e7e4df;
      border-radius:14px; background:linear-gradient(145deg,#fff,#faf9f6);
      box-shadow:0 5px 18px rgba(30,30,30,.04); }
    .pim-card-icon { font-size:2rem; }
    .pim-card h3 { margin:.6rem 0 .35rem; color:var(--ws-charcoal); }
    .pim-card p { color:#686868; min-height:48px; }
    .pim-image-placeholder { aspect-ratio:1; display:grid; place-items:center;
      background:#f1f1ef; color:#777; border-radius:6px; }
    @media (max-width:1200px) {
      div[data-testid="stHorizontalBlock"]:has(.pim-card) { flex-wrap:wrap; }
      div[data-testid="stHorizontalBlock"]:has(.pim-card)
        > div[data-testid="stColumn"] {
        flex:1 1 calc(33.333% - 1rem) !important;
        min-width:260px !important;
        width:auto !important;
      }
    }
    @media (max-width:650px) {
      div[data-testid="stHorizontalBlock"]:has(.pim-card)
        > div[data-testid="stColumn"] {
        flex-basis:100% !important;
        min-width:100% !important;
      }
      .pim-card { height:auto; min-height:190px; }
    }
    @keyframes pim-clock-spin { to { transform:rotate(360deg); } }
    .pim-source-loading { display:flex; align-items:center; gap:.5rem;
      min-height:2.5rem; color:#555; font-weight:650; white-space:nowrap; }
    .pim-source-loading .clock { display:inline-block; font-size:1.35rem;
      animation:pim-clock-spin 1.2s linear infinite; }
    div[data-testid="stImage"] img { border-radius:4px; background:white; }
    </style>
    """,
    unsafe_allow_html=True,
)

PIM_SECTIONS = [
    "Voorblad",
    "Nieuw product maken",
    "Leverancierssynchronisatie",
    "Bundelbouwer",
    "Labels",
    "Back-ups",
]
SECTION_QUERY_VALUES = {
    "Voorblad": "home",
    "Nieuw product maken": "new-product",
    "Leverancierssynchronisatie": "suppliers",
    "Bundelbouwer": "bundles",
    "Labels": "labels",
    "Back-ups": "backups",
}
QUERY_VALUE_SECTIONS = {
    value: key for key, value in SECTION_QUERY_VALUES.items()
}
requested_section = QUERY_VALUE_SECTIONS.get(
    str(st.query_params.get("section") or "")
)
if requested_section and "pim_main_section" not in st.session_state:
    st.session_state["pim_main_section"] = requested_section
if st.session_state.get("pim_main_section") not in PIM_SECTIONS:
    st.session_state["pim_main_section"] = "Voorblad"

with st.sidebar:
    st.markdown("### Weldingshop PIM")
    main_section = st.radio(
        "Hoofdsectie",
        PIM_SECTIONS,
        key="pim_main_section",
        label_visibility="collapsed",
    )
    st.divider()
previous_main_section = st.session_state.get("pim_previous_rendered_section")
entering_labels = main_section == "Labels" and previous_main_section != "Labels"
st.session_state["pim_previous_rendered_section"] = main_section
if st.query_params.get("section") != SECTION_QUERY_VALUES[main_section]:
    st.query_params["section"] = SECTION_QUERY_VALUES[main_section]

if main_section == "Voorblad":
    suppliers_for_home = list_suppliers()
    bundle_summary = bundle_stats()
    st.markdown(
        """
        <div class="pim-hero">
          <h1>Weldingshop PIM</h1>
          <p>De centrale werkplaats voor productdata, leveranciers, bundels en
          veilige Shopify-processen.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    metric_columns = st.columns(4)
    metric_columns[0].metric("Leveranciers", len(suppliers_for_home))
    metric_columns[1].metric("Bundelvarianten", bundle_summary["total"])
    metric_columns[2].metric("Bundels geselecteerd", bundle_summary["selected"])
    metric_columns[3].metric("Bundelcomponenten", bundle_summary["components"])

    new_product_card, supplier_card, bundle_card, label_card, backup_card = (
        st.columns(5)
    )
    with new_product_card:
        st.markdown(
            """
            <div class="pim-card"><div class="pim-card-icon">🆕</div>
            <h3>Nieuw product maken</h3>
            <p>Maak vanuit een artikel een controleerbaar nieuw Shopify-product.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.button(
            "Open productmaker",
            width="stretch",
            on_click=lambda: st.session_state.update(
                pim_main_section="Nieuw product maken"
            ),
        )
    with supplier_card:
        st.markdown(
            """
            <div class="pim-card"><div class="pim-card-icon">🔄</div>
            <h3>Leverancierssynchronisatie</h3>
            <p>Bronnen koppelen, productdata controleren en gecontroleerd
            synchroniseren met Shopify.</p></div>
            """,
            unsafe_allow_html=True,
        )
        st.button(
            "Open leveranciers",
            width="stretch",
            on_click=lambda: st.session_state.update(
                pim_main_section="Leverancierssynchronisatie"
            ),
        )
    with bundle_card:
        st.markdown(
            """
            <div class="pim-card"><div class="pim-card-icon">🧩</div>
            <h3>Bundelbouwer</h3>
            <p>Native Shopify-bundels bekijken, samenstellen, selecteren en
            straks gecontroleerd publiceren.</p></div>
            """,
            unsafe_allow_html=True,
        )
        st.button(
            "Open bundelbouwer",
            width="stretch",
            on_click=lambda: st.session_state.update(
                pim_main_section="Bundelbouwer"
            ),
        )
    with label_card:
        st.markdown(
            """
            <div class="pim-card"><div class="pim-card-icon">🏷️</div>
            <h3>Labels</h3>
            <p>Productlabels zoeken, samenstellen en op het juiste formaat
            afdrukken.</p></div>
            """,
            unsafe_allow_html=True,
        )
        st.button(
            "Open labelprogramma",
            width="stretch",
            on_click=lambda: st.session_state.update(pim_main_section="Labels"),
        )
    with backup_card:
        st.markdown(
            """
            <div class="pim-card"><div class="pim-card-icon">🛡️</div>
            <h3>Back-ups</h3>
            <p>Maak een versleutelde serverback-up van PIM, ERP, productdata,
            configuratie en secrets met herstelcontroles.</p></div>
            """,
            unsafe_allow_html=True,
        )
        st.button(
            "Open veilige back-ups",
            width="stretch",
            on_click=lambda: st.session_state.update(pim_main_section="Back-ups"),
        )

    st.markdown("### Ruimte voor volgende hoofdstukken")
    st.caption(
        "Denk aan datakwaliteit, mediabeheer, prijsregels, productvertalingen, "
        "publicatieplanning en auditrapporten."
    )
    st.stop()

if main_section == "Nieuw product maken":
    show_product_maker()
    st.stop()

if main_section == "Bundelbouwer":
    show_bundle_builder(PROJECT_DIR)
    st.stop()

if main_section == "Labels":
    show_label_page(force_reload=entering_labels)
    st.stop()

if main_section == "Back-ups":
    st.title("Back-ups")
    st.caption(
        "Open ieder back-uponderdeel via een eigen tabblad en bouw gecontroleerde, "
        "versleutelde herstelarchieven op."
    )
    shopify_backup_component_names = [
        "Producten", "Slimme collecties", "Handmatige collecties",
        "Metafield-definities", "Metaobjects", "Thema’s", "Blogs",
        "Bestanden", "Opgeslagen zoekopdrachten", "Pagina’s", "Menu’s",
        "Voorraad", "Policies", "Verzendzones", "Klanten", "Orders",
        "Winkel klonen",
    ]
    server_main_tab, shopify_main_tab = st.tabs(["Server", "Shopify"])
    with server_main_tab:
        st.success("Beschikbaar · volledige versleutelde PIM- en ERP-serverback-up")
        st.caption("De bediening staat onder **Serverback-up** op deze pagina.")
    planned_backup_components = {
        "Slimme collecties": "Collectieregels, sortering, publicaties en metafields.",
        "Handmatige collecties": "Collectie-inhoud, productvolgorde en metafields.",
        "Metafield-definities": "Definities per Shopify-eigenaarstype.",
        "Metaobjects": "Metaobjectdefinities, records en onderlinge verwijzingen.",
        "Thema’s": "Thema-informatie en alle afzonderlijke themabestanden.",
        "Blogs": "Blogs, artikelen, auteursinformatie en afbeeldingen.",
        "Bestanden": "Originele bestanden uit Shopify Files met controlesommen.",
        "Opgeslagen zoekopdrachten": "Opgeslagen filters voor producten, orders en bestanden.",
        "Pagina’s": "Pagina-inhoud, SEO, publicaties en metafields.",
        "Menu’s": "Navigatiestructuur en geneste menu-items.",
        "Voorraad": "Voorraadaantallen per artikel en Shopify-locatie.",
        "Policies": "Privacy-, retour-, verzend- en algemene voorwaarden.",
        "Verzendzones": "Verzendprofielen, zones, methoden en tarieven.",
        "Klanten": "Klantrecords, adressen, tags en metafields.",
        "Orders": "Historische orders, orderregels, betalingen en fulfilmentgegevens.",
        "Winkel klonen": "Gecontroleerde opbouw van een nieuwe winkel uit back-upmodules.",
    }
    with shopify_main_tab:
        st.caption("Kies hieronder welk onderdeel van de Shopify-winkel je wilt beheren.")
        shopify_component_tabs = st.tabs(shopify_backup_component_names)
        with shopify_component_tabs[0]:
            st.success("Beschikbaar · actuele versleutelde Shopify-productcatalogus")
            st.caption(
                "De bediening staat onder **Shopify-productcatalogus** op deze pagina."
            )
        for tab, component_name in zip(
            shopify_component_tabs[1:], shopify_backup_component_names[1:]
        ):
            with tab:
                st.warning("Eigen ingang aangemaakt · back-upmodule wordt nog gebouwd")
                st.caption(planned_backup_components[component_name])
                st.button(
                    f"{component_name} nog niet beschikbaar",
                    key=f"planned_backup_{component_name}", disabled=True,
                    width="stretch",
                )
    with server_main_tab:
        st.markdown('<div id="server-backup"></div>', unsafe_allow_html=True)
        st.markdown("### Serverback-up")
        st.warning(
            "Het archief bevat gevoelige gegevens en encryptiesleutels. Gebruik een "
            "uniek sterk wachtwoord, bewaar dat apart en deel het nooit via e-mail."
        )
        st.markdown("#### Nieuwe herstelback-up maken")
        estimate = cached_server_backup_estimate()
        estimate_columns = st.columns(2)
        estimate_columns[0].metric(
            "Geschatte maximale back-upomvang",
            f"{estimate['archive_upper_bytes'] / (1024 ** 3):.2f} GB",
            help=(
                "Conservatieve bovengrens op basis van de gegevens die nu worden "
                "meegenomen. Compressie maakt het uiteindelijke bestand meestal kleiner."
            ),
        )
        estimate_columns[1].metric(
            "Tijdelijk benodigde werkruimte",
            f"{estimate['temporary_required_bytes'] / (1024 ** 3):.2f} GB",
            help=(
                "Tijdens het maken bestaan de werkkopie, het gecomprimeerde archief en "
                "het versleutelde archief korte tijd naast elkaar."
            ),
        )
        st.info(
            f"De back-up wordt op de server opgeslagen in `{DEFAULT_BACKUP_DIR}`. "
            "Daar vind je met WinSCP zowel het `.enc`-bestand als het "
            "`.sha256`-bestand."
        )
        with st.form("encrypted_server_backup", clear_on_submit=True):
            password_columns = st.columns(2)
            backup_password = password_columns[0].text_input(
                "Back-upwachtwoord", type="password",
                help="Minimaal 12 tekens. Dit wachtwoord wordt niet opgeslagen.",
            )
            confirm_password = password_columns[1].text_input(
                "Wachtwoord herhalen", type="password",
            )
            backup_confirmed = st.checkbox(
                "Ik heb het wachtwoord buiten deze server veilig vastgelegd",
            )
            create_backup = st.form_submit_button(
                "Versleutelde serverback-up maken", type="primary",
                width="stretch",
            )
        progress_notice = st.empty()
        if create_backup:
            if len(backup_password) < 12:
                st.error("Gebruik een back-upwachtwoord van minimaal 12 tekens.")
            elif backup_password != confirm_password:
                st.error("De twee ingevoerde wachtwoorden zijn niet gelijk.")
            elif not backup_confirmed:
                st.error(
                    "Bevestig eerst dat je het wachtwoord buiten deze server veilig "
                    "hebt vastgelegd."
                )
            else:
                try:
                    with st.spinner("Volledige herstelback-up wordt opgebouwd…"):
                        result = create_server_backup(
                            backup_password,
                            progress=lambda message: progress_notice.info(message),
                        )
                    st.session_state["latest_server_backup"] = result
                    st.success(
                        f"Back-up voltooid en versleuteld in {DEFAULT_BACKUP_DIR}. "
                        "Je vindt daar met WinSCP het archief en het SHA-256-bestand."
                    )
                except Exception as exc:
                    st.error(f"Serverback-up mislukt: {exc}")

        st.markdown("#### Beschikbare serverback-ups")
        backups = list_server_backups()
        storage = backup_storage_summary()
        storage_columns = st.columns(3)
        storage_columns[0].metric("Aantal back-ups", storage["backup_count"])
        storage_columns[1].metric(
            "Ruimte door back-ups",
            f"{storage['backup_bytes'] / (1024 ** 3):.2f} GB",
        )
        storage_columns[2].metric(
            "Vrije serverruimte",
            f"{storage['disk_free_bytes'] / (1024 ** 3):.1f} GB",
            help=(
                "Vrij op het bestandssysteem van de back-upmap; totale capaciteit: "
                f"{storage['disk_total_bytes'] / (1024 ** 3):.1f} GB."
            ),
        )
        if not backups:
            st.info("Er is nog geen volledige serverback-up gemaakt.")
        else:
            backup_rows = [{
                "Bestand": item["name"],
                "Grootte": f"{item['size'] / (1024 ** 3):.2f} GB",
                "Aangemaakt (UTC)": item["modified_at"],
                "SHA-256": item["sha256"],
            } for item in backups]
            st.dataframe(backup_rows, hide_index=True, width="stretch", height=220)
            selected_backup_name = st.selectbox(
                "Back-up voor controle of download selecteren",
                [item["name"] for item in backups],
            )
            selected_backup = next(
                item for item in backups if item["name"] == selected_backup_name
            )
            st.code(selected_backup["path"], language=None)
            st.caption(
                f"WinSCP-servermap: {DEFAULT_BACKUP_DIR}. Download zowel `.enc` als "
                "`.sha256`."
            )
            action_columns = st.columns(2)
            if action_columns[0].button(
                "Volledige SHA-256-controle uitvoeren",
                key=f"verify_backup_{selected_backup_name}", width="stretch",
            ):
                with st.spinner("Het volledige archief wordt opnieuw gelezen…"):
                    verification = verify_server_backup(selected_backup["path"])
                if verification["valid"]:
                    st.success("SHA-256 klopt: het versleutelde archief is ongewijzigd.")
                else:
                    st.error("SHA-256 wijkt af. Gebruik dit back-upbestand niet.")
            checksum_path = Path(selected_backup["checksum_path"])
            if checksum_path.is_file():
                action_columns[1].download_button(
                    "SHA-256-bestand downloaden",
                    data=checksum_path.read_bytes(), file_name=checksum_path.name,
                    mime="text/plain", width="stretch",
                )


    with shopify_component_tabs[0]:
        st.divider()
        st.markdown('<div id="shopify-productcatalogus"></div>', unsafe_allow_html=True)
        st.markdown("#### Shopify-productcatalogus")
        st.caption(
            "Maakt een actuele, versleutelde momentopname van producten, varianten, "
            "productmetafields en de originele productmedia. Historisch verwijderde "
            "producten worden niet meegenomen. Klanten, orders en winkelinrichting "
            "volgen als afzonderlijke back-upmodules."
        )
        st.info(
            f"De Shopify-catalogusback-up wordt opgeslagen in "
            f"`{DEFAULT_SHOPIFY_BACKUP_DIR}`. Op basis van de huidige winkel kan deze "
            "run meerdere gigabytes groot zijn en geruime tijd duren."
        )
        with st.form("encrypted_shopify_catalog_backup", clear_on_submit=True):
            shopify_password_columns = st.columns(2)
            shopify_backup_password = shopify_password_columns[0].text_input(
                "Shopify-back-upwachtwoord", type="password",
                help="Minimaal 12 tekens; dit wachtwoord wordt niet opgeslagen.",
            )
            shopify_confirm_password = shopify_password_columns[1].text_input(
                "Shopify-wachtwoord herhalen", type="password",
            )
            shopify_backup_confirmed = st.checkbox(
                "Ik heb het Shopify-back-upwachtwoord buiten deze server vastgelegd",
            )
            create_shopify_backup = st.form_submit_button(
                "Versleutelde Shopify-catalogusback-up maken", type="primary",
                width="stretch",
            )
        shopify_progress = st.empty()
        if create_shopify_backup:
            if len(shopify_backup_password) < 12:
                st.error("Gebruik een Shopify-back-upwachtwoord van minimaal 12 tekens.")
            elif shopify_backup_password != shopify_confirm_password:
                st.error("De twee ingevoerde Shopify-wachtwoorden zijn niet gelijk.")
            elif not shopify_backup_confirmed:
                st.error("Bevestig eerst dat het wachtwoord veilig buiten de server staat.")
            else:
                try:
                    with st.spinner("Shopify-catalogusback-up wordt opgebouwd…"):
                        result = create_shopify_catalog_backup(
                            shopify_backup_password,
                            progress=lambda message: shopify_progress.info(message),
                        )
                    st.success(
                        f"Shopify-catalogusback-up voltooid: {result['products']} "
                        f"producten en {result['media_files']} mediabestanden."
                    )
                except Exception as exc:
                    st.error(f"Shopify-catalogusback-up mislukt: {exc}")

        shopify_backups = list_shopify_catalog_backups()
        if not shopify_backups:
            st.info("Er is nog geen Shopify-catalogusback-up gemaakt.")
        else:
            st.dataframe(
                [{"Bestand": item["name"],
                  "Grootte": f"{item['size'] / (1024 ** 3):.2f} GB",
                  "SHA-256": item["sha256"]} for item in shopify_backups],
                hide_index=True, width="stretch", height=220,
            )
            selected_shopify_name = st.selectbox(
                "Shopify-back-up voor controle selecteren",
                [item["name"] for item in shopify_backups],
            )
            selected_shopify_backup = next(
                item for item in shopify_backups
                if item["name"] == selected_shopify_name
            )
            shopify_action_columns = st.columns(2)
            if shopify_action_columns[0].button(
                "Shopify SHA-256-controle uitvoeren",
                key=f"verify_shopify_{selected_shopify_name}", width="stretch",
            ):
                with st.spinner("Het volledige Shopify-archief wordt gelezen…"):
                    verification = verify_shopify_catalog_backup(
                        selected_shopify_backup["path"]
                    )
                if verification["valid"]:
                    st.success("SHA-256 klopt: de Shopify-back-up is ongewijzigd.")
                else:
                    st.error("SHA-256 wijkt af. Gebruik deze Shopify-back-up niet.")
            shopify_checksum = Path(selected_shopify_backup["checksum_path"])
            if shopify_checksum.is_file():
                shopify_action_columns[1].download_button(
                    "Shopify SHA-256-bestand downloaden",
                    data=shopify_checksum.read_bytes(), file_name=shopify_checksum.name,
                    mime="text/plain", width="stretch",
                )

            with st.expander("Eén product uit deze back-up terugzetten"):
                st.warning(
                    "Het product wordt als een nieuw concept aangemaakt. Het bestaande "
                    "Shopify-product en de huidige voorraad worden niet gewijzigd."
                )
                restore_password = st.text_input(
                    "Back-upwachtwoord voor herstel", type="password",
                    key=f"restore_password_{selected_shopify_name}",
                )
                restore_state_key = f"restore_products_{selected_shopify_name}"
                if st.button(
                    "Producten in back-up laden",
                    key=f"load_restore_products_{selected_shopify_name}",
                    width="stretch",
                ):
                    try:
                        with st.spinner("Back-up ontsleutelen en producten lezen…"):
                            st.session_state[restore_state_key] = (
                                list_products_in_shopify_backup(
                                    selected_shopify_backup["path"], restore_password
                                )
                            )
                    except Exception as exc:
                        st.session_state.pop(restore_state_key, None)
                        st.error(f"Back-up openen mislukt: {exc}")
                restore_products = st.session_state.get(restore_state_key) or []
                if restore_products:
                    restore_query = st.text_input(
                        "Zoeken op titel, SKU, leverancier of handle",
                        key=f"restore_query_{selected_shopify_name}",
                    ).strip().casefold()
                    filtered = [
                        item for item in restore_products
                        if not restore_query or restore_query in " ".join([
                            item["title"], item["vendor"], item["handle"],
                            *item["skus"],
                        ]).casefold()
                    ]
                    if not filtered:
                        st.info("Geen producten gevonden met deze zoekterm.")
                    else:
                        labels = {
                            item["id"]: (
                                f"{item['title']} — {', '.join(item['skus'][:3]) or 'geen SKU'}"
                            ) for item in filtered
                        }
                        restore_product_id = st.selectbox(
                            "Product selecteren", [item["id"] for item in filtered],
                            format_func=lambda value: labels[value],
                            key=f"restore_product_{selected_shopify_name}",
                        )
                        preview = next(
                            item for item in filtered if item["id"] == restore_product_id
                        )
                        viewer_key = (
                            f"restore_viewer_{selected_shopify_name}_{restore_product_id}"
                        )
                        if st.button(
                            "Geselecteerd product bekijken",
                            key=f"open_{viewer_key}", width="stretch",
                        ):
                            try:
                                with st.spinner("Productdetails uit back-up laden…"):
                                    st.session_state[viewer_key] = (
                                        get_product_from_shopify_backup(
                                            selected_shopify_backup["path"], restore_password,
                                            restore_product_id,
                                        )
                                    )
                            except Exception as exc:
                                st.session_state.pop(viewer_key, None)
                                st.error(f"Productviewer openen mislukt: {exc}")
                        viewed_product = st.session_state.get(viewer_key)
                        if viewed_product:
                            st.markdown(f"### {viewed_product.get('title') or 'Zonder titel'}")
                            viewer_metrics = st.columns(4)
                            viewer_metrics[0].metric(
                                "Status", viewed_product.get("status") or "Onbekend"
                            )
                            viewer_metrics[1].metric(
                                "Leverancier", viewed_product.get("vendor") or "—"
                            )
                            viewer_metrics[2].metric(
                                "Varianten", len(viewed_product.get("variants") or [])
                            )
                            viewer_metrics[3].metric(
                                "Media", len(viewed_product.get("media") or [])
                            )
                            product_tabs = st.tabs([
                                "Product", "Afbeeldingen", "Varianten", "Metafields", "Technisch",
                            ])
                            with product_tabs[0]:
                                st.caption(
                                    f"Handle: {viewed_product.get('handle') or '—'} · "
                                    f"Producttype: {viewed_product.get('productType') or '—'}"
                                )
                                tags = viewed_product.get("tags") or []
                                if tags:
                                    st.write("Tags: " + ", ".join(tags))
                                description = viewed_product.get("descriptionHtml") or ""
                                if description:
                                    st.markdown(description, unsafe_allow_html=True)
                                else:
                                    st.info("Dit product heeft geen beschrijving in de back-up.")
                                seo = viewed_product.get("seo") or {}
                                if seo.get("title") or seo.get("description"):
                                    st.markdown("##### SEO")
                                    st.write(seo.get("title") or "—")
                                    st.caption(seo.get("description") or "Geen SEO-beschrijving")
                            with product_tabs[1]:
                                images = [
                                    media for media in viewed_product.get("media") or []
                                    if media.get("mediaContentType") == "IMAGE"
                                    and (media.get("image") or {}).get("url")
                                ]
                                if not images:
                                    st.info("Geen afbeeldingen gevonden in deze productback-up.")
                                else:
                                    for start in range(0, len(images), 3):
                                        columns = st.columns(3)
                                        for column, media in zip(columns, images[start:start + 3]):
                                            column.image(
                                                media["image"]["url"],
                                                caption=media.get("alt") or "Productafbeelding",
                                                width="stretch",
                                            )
                            with product_tabs[2]:
                                variants = viewed_product.get("variants") or []
                                if variants:
                                    st.dataframe([{
                                        "SKU": item.get("sku") or "",
                                        "Variant": item.get("title") or "",
                                        "Barcode": item.get("barcode") or "",
                                        "Prijs": item.get("price") or "",
                                        "Van-prijs": item.get("compareAtPrice") or "",
                                        "Voorraad back-up": item.get("inventoryQuantity"),
                                        "Opties": ", ".join(
                                            f"{value.get('name')}: {value.get('value')}"
                                            for value in item.get("selectedOptions") or []
                                        ),
                                    } for item in variants], hide_index=True,
                                        width="stretch", height=260)
                                else:
                                    st.info("Geen varianten gevonden.")
                            with product_tabs[3]:
                                metafields = viewed_product.get("metafields") or []
                                if metafields:
                                    st.dataframe([{
                                        "Namespace": item.get("namespace") or "",
                                        "Sleutel": item.get("key") or "",
                                        "Type": item.get("type") or "",
                                        "Waarde": item.get("value") or "",
                                    } for item in metafields], hide_index=True,
                                        width="stretch", height=260)
                                else:
                                    st.info("Geen metafields gevonden.")
                            with product_tabs[4]:
                                st.code(viewed_product.get("id") or "", language=None)
                                st.json({
                                    "createdAt": viewed_product.get("createdAt"),
                                    "updatedAt": viewed_product.get("updatedAt"),
                                    "publishedAt": viewed_product.get("publishedAt"),
                                    "templateSuffix": viewed_product.get("templateSuffix"),
                                    "options": viewed_product.get("options") or [],
                                })
                        restore_confirmed = st.checkbox(
                            "Ik begrijp dat dit een nieuw Shopify-concept aanmaakt",
                            key=f"restore_confirm_{selected_shopify_name}_{restore_product_id}",
                        )
                        if st.button(
                            "Geselecteerd product als concept terugzetten",
                            type="primary", disabled=not restore_confirmed,
                            key=f"restore_submit_{selected_shopify_name}_{restore_product_id}",
                            width="stretch",
                        ):
                            try:
                                with st.spinner("Product en afbeeldingen worden hersteld…"):
                                    restored = restore_product_from_shopify_backup(
                                        selected_shopify_backup["path"], restore_password,
                                        restore_product_id,
                                    )
                                st.success(
                                    f"{restored['title']} is als Shopify-concept hersteld "
                                    f"met {restored['variants']} varianten en "
                                    f"{restored['images']} afbeeldingen."
                                )
                            except Exception as exc:
                                st.error(f"Product herstellen mislukt: {exc}")

    st.stop()

st.title("Leverancierssynchronisatie")
st.caption("Leveranciersdata analyseren, beheren en voorbereiden voor Shopify")


def _format_sync_log_datetime(value: object) -> str:
    """Render stored UTC job timestamps in the Amsterdam business timezone."""
    if value in (None, ""):
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone(ZoneInfo(DEFAULT_TIMEZONE))
    except (TypeError, ValueError):
        return str(value)
    return f"{local:%d-%m-%Y %H:%M:%S}"


def _format_sync_log_duration(job: dict) -> str:
    """Format persisted or timestamp-derived duration without reload coupling."""
    seconds = job.get("duration_seconds")
    if seconds is None and job.get("started_at"):
        try:
            started = datetime.fromisoformat(str(job["started_at"]))
            finished = (
                datetime.fromisoformat(str(job["finished_at"]))
                if job.get("finished_at") else datetime.now(timezone.utc)
            )
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            seconds = max(0.0, (finished - started).total_seconds())
        except (TypeError, ValueError):
            seconds = None
    if seconds is None:
        return "—"
    total = int(round(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (
        f"{hours:d}:{minutes:02d}:{seconds:02d}"
        if hours else f"{minutes:d}:{seconds:02d}"
    )


@st.fragment(run_every=2)
def show_sync_job(slug: str) -> None:
    job = get_background_sync(slug)
    if job:
        status = job.get("status")
        terminal_refresh_key = f"sync_terminal_refresh_{slug}"
        if status in ("queued", "running", "cancel_requested"):
            st.session_state.pop(terminal_refresh_key, None)
        elif (
            status in ("completed", "failed", "cancelled")
            and st.session_state.get(terminal_refresh_key) != job.get("id")
        ):
            # Dit fragment ververst tijdens een achtergrondtaak zelfstandig.
            # Ververs bij de eindstatus eenmaal de hele app, zodat de knop
            # erboven niet in zijn oude disabled/running-toestand blijft staan.
            st.session_state[terminal_refresh_key] = job.get("id")
            st.rerun()
        if status in ("queued", "running", "cancel_requested"):
            try:
                heartbeat_at = datetime.fromisoformat(
                    str(job.get("updated_at") or "")
                )
                if heartbeat_at.tzinfo is None:
                    heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
                heartbeat_age = max(
                    0, int((datetime.now(timezone.utc) - heartbeat_at).total_seconds())
                )
            except (TypeError, ValueError):
                heartbeat_age = -1
            pid = int(job.get("pid") or 0)
            process_alive = False
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    process_alive = True
                except (OSError, ProcessLookupError):
                    pass
            clock_frames = ("🕛", "🕒", "🕕", "🕘")
            clock = clock_frames[int(datetime.now().timestamp() / 2) % 4]
            st.progress(
                int(job.get("progress") or 0),
                text=job.get("message") or "Synchronisatie wordt uitgevoerd…",
            )
            if status == "cancel_requested":
                st.info(f"{clock} De synchronisatie wordt gestopt…")
            elif not process_alive:
                st.error(
                    "⛔ Het achtergrondproces is niet meer actief. "
                    "De taakstatus wordt gecontroleerd."
                )
            elif heartbeat_age > 15 * 60:
                st.error(
                    f"{clock} Proces bestaat nog, maar al "
                    f"{heartbeat_age // 60} minuten geen nieuw controlepunt. "
                    "De synchronisatie is mogelijk vastgelopen."
                )
            elif heartbeat_age > 2 * 60:
                st.warning(
                    f"{clock} Achtergrondproces actief · laatste nieuw "
                    f"controlepunt {heartbeat_age // 60} minuten geleden."
                )
            else:
                age_text = (
                    f"{heartbeat_age} seconden geleden"
                    if heartbeat_age >= 0 else "onbekend"
                )
                st.info(
                    f"{clock} De synchronisatie draait als achtergrondtaak · "
                    f"laatste activiteit {age_text}. "
                    "Je kunt deze pagina veilig verlaten."
                )
        elif status == "completed":
            st.progress(100, text="Synchronisatie voltooid")
            result = job.get("result") or {}
            imported = result.get("import") or {}
            shopify = result.get("shopify") or {}
            st.success(
                f"Synchronisatie voltooid: {result.get('rows', 0)} bronregels; "
                f"{imported.get('inserted', 0)} nieuw, "
                f"{imported.get('updated', 0)} gewijzigd en "
                f"{imported.get('missing', 0)} niet meer in de bron."
                + (
                    f" Shopify: {shopify.get('active', 0)} actief, "
                    f"{shopify.get('draft', 0)} concept en "
                    f"{shopify.get('publications', 0)} verkoopkanalen."
                    if shopify else ""
                )
            )
        elif status == "failed":
            st.error(
                job.get("message") or job.get("error")
                or "Synchronisatie mislukt."
            )
        elif status == "cancelled":
            st.warning(job.get("message") or "Synchronisatie gestopt.")
    history = list_sync_history(slug)
    st.markdown(
        """
        <div style="margin-top:1rem;padding:.7rem 1rem;border-left:5px solid #2563eb;
                    border-radius:.45rem;background:#eff6ff;color:#1e3a8a;
                    font-weight:700;">
            📋 Synchronisatielogboek
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.expander(
        f"Synchronisatielog – maximaal 1 maand ({len(history)})"
    ):
        if not history:
            st.info("Er zijn nog geen synchronisaties gelogd.")
            return
        st.dataframe(
            pd.DataFrame([
                {
                    "Gestart": _format_sync_log_datetime(
                        item.get("started_at") or item.get("created_at")
                    ),
                    "Duur": _format_sync_log_duration(item),
                    "Status": item.get("status"),
                    "Voortgang": item.get("progress"),
                    "Melding": item.get("message")
                    or item.get("error"),
                }
                for item in history
            ]),
            width="stretch",
            hide_index=True,
        )
        selected_log_id = st.selectbox(
            "Open synchronisatie voor details",
            [item["id"] for item in history],
            format_func=lambda job_id: next(
                (
                    f"{_format_sync_log_datetime(item.get('started_at') or item.get('created_at'))} · "
                    f"{item.get('status')}"
                    for item in history if item["id"] == job_id
                ),
                job_id,
            ),
            key=f"sync_history_detail_{slug}",
        )
        selected_log = next(
            item for item in history if item["id"] == selected_log_id
        )
        # De actuele mislukte taak staat hierboven al als foutmelding. Toon
        # dezelfde fout niet direct daaronder nogmaals in het logdetail.
        current_failure_is_already_visible = bool(
            job
            and job.get("status") == "failed"
            and selected_log.get("id") == job.get("id")
        )
        if selected_log.get("error") and not current_failure_is_already_visible:
            st.error(selected_log["error"])
        log_result = selected_log.get("result") or {}
        imported = log_result.get("import") or {}
        shopify = log_result.get("shopify") or {}
        change_detection = shopify.get("change_detection") or {}
        detail_columns = st.columns(4)
        detail_columns[0].metric(
            "Bronregels", log_result.get("rows", 0)
        )
        detail_columns[1].metric(
            "Nieuw", imported.get("inserted", 0)
        )
        detail_columns[2].metric(
            "Gewijzigd", imported.get("updated", 0)
        )
        detail_columns[3].metric(
            "Naar Shopify", shopify.get("uploaded_or_updated", 0)
        )
        if change_detection:
            st.caption(
                "Modus: "
                + (
                    "alleen gewijzigd"
                    if change_detection.get("mode") == "changed_only"
                    else "volledig"
                )
                + f" · geselecteerd {change_detection.get('selected', 0)}"
                + " · ongewijzigd overgeslagen "
                + str(change_detection.get("unchanged_skipped", 0))
            )
            details = change_detection.get("details") or []
            if details:
                st.dataframe(
                    pd.DataFrame(details),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "sku": "SKU",
                        "reason": "Reden van synchronisatie",
                    },
                )


@st.fragment(run_every=5)
def show_enrichment_recovery_progress(slug: str) -> None:
    """Refresh only the selected supplier's recovery progress panel."""
    recovery = enrichment_recovery_status(slug)
    job = recovery.get("job") or {}
    if not job:
        return
    completed = int(job.get("completed") or 0)
    total = max(1, int(job.get("total") or 0))
    st.progress(
        min(1.0, completed / total),
        text=(
            f"Herstel {completed}/{total} · "
            f"{job.get('message') or job.get('status')}"
        ),
    )
    if job.get("status") in {"queued", "running"}:
        st.caption("Deze voortgang wordt iedere 5 seconden automatisch bijgewerkt.")


@st.fragment(run_every=5)
def show_tecweld_translation_controls() -> None:
    status = translation_status()
    job = status.get("job") or {}
    active = job.get("status") in {"queued", "running"}
    st.markdown("#### Nederlandse productvertalingen")
    st.caption(
        "Strikte Tecweld-vertaling van de productnaam, productgroep en klanttekst. "
        "De oorspronkelijke brongegevens blijven behouden."
    )
    metric_columns = st.columns(3)
    metric_columns[0].metric("Nog te vertalen", status["eligible"])
    metric_columns[1].metric("Goedgekeurd", status["approved"])
    status_labels = {
        "queued": "In wachtrij",
        "running": "Bezig",
        "completed": "Voltooid",
        "completed_with_errors": "Voltooid met fouten",
        "failed": "Mislukt",
        "cancel_requested": "Annuleren aangevraagd",
        "cancelled": "Geannuleerd",
    }
    raw_status = job.get("status")
    metric_columns[2].metric(
        "Status",
        status_labels.get(raw_status, "Nog niet gestart"),
    )
    if active:
        completed = int(job.get("completed_products") or 0)
        total = max(1, int(job.get("total_products") or 0))
        st.progress(
            min(1.0, completed / total),
            text=f"Producten {completed}/{total} · {job.get('message') or 'Bezig…'}",
        )
        st.info(
            "Er draait al een Tecweld-contenttaak. Een tweede taak kan niet "
            "tegelijk worden gestart."
        )
    elif job and job.get("status") in {"completed", "completed_with_errors"}:
        if int(job.get("failed") or 0):
            st.warning(
                f"Laatste run afgerond met {job.get('failed')} fout(en)."
            )
        else:
            st.success("Laatste Nederlandse vertaalrun is voltooid.")
    confirm_costs = st.checkbox(
        "Ik bevestig dat deze vertaalrun AI-verbruikskosten veroorzaakt",
        key="confirm_tecweld_product_translation_costs",
        disabled=active,
    )
    if st.button(
        "Nederlandse vertaalrun starten",
        type="primary",
        key="start_tecweld_product_translation",
        disabled=active or not confirm_costs or status["eligible"] == 0,
    ):
        start_product_translation()
        st.session_state["products_table_revision_tecweld"] = (
            int(st.session_state.get("products_table_revision_tecweld", 0)) + 1
        )
        selected_products = st.session_state.get(
            "selected_products_by_supplier"
        )
        if isinstance(selected_products, dict):
            selected_products.pop("tecweld", None)
        st.rerun()


def euro(value: object) -> str:
    if value is None or value == "":
        return "Prijs niet beschikbaar"
    try:
        number = float(str(value).strip().replace(".", "").replace(",", ".")) if isinstance(value, str) and "," in value else float(value)
        return f"€ {number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(value)


EURO_PRODUCT_FIELDS = {
    "price", "sale_price", "cost_price", "gross_purchase_price_per_kg",
    "net_purchase_price_per_kg",
}


def _is_euro_source_field(field: str) -> bool:
    normalized = field.casefold()
    if any(currency in normalized for currency in ("pln", "usd", "gbp", "dollar")):
        return False
    return "eur" in normalized or any(
        marker in normalized
        for marker in ("price", "prijs", "cost", "kost", "amount", "bedrag")
    )


def _display_product_value(field: str, value: object, *, source: bool = False) -> str:
    if value is None or value == "":
        return "—"
    if (source and _is_euro_source_field(field)) or field in EURO_PRODUCT_FIELDS:
        return euro(value)
    if field == "purchase_discount_percent" or "discount" in field.casefold():
        try:
            return f"{float(value):g}%"
        except (TypeError, ValueError):
            pass
    if isinstance(value, bool):
        return "Ja" if value else "Nee"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _flatten_source_data(
    value: object, prefix: str = ""
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(value, dict):
        for field, nested_value in value.items():
            label = f"{prefix} › {field}" if prefix else str(field)
            if isinstance(nested_value, (dict, list)):
                rows.extend(_flatten_source_data(nested_value, label))
            else:
                rows.append({
                    "Bronveld": label,
                    "Waarde": _display_product_value(
                        label, nested_value, source=True
                    ),
                })
    elif isinstance(value, list):
        for index, nested_value in enumerate(value, start=1):
            label = f"{prefix} › {index}" if prefix else str(index)
            if isinstance(nested_value, (dict, list)):
                rows.extend(_flatten_source_data(nested_value, label))
            else:
                rows.append({
                    "Bronveld": label,
                    "Waarde": _display_product_value(
                        label, nested_value, source=True
                    ),
                })
    return rows


@st.dialog("Prijsopbouw", width="small")
def show_purchase_price_details(product: dict) -> None:
    st.caption(f"Alleen intern · SKU {product.get('sku') or '—'}")
    st.metric("Bruto prijs", euro(product.get("price")))
    discount = product.get("purchase_discount_percent")
    st.metric(
        "Inkoopkorting",
        f"{float(discount):g}%" if discount is not None else "—",
    )
    st.metric("Netto inkoopprijs", euro(product.get("cost_price")))


@st.dialog("Productinformatie", width="large")
def show_supplier_product_details(supplier_slug: str, sku: str) -> None:
    product = get_supplier_product(supplier_slug, sku)
    if not product:
        st.error("Dit product is niet meer beschikbaar.")
        return

    st.subheader(
        (
            product.get("ai_title")
            if supplier_slug == "tecweld" else product.get("source_title")
        )
        or product.get("source_title")
        or product.get("ai_title")
        or product.get("source_description")
        or sku
    )
    st.caption(f"Leverancier: {supplier_slug} · SKU: {sku}")
    if st.button(
        "Sla op in Shopify",
        key=f"supplier_product_shopify_{supplier_slug}_{sku}",
        type="primary",
        width="stretch",
    ):
        try:
            with st.spinner("Shopify-SKU controleren en opslaan…"):
                result = upload_pim_product_draft(supplier_slug, sku)
            if result.get("target_type") == "variant":
                st.success(f"Variant {result['sku']} is bijgewerkt in Shopify.")
            else:
                st.success(
                    f"Product {result['sku']} is als Shopify-concept opgeslagen."
                )
            st.markdown(f"[Open product in Shopify]({result['admin_url']})")
        except Exception as exc:
            st.error(f"Opslaan in Shopify mislukt: {exc}")
    price_columns = st.columns(3)
    price_columns[0].metric("Inkoopprijs", euro(product.get("price")))
    price_columns[1].metric("Verkoopprijs", euro(product.get("sale_price")))
    price_columns[2].metric("Kostprijs", euro(product.get("cost_price")))
    invoice_history = list_product_invoice_evidence(supplier_slug, sku)
    tabs = st.tabs(
        [
            "Productgegevens",
            "Brondata",
            "Afbeeldingen",
            f"Inkoopfacturen ({len(invoice_history)})",
        ]
    )
    details_tab, source_data_tab, images_tab = tabs[:3]
    with details_tab:
        excluded_fields = {"images", "raw_data", "raw_data_json"}
        product_fields = [
            {
                "Veld": field,
                "Waarde": _display_product_value(field, value),
            }
            for field, value in product.items()
            if field not in excluded_fields
        ]
        st.dataframe(
            pd.DataFrame(product_fields),
            width="stretch",
            hide_index=True,
            column_config={
                "Veld": st.column_config.TextColumn(width="medium"),
                "Waarde": st.column_config.TextColumn(width="large"),
            },
        )
    with source_data_tab:
        raw_data = product.get("raw_data") or {}
        if raw_data:
            source_rows = _flatten_source_data(raw_data)
            st.dataframe(
                pd.DataFrame(source_rows),
                width="stretch",
                hide_index=True,
                column_config={
                    "Bronveld": st.column_config.TextColumn(width="medium"),
                    "Waarde": st.column_config.TextColumn(width="large"),
                },
            )
        else:
            st.info("Voor dit product is geen aanvullende ruwe brondata opgeslagen.")
    with images_tab:
        images = product.get("images") or []
        if images:
            for image in images:
                st.image(
                    image["image_url"],
                    caption=(
                        image.get("alt_text")
                        or f"Afbeelding {image.get('position') or '—'}"
                    ),
                    width="stretch",
                )
                st.caption(image["image_url"])
        else:
            st.info("Voor dit product zijn geen afbeeldingen opgeslagen.")
    with tabs[3]:
        if not invoice_history:
            st.info("Voor dit product zijn nog geen inkoopfacturen gekoppeld.")
        else:
            st.caption("Nieuwste factuur bovenaan · alleen intern zichtbaar")
            with st.container(height=430, border=True):
                for invoice in invoice_history:
                    with st.container(border=True):
                        st.markdown(
                            f"**Factuur {invoice['invoice_number']}** · "
                            f"{invoice.get('invoice_date') or 'datum onbekend'}"
                        )
                        st.caption(
                            f"Regel {invoice['line_number']} · "
                            f"leveranciersartikel {invoice['supplier_article_number']} · "
                            f"aantal {invoice.get('quantity') or '—'} · "
                            f"inkoop {euro(invoice.get('net_unit_price'))}"
                        )
                        if invoice.get("erp_url"):
                            actions = st.columns(2)
                            actions[0].link_button(
                                "Originele PDF", invoice["pdf_url"],
                                width="stretch",
                            )
                            actions[1].link_button(
                                "Open factuur", invoice["erp_url"],
                                width="stretch",
                            )
                        else:
                            st.caption("ERP-koppeling ontbreekt")


def safe_description(value: str) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    for forbidden in soup(["script", "style", "iframe", "object", "embed", "form"]):
        forbidden.decompose()
    allowed = {"p", "br", "strong", "b", "em", "i", "ul", "ol", "li", "h2", "h3", "h4", "table", "thead", "tbody", "tr", "th", "td", "img"}
    for tag in soup.find_all(True):
        if tag.name not in allowed:
            tag.unwrap()
        elif tag.name == "img":
            source = str(tag.get("src") or "").strip()
            if not source.startswith("https://"):
                tag.decompose()
                continue
            tag.attrs = {
                key: value for key, value in tag.attrs.items()
                if key in {"src", "alt", "width", "height", "loading"}
            }
        else:
            tag.attrs = {}
    return str(soup)

suppliers = list_suppliers()
supplier_names = {row["name"]: row["slug"] for row in suppliers}
supplier_options = ["— Nieuwe leverancier —", *supplier_names.keys()]
requested_supplier = str(st.query_params.get("supplier") or "")
requested_supplier_name = next(
    (
        name for name, slug in supplier_names.items()
        if slug == requested_supplier
    ),
    None,
)
if (
    "pim_selected_supplier_name" not in st.session_state
    or st.session_state["pim_selected_supplier_name"] not in supplier_options
):
    st.session_state["pim_selected_supplier_name"] = (
        requested_supplier_name or supplier_options[0]
    )

with st.sidebar:
    st.header("Leverancier")
    selected_name = st.selectbox(
        "Selecteer leverancier",
        supplier_options,
        key="pim_selected_supplier_name",
    )
    selected_slug = supplier_names.get(selected_name)
    if selected_slug and st.query_params.get("supplier") != selected_slug:
        st.query_params["supplier"] = selected_slug
    elif "supplier" in st.query_params:
        del st.query_params["supplier"]

if not selected_slug:
    st.subheader("Nieuwe leverancier toevoegen")
    st.info("Iedere leverancier krijgt een eigen database en eigen bronconfiguratie.")
    with st.form("new_supplier"):
        name = st.text_input("Leveranciersnaam")
        source_type = st.selectbox(
            "Type databron",
            ["xml_url", "json_url", "csv_url", "pdf_url", "xml_upload", "json_upload", "csv_upload", "excel_upload"],
            format_func=lambda value: {
                "xml_url": "XML via URL/API",
                "json_url": "JSON via URL/API",
                "csv_url": "CSV via URL/API",
                "pdf_url": "PDF via beveiligde URL",
                "xml_upload": "XML-bestand uploaden",
                "json_upload": "JSON-bestand uploaden",
                "csv_upload": "CSV-bestand uploaden",
                "excel_upload": "Excel-bestand uploaden",
            }[value],
        )
        source_location = st.text_input("URL of bronlocatie")
        website_url = st.text_input(
            "Officiële leverancierswebsite (aanvullende bron)",
            placeholder="https://leverancier.example/nl/",
        )
        ai_research_allowed = st.checkbox(
            "AI mag deze website doorzoeken voor ontbrekende aanvullende gegevens"
        )
        auth_type = st.selectbox(
            "Authenticatie",
            ["none", "basic", "bearer", "api_key_header", "wordpress_post_password"],
            format_func=lambda value: {
                "none": "Geen",
                "basic": "Gebruikersnaam + wachtwoord/token",
                "bearer": "Bearer token",
                "api_key_header": "API-key in HTTP-header",
                "wordpress_post_password": "WordPress-documentwachtwoord",
            }[value],
        )
        username = st.text_input("Gebruikersnaam", autocomplete="off")
        secret = st.text_input("Wachtwoord/token", type="password", autocomplete="new-password")
        submitted = st.form_submit_button("Leverancier opslaan", type="primary")
    if submitted:
        if not name.strip():
            st.error("Vul een leveranciersnaam in.")
        else:
            slug = save_supplier(
                name=name,
                source_type=source_type,
                source_location=source_location,
                auth_type=auth_type,
                username=username or None,
                secret=secret or None,
                website_url=website_url,
                ai_research_allowed=ai_research_allowed,
            )
            st.success(f"{name} is toegevoegd met een eigen database ({slug}).")
            st.rerun()
    st.stop()

supplier = get_supplier(selected_slug) or {}
from app.suppliers.routes import supplier_route
selected_route = supplier_route(selected_slug)
st.subheader(supplier.get("name", selected_slug))

overview_tab, source_tab, families_tab, products_tab, viewer_tab, shopify_tab = st.tabs(
    [
        "Overzicht", "Bron & import", "Productfamilies", "Producten",
        "Productviewer", "Shopify",
    ]
)

with overview_tab:
    stats = supplier_stats(selected_slug)
    columns = st.columns(6)
    labels = [
        ("Producten", "total"),
        ("Actueel", "active"),
        ("Niet meer in bron", "missing"),
        ("Beschikbaar", "available"),
        ("Met omschrijving", "descriptions"),
        ("Afbeeldingen", "images"),
    ]
    for column, (label, key) in zip(columns, labels):
        column.metric(label, stats[key])
    if supplier.get("last_run_at"):
        st.caption(
            f"Laatste import: {supplier['last_run_at']} · "
            f"{supplier.get('last_run_status') or 'onbekend'}"
        )
    else:
        st.info("Voor deze leverancier is nog geen import uitgevoerd.")

    with st.expander("Database opschonen", expanded=False):
        cleanup_preview = supplier_database_cleanup_preview(selected_slug)
        cleanup_columns = st.columns(3)
        cleanup_columns[0].metric(
            "Totaal in PIM", cleanup_preview["total"]
        )
        cleanup_columns[1].metric(
            "Actueel", cleanup_preview["active"]
        )
        cleanup_columns[2].metric(
            "Niet meer in bron", cleanup_preview["missing"]
        )
        st.warning(
            "Opschonen verwijdert uitsluitend lokale PIM-producten die niet "
            "meer in de actuele leveranciersbron staan. Shopify wordt niet "
            "aangepast. De verwijderde PIM-historie kan daarna niet meer voor "
            "de Shopify-bewaartermijn worden gebruikt."
        )
        cleanup_result = st.session_state.pop(
            f"cleanup_result_{selected_slug}", None
        )
        if cleanup_result:
            st.success(
                f"{cleanup_result['removed']} oude PIM-producten en "
                f"{cleanup_result['removed_images']} afbeeldingen verwijderd. "
                f"Herstelarchief: {cleanup_result['archive']}"
            )
        confirm_cleanup = st.checkbox(
            (
                "Ik bevestig dat de niet-actuele PIM-producten mogen "
                "worden gearchiveerd en verwijderd"
            ),
            key=f"confirm_cleanup_{selected_slug}",
        )
        run_cleanup = st.button(
            "Database opschonen",
            disabled=(
                not confirm_cleanup
                or cleanup_preview["missing"] == 0
            ),
            key=f"cleanup_supplier_database_{selected_slug}",
        )
        if run_cleanup:
            st.session_state[f"cleanup_result_{selected_slug}"] = (
                cleanup_missing_supplier_products(selected_slug)
            )
            st.rerun()

with source_tab:
    (
        source_setup_subtab,
        source_analysis_subtab,
        source_mapping_subtab,
        source_inventory_subtab,
        source_purchase_pricing_subtab,
        source_sales_pricing_subtab,
        source_sync_subtab,
        source_enrichment_rules_subtab,
    ) = st.tabs([
        "1. Bron instellen",
        "2. Inlezen & analyseren",
        "3. Koppelingen",
        "4. Voorraad & status",
        "5. Inkoopprijzen",
        "6. Verkoopprijzen",
        "7. Synchronisatie",
        "8. Verrijkingsregels",
    ])

with source_setup_subtab:
    st.markdown("#### Leveranciersbron onderzoeken")
    st.caption(
        "Vul alleen de informatie in die je hebt. PIM onderzoekt het formaat, "
        "de bereikbaarheid, de vermoedelijke inlogmethode en mogelijke veldkoppelingen. "
        "Er wordt niets opgeslagen of geïmporteerd."
    )
    research_url = st.text_input(
        "Te onderzoeken bron-URL",
        placeholder="https://leverancier.example/productfeed?customer=...",
        key=f"source_research_url_{selected_slug}",
    )
    if st.button(
        "Bron en inlogprocedure onderzoeken",
        disabled=not research_url.strip(),
        key=f"research_source_{selected_slug}",
    ):
        try:
            with st.spinner("Bron veilig onderzoeken…"):
                st.session_state[f"source_research_{selected_slug}"] = (
                    research_supplier_source(research_url)
                )
        except Exception as exc:
            st.error(f"Onderzoek mislukt: {exc}")
    research = st.session_state.get(f"source_research_{selected_slug}")
    if research:
        if research.get("ok"):
            summary_columns = st.columns(4)
            summary_columns[0].metric("HTTP-status", research["http_status"])
            summary_columns[1].metric("Formaat", research["format"])
            summary_columns[2].metric("Inlogmethode", research["auth_type"])
            summary_columns[3].metric("Velden", len(research["fields"]))
            st.info(research["authentication_explanation"])
            for warning in research.get("warnings") or []:
                st.warning(warning)
            for note in research.get("notes") or []:
                st.caption(note)
            st.markdown("**Voorgestelde veldkoppeling**")
            mapping_rows = [
                {"PIM-veld": target, "Bronveld": field or "Niet gevonden"}
                for target, field in research["suggested_mapping"].items()
            ]
            st.dataframe(
                pd.DataFrame(mapping_rows), width="stretch", hide_index=True
            )
            with st.expander("Gevonden velden en voorbeeldrecords"):
                st.write(", ".join(research["fields"]))
                st.dataframe(
                    pd.DataFrame(research["sample"]),
                    width="stretch",
                    hide_index=True,
                )
            if st.button(
                "Gevonden instellingen invullen",
                type="primary",
                key=f"apply_research_{selected_slug}",
                help=(
                    f"Vult de broninstellingen van {supplier['name']} in. "
                    "Definitief opslaan gebeurt pas via Instellingen opslaan."
                ),
            ):
                source_type_by_format = {
                    "XML": "xml_url",
                    "JSON": "json_url",
                    "CSV": "csv_url",
                }
                st.session_state[f"source_type_{selected_slug}"] = (
                    source_type_by_format.get(research["format"], "xml_url")
                )
                st.session_state[f"source_location_{selected_slug}"] = (
                    research["final_url"]
                )
                # URL-parameters worden onderdeel van de bronlocatie. Er is
                # geen afzonderlijke HTTP Authorization-header nodig.
                st.session_state[f"auth_type_{selected_slug}"] = (
                    "none"
                    if research["auth_type"] in {"none", "url_parameters"}
                    else research["auth_type"]
                )
                st.session_state[f"research_mapping_{selected_slug}"] = {
                    key: value
                    for key, value in research["suggested_mapping"].items()
                    if value
                }
                st.session_state[f"research_prefilled_{selected_slug}"] = True
                st.rerun()
        else:
            st.warning(research.get("message") or "Bron vereist aanvullende gegevens.")
            st.write("Vermoedelijke inlogmethode:", research.get("auth_type"))

with source_analysis_subtab:
    st.markdown("#### Bron inlezen en voorbeeld selecteren")
    upload = None
    if "upload" in supplier.get("source_type", ""):
        if supplier.get("source_type") == "excel_upload":
            header_col, save_col = st.columns(
                [2, 1], vertical_alignment="bottom"
            )
            with header_col:
                excel_header_row = st.number_input(
                    "Regel met kolomnamen",
                    min_value=1,
                    max_value=10_000,
                    value=max(
                        1,
                        int(
                            supplier.get("request_options", {}).get(
                                "excel_header_row"
                            ) or 1
                        ),
                    ),
                    step=1,
                    help=(
                        "Tel zoals in Excel vanaf 1. Staat de kolomnaam op "
                        "de eerste regel, vul dan 1 in."
                    ),
                    key=f"excel_header_row_upload_{selected_slug}",
                )
            with save_col:
                if st.button(
                    "Kolomnaamregel opslaan",
                    key=f"save_excel_header_row_{selected_slug}",
                    width="stretch",
                ):
                    save_excel_header_row(
                        selected_slug, int(excel_header_row)
                    )
                    st.success("Regel met kolomnamen opgeslagen.")
                    st.rerun()
        upload = st.file_uploader(
            "Selecteer bronbestand",
            type=["xml", "json", "csv", "xlsx", "xls"],
        )
    analyze_button_col, enrich_button_col, analyze_clock_col = st.columns(
        [3, 3, 2], vertical_alignment="center"
    )
    with analyze_button_col:
        analyze_source_clicked = st.button(
            "Bron lezen en analyseren",
            type="primary",
            key=f"analyze_source_top_{selected_slug}",
            width="stretch",
        )
    with enrich_button_col:
        if selected_slug == "kentie":
            kentie_status = kentie_enrichment_status()
            kentie_job = kentie_status.get("job") or {}
            kentie_running = kentie_job.get("status") in {"queued", "running"}
            if kentie_running:
                if st.button(
                    "Kentie-verrijking stoppen",
                    key="stop_kentie_bulk_enrichment",
                    width="stretch",
                    help="Stopt de Kentie-taak en bewaart de voortgang per SKU.",
                ):
                    stop_kentie_enrichment()
                    st.warning("Kentie-verrijking is gepauzeerd; voortgang is bewaard.")
                    st.rerun()
            elif kentie_job.get("status") == "paused":
                resume_col, restart_col = st.columns(2)
                if resume_col.button(
                    "Doorgaan",
                    key="resume_kentie_bulk_enrichment",
                    type="primary",
                    width="stretch",
                    help="Gaat alleen verder met de resterende Kentie-SKU's.",
                ):
                    resume_kentie_enrichment()
                    st.success("Kentie gaat verder vanaf de opgeslagen voortgang.")
                    st.rerun()
                if restart_col.button(
                    "Opnieuw",
                    key="restart_kentie_bulk_enrichment",
                    width="stretch",
                    help="Maakt een nieuwe Kentie-werklijst vanaf het eerste artikel.",
                ):
                    restart_kentie_enrichment(5)
                    st.warning("Kentie-verrijking is helemaal opnieuw gestart.")
                    st.rerun()
            elif st.button(
                "Kentie-catalogus rustig verrijken",
                key="start_kentie_bulk_enrichment",
                width="stretch",
                disabled=kentie_status["total"] == 0,
                help=(
                    "Verwerkt Kentie rustig op de achtergrond met exacte SKU-matches "
                    "en bewaart tekst, bronpagina en foto's in de PIM."
                ),
            ):
                start_kentie_enrichment(5)
                st.success("Kentie-bulkverrijking is op de achtergrond gestart.")
                st.rerun()
            if kentie_job:
                completed = int(kentie_job.get("completed") or 0)
                total = max(1, int(kentie_job.get("total") or 0))
                st.progress(
                    min(1.0, completed / total),
                    text=(
                        f"{completed}/{total} · {kentie_job.get('message') or kentie_job.get('status')}"
                    ),
                )
        elif selected_slug == "tecweld":
            tecweld_status = tecweld_enrichment_status()
            tecweld_job = tecweld_status.get("job") or {}
            tecweld_running = tecweld_job.get("status") in {"queued", "running"}
            if tecweld_running:
                if st.button(
                    "Tecweld-verrijking stoppen",
                    key="stop_tecweld_bulk_enrichment",
                    type="secondary",
                    width="stretch",
                    help="Stopt na het actuele verzoek en bewaart de voortgang per SKU.",
                ):
                    stop_tecweld_enrichment()
                    st.warning("Tecweld-verrijking is gepauzeerd; voortgang is bewaard.")
                    st.rerun()
            elif tecweld_job.get("status") == "paused":
                resume_col, restart_col = st.columns(2)
                if resume_col.button(
                    "Doorgaan",
                    key="resume_tecweld_bulk_enrichment",
                    type="primary",
                    width="stretch",
                    help="Gaat verder met uitsluitend de nog niet afgeronde SKU's.",
                ):
                    resume_tecweld_enrichment()
                    st.success("Tecweld-verrijking gaat verder vanaf de opgeslagen voortgang.")
                    st.rerun()
                if restart_col.button(
                    "Opnieuw",
                    key="restart_tecweld_bulk_enrichment",
                    width="stretch",
                    help="Begint met een nieuwe werklijst vanaf het eerste artikel.",
                ):
                    restart_tecweld_enrichment(5)
                    st.warning("Tecweld-verrijking is helemaal opnieuw gestart.")
                    st.rerun()
            elif st.button(
                "Tecweld-catalogus rustig verrijken",
                key="start_tecweld_bulk_enrichment",
                width="stretch",
                disabled=tecweld_status["total"] == 0,
                help=(
                    "Verwerkt Tecweld rustig op de achtergrond met exacte "
                    "artikelnummatches en bewaart tekst, specificaties, bronpagina "
                    "en foto's in de PIM."
                ),
            ):
                start_tecweld_enrichment(5)
                st.success("Tecweld-bulkverrijking is op de achtergrond gestart.")
                st.rerun()
            if tecweld_job:
                completed = int(tecweld_job.get("completed") or 0)
                total = max(1, int(tecweld_job.get("total") or 0))
                st.progress(
                    min(1.0, completed / total),
                    text=(
                        f"{completed}/{total} · "
                        f"{tecweld_job.get('message') or tecweld_job.get('status')}"
                    ),
                )
        recovery_status = enrichment_recovery_status(selected_slug)
        recovery_job = recovery_status.get("job") or {}
        recovery_running = recovery_job.get("status") in {"queued", "running"}
        source_running = recovery_status.get("source_job_status") in {
            "queued", "running"
        }
        if recovery_running:
            if st.button(
                "Herstelrun stoppen",
                key=f"stop_enrichment_recovery_{selected_slug}",
                width="stretch",
                help=(
                    "Stopt alleen de herstelrun van de geselecteerde leverancier; "
                    "de voortgang per artikel blijft bewaard."
                ),
            ):
                stop_enrichment_recovery(selected_slug)
                st.warning(
                    f"Herstelrun voor {supplier.get('name', selected_slug)} is gepauzeerd."
                )
                st.rerun()
        elif recovery_job.get("status") == "paused":
            if st.button(
                "Herstelrun doorgaan",
                key=f"resume_enrichment_recovery_{selected_slug}",
                type="primary",
                width="stretch",
                help="Hervat alleen de resterende artikelen uit deze herstelrun.",
            ):
                resume_enrichment_recovery(selected_slug)
                st.success(
                    f"Herstelrun voor {supplier.get('name', selected_slug)} is hervat."
                )
                st.rerun()
        else:
            recoverable = int(recovery_status.get("recoverable") or 0)
            if st.button(
                f"Mislukte verrijking hervatten ({recoverable})",
                key=f"start_enrichment_recovery_{selected_slug}",
                type="primary" if recoverable else "secondary",
                width="stretch",
                disabled=(
                    not recovery_status.get("supported")
                    or recoverable == 0
                    or source_running
                ),
                help=(
                    "Zoekt in de laatste verrijkingsrun van alleen deze leverancier "
                    "naar mislukte en onafgeronde artikelen. Eerdere successen en "
                    "andere leveranciers worden niet aangepast."
                    if recovery_status.get("supported") else
                    "Voor deze leverancier is nog geen verrijkingshistorie per artikel."
                ),
            ):
                start_enrichment_recovery(selected_slug, 5)
                st.success(
                    f"Alleen de {recoverable} mislukte of onafgeronde artikelen van "
                    f"{supplier.get('name', selected_slug)} zijn gestart."
                )
                st.rerun()
            if source_running and recoverable:
                st.caption(
                    "Stop eerst de bestaande verrijkingsrun van deze leverancier; "
                    "daarna wordt de herstelknop beschikbaar."
                )
        show_enrichment_recovery_progress(selected_slug)
    analyze_clock = analyze_clock_col.empty()
    if analyze_source_clicked:
        analyze_clock.markdown(
            '<div class="pim-source-loading">'
            '<span class="clock">🕒</span><span>Bron wordt ingelezen…</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        analyze_supplier_source(selected_slug, upload)
        analyze_clock.empty()
    analyzed_source = st.session_state.get(f"analysis_{selected_slug}")
    if analyzed_source and analyzed_source.records:
        example_mapping = {
            **suggest_source_field_mapping(analyzed_source.fields),
            **(supplier.get("field_mapping") or {}),
        }
        example_sku_field = example_mapping.get("sku") or "sku"
        example_title_field = example_mapping.get("title") or "title"
        example_description_field = (
            example_mapping.get("description") or "description"
        )
        central_example_key = (
            f"central_example_product_{selected_slug}"
        )
        central_options = [-1, *range(len(analyzed_source.records))]
        if st.session_state.get(
            central_example_key, -1
        ) not in central_options:
            st.session_state[central_example_key] = -1
        selected_example_index = st.selectbox(
            "Zoek voorbeeldproduct",
            central_options,
            format_func=lambda index: (
                "Geen selectie – eerste product gebruiken"
                if index == -1 else
                f"{analyzed_source.records[index].get(example_sku_field) or 'Geen SKU'}"
                f" · {source_record_product_name(analyzed_source.records[index], example_title_field, example_description_field)[:100]}"
            ),
            key=central_example_key,
            help=(
                "Klik en typ een SKU of producttitel. Dit product wordt in "
                "alle koppelingen en bewerkingsregels als voorbeeld gebruikt."
            ),
        )
        effective_example_index = (
            selected_example_index if selected_example_index >= 0 else 0
        )
        selected_example_record = analyzed_source.records[
            effective_example_index
        ]
        st.caption(
            "Actief voorbeeld: "
            f"{selected_example_record.get(example_sku_field) or 'Geen SKU'}"
            f" · {source_record_product_name(selected_example_record, example_title_field, example_description_field)}"
        )

with source_setup_subtab:
    st.divider()
    st.markdown("#### Broninstellingen")
    if st.session_state.get(f"research_prefilled_{selected_slug}"):
        st.success(
            "De gevonden broninstellingen zijn ingevuld. Controleer ze en klik "
            "op Instellingen opslaan."
        )
    with st.form("source_settings"):
        source_type = st.selectbox(
            "Type databron",
            ["xml_url", "json_url", "csv_url", "pdf_url", "xml_upload", "json_upload", "csv_upload", "excel_upload"],
            index=["xml_url", "json_url", "csv_url", "pdf_url", "xml_upload", "json_upload", "csv_upload", "excel_upload"].index(
                supplier.get("source_type", "xml_url")
            ),
            key=f"source_type_{selected_slug}",
        )
        source_location = st.text_input(
            "URL of bronlocatie",
            value=supplier.get("source_location") or "",
            key=f"source_location_{selected_slug}",
        )
        excel_sheet = st.text_input(
            "Excel-werkblad",
            value=(
                supplier.get("request_options", {}).get("excel_sheet")
                or ""
            ),
            placeholder="Bijvoorbeeld NL - Upload",
            help=(
                "Alleen voor Excel-bronnen. Leeg gebruikt het eerste werkblad."
            ),
            disabled=source_type != "excel_upload",
        )
        sku_prefix = st.text_input(
            "SKU-voorloopcode",
            value=(
                supplier.get("request_options", {}).get("sku_prefix")
                or ""
            ),
            placeholder="Bijvoorbeeld VP- of HLT-",
            help=(
                "Deze code wordt vóór het artikelnummer uit de leveranciersbron "
                "gezet voor de exacte Shopify-SKU-koppeling. Een SKU die de code "
                "al heeft, krijgt hem niet nogmaals."
            ),
        )
        website_url = st.text_input(
            "Officiële leverancierswebsite",
            value=supplier.get("website_url") or "",
            help=(
                "Alleen voor handleidingen en aanvullende gegevens die via exacte SKU, "
                "EAN of een rechtstreeks gekoppelde productpagina aan het product zijn te herleiden."
            ),
        )
        ai_research_allowed = st.checkbox(
            "AI mag deze website doorzoeken voor aanvullende informatie",
            value=bool(supplier.get("ai_research_allowed")),
            help="De XML/API blijft leidend en wordt nooit door websitegegevens overschreven.",
        )
        st.caption(
            "Bronregel: XML/API is bepalend. Websitegegevens zijn uitsluitend aanvullend "
            "en vereisen een exacte productmatch."
        )
        st.markdown("##### Aanvullende bronnen")
        catalogue_url = st.text_input(
            "Cataloguspagina",
            value=supplier.get("catalogue_url") or "",
            placeholder="https://certilas.com/nl/pdf-catalogus",
            help=(
                "Openbare pagina of PDF met aanvullende technische gegevens. "
                "Productkoppeling blijft alleen via exacte SKU/EAN toegestaan."
            ),
        )
        dealer_portal_url = st.text_input(
            "Dealerportaal loginpagina",
            value=supplier.get("dealer_portal_url") or "",
            placeholder="https://portaal.certilas.nl/nl/login/",
        )
        dealer_username = st.text_input(
            "Nieuwe dealer-gebruikersnaam (leeg = huidige behouden)",
            placeholder=(
                "Versleuteld opgeslagen"
                if supplier.get("has_dealer_username") else ""
            ),
            autocomplete="off",
        )
        dealer_secret = st.text_input(
            "Nieuw dealer-wachtwoord (leeg = huidige behouden)",
            type="password",
            placeholder=(
                "Versleuteld opgeslagen"
                if supplier.get("has_dealer_secret") else ""
            ),
            autocomplete="new-password",
            help=(
                "Het wachtwoord wordt versleuteld opgeslagen en nooit aan "
                "het AI-model gegeven."
            ),
        )
        save_additional_sources = st.form_submit_button(
            "Catalogus en dealerlogin opslaan",
            help=(
                "Slaat de aanvullende bron-URL's en eventuele nieuwe "
                "dealercredentials versleuteld op."
            ),
        )
        auth_type = st.selectbox(
            "Authenticatie",
            ["none", "basic", "bearer", "api_key_header", "wordpress_post_password"],
            index=["none", "basic", "bearer", "api_key_header", "wordpress_post_password"].index(supplier.get("auth_type", "none")),
            format_func=lambda value: {
                "none": "Geen",
                "basic": "Gebruikersnaam + wachtwoord/token",
                "bearer": "Bearer token",
                "api_key_header": "API-key in HTTP-header",
                "wordpress_post_password": "WordPress-documentwachtwoord",
            }[value],
            key=f"auth_type_{selected_slug}",
        )
        username = st.text_input(
            "Nieuwe gebruikersnaam (leeg = huidige behouden)",
            placeholder="Opgeslagen" if supplier.get("has_username") else "",
            autocomplete="off",
        )
        secret = st.text_input(
            "Nieuw wachtwoord/token (leeg = huidige behouden)",
            type="password",
            placeholder="Veilig opgeslagen" if supplier.get("has_secret") else "",
            autocomplete="new-password",
        )
        save_settings = st.form_submit_button("Instellingen opslaan")
    if save_settings or save_additional_sources:
        researched_mapping = st.session_state.get(
            f"research_mapping_{selected_slug}", {}
        )
        save_supplier(
            name=supplier["name"],
            slug=selected_slug,
            source_type=source_type,
            source_location=source_location,
            auth_type=auth_type,
            username=username if username else None,
            secret=secret if secret else None,
            request_options={
                **(supplier.get("request_options") or {}),
                "excel_sheet": excel_sheet.strip(),
                "sku_prefix": sku_prefix.strip().upper(),
            },
            field_mapping={
                **(supplier.get("field_mapping") or {}),
                **researched_mapping,
            },
            website_url=website_url,
            catalogue_url=catalogue_url,
            dealer_portal_url=dealer_portal_url,
            dealer_username=dealer_username if dealer_username else None,
            dealer_secret=dealer_secret if dealer_secret else None,
            ai_research_allowed=ai_research_allowed,
            website_match_policy="exact_sku_or_ean",
        )
        st.session_state.pop(f"research_prefilled_{selected_slug}", None)
        st.session_state.pop(f"research_mapping_{selected_slug}", None)
        st.session_state.pop(f"analysis_{selected_slug}", None)
        st.success("Broninstellingen opgeslagen. Geheimen worden versleuteld bewaard.")
        st.rerun()

with source_inventory_subtab:
    st.markdown("#### Voorraad en productstatus")
    st.caption(
        "Kies zelf welke bronwaarden beschikbaar of juist niet beschikbaar "
        "betekenen. Voor beschikbare producten wordt het onderstaande aantal "
        "als Shopify-voorraad gebruikt. "
        "Een ontbrekende of niet-positieve verkoopprijs of een ontbrekende "
        "goedgekeurde productfoto blijft altijd Concept."
    )
    supplier_stock_field = (
        (supplier.get("field_mapping") or {}).get("stock") or ""
    )
    if not supplier_stock_field:
        st.info(
            "Deze leverancier heeft geen voorraadveld. Het onderstaande aantal "
            "wordt daarom als aangenomen voorraad gebruikt voor artikelen met "
            "een geldige prijs. Publicatie vereist daarnaast een goede foto."
        )
    stock_options = supplier.get("request_options") or {}
    stock_mode = (
        stock_options.get("stock_availability_mode")
        or "numeric_positive"
    )
    configured_stock_values = [
        str(value)
        for value in stock_options.get("stock_availability_values") or []
    ]
    configured_stock_actions = {
        str(value): str(action)
        for value, action in (
            stock_options.get("stock_value_actions") or {}
        ).items()
    }
    detected_stock_values = []
    stock_analysis = st.session_state.get(f"analysis_{selected_slug}")
    if supplier_stock_field and stock_analysis:
        detected_stock_values = sorted({
            str(record.get(supplier_stock_field)).strip()
            for record in stock_analysis.records
            if record.get(supplier_stock_field) not in (None, "")
        })
    stock_value_options = list(dict.fromkeys([
        *detected_stock_values, *configured_stock_actions,
        *configured_stock_values,
    ]))
    try:
        inventory_locations = cached_shopify_locations()
        inventory_location_error = ""
    except Exception as exc:
        inventory_locations = []
        inventory_location_error = str(exc)
    inventory_location_by_id = {
        str(location.get("id")): location
        for location in inventory_locations if location.get("id")
    }
    configured_inventory_location_id = str(
        supplier.get("shopify_location_id") or ""
    )
    if (
        configured_inventory_location_id
        and configured_inventory_location_id not in inventory_location_by_id
    ):
        inventory_location_by_id[configured_inventory_location_id] = {
            "id": configured_inventory_location_id,
            "name": "Eerder ingesteld magazijn",
        }
    inventory_location_options = ["", *inventory_location_by_id]
    selected_inventory_location_id = st.selectbox(
        "Shopify-magazijn voor voorraadsynchronisatie *",
        inventory_location_options,
        index=(
            inventory_location_options.index(
                configured_inventory_location_id
            )
            if configured_inventory_location_id
            in inventory_location_options else 0
        ),
        format_func=lambda location_id: (
            "Selecteer een magazijn"
            if not location_id else
            str(
                inventory_location_by_id[location_id].get("name")
                or location_id
            )
        ),
        help=(
            "Alle voorraadwaarden van deze leverancier worden uitsluitend "
            "naar deze actieve Shopify-locatie geschreven."
        ),
        key=f"inventory_location_{selected_slug}",
    )
    if inventory_location_error:
        st.error(
            "Shopify-magazijnen konden niet worden geladen: "
            + inventory_location_error
        )
    with st.container():
        st.markdown("##### Actie per voorraadstatus van de leverancier")
        st.caption(
            "Kies per bronstatus of deze op het geselecteerde Shopify-magazijn "
            "als Actief of Concept moet worden verwerkt."
        )
        action_rows = []
        for value in stock_value_options:
            configured_action = configured_stock_actions.get(value)
            if not configured_action:
                normalized = value.casefold()
                selected_normalized = {
                    item.casefold() for item in configured_stock_values
                }
                if stock_mode == "selected_available":
                    configured_action = (
                        "available"
                        if normalized in selected_normalized
                        else "unavailable"
                    )
                elif stock_mode == "selected_unavailable":
                    configured_action = (
                        "unavailable"
                        if normalized in selected_normalized
                        else "available"
                    )
                else:
                    configured_action = (
                        "available"
                        if float(value) > 0
                        else "unavailable"
                    ) if re.fullmatch(r"-?\d+(?:[.,]\d+)?", value) else (
                        "unavailable"
                    )
            action_rows.append({
                "Bronwaarde": value,
                "Actie": {
                    "available": "Actief · voorraad ingesteld",
                    "active_zero": "Actief · voorraad 0",
                    "unavailable": "Concept · voorraad 0",
                }.get(configured_action, "Concept · voorraad 0"),
            })
        stock_action_editor = st.data_editor(
            pd.DataFrame(
                action_rows, columns=["Bronwaarde", "Actie"]
            ),
            hide_index=True,
            width="stretch",
            num_rows="dynamic",
            disabled=not supplier_stock_field,
            column_config={
                "Bronwaarde": st.column_config.TextColumn(
                    "Waarde uit leveranciersbron",
                    required=True,
                ),
                "Actie": st.column_config.SelectboxColumn(
                    "Wat moet hiermee gebeuren?",
                    options=[
                        "Actief · voorraad ingesteld",
                        "Actief · voorraad 0",
                        "Concept · voorraad 0",
                    ],
                    required=True,
                    default="Concept · voorraad 0",
                    help=(
                        "Kies onafhankelijk de publicatiestatus en voorraad. "
                        "Een ontbrekende prijs of foto kan het product alsnog "
                        "op Concept houden."
                    ),
                ),
            },
            key=f"stock_value_actions_{selected_slug}",
        )
        available_stock_quantity = st.number_input(
            (
                "Shopify-voorraad wanneer het product beschikbaar is"
                if supplier_stock_field
                else "Aangenomen Shopify-voorraad"
            ),
            min_value=1,
            max_value=1_000_000,
            value=int(supplier.get("available_stock_quantity") or 1),
            step=1,
        )
        draft_only_when_no_location_stock = st.checkbox(
            "Alleen op Concept zetten als op geen enkele voorraadlocatie voorraad is",
            value=bool(stock_options.get(
                "draft_only_when_no_location_stock", False
            )),
            help=(
                "Controleert ook andere Shopify-locaties, zoals Weldingshop. "
                "Het product blijft actief zolang ergens voorraad aanwezig is."
            ),
        )
        current_availability_actions = {
            str(row.get("Bronwaarde") or "").strip():
                {
                    "Actief · voorraad ingesteld": "available",
                    "Actief · voorraad 0": "active_zero",
                    "Concept · voorraad 0": "unavailable",
                }.get(row.get("Actie"), "unavailable")
            for row in stock_action_editor.to_dict("records")
            if str(row.get("Bronwaarde") or "").strip()
        }
        stored_availability_actions = {
            str(value).strip(): str(action)
            for value, action in configured_stock_actions.items()
            if str(value).strip()
        }
        inventory_settings_changed = any((
            selected_inventory_location_id
            != configured_inventory_location_id,
            int(available_stock_quantity)
            != int(supplier.get("available_stock_quantity") or 1),
            current_availability_actions != stored_availability_actions,
            bool(draft_only_when_no_location_stock) != bool(
                stock_options.get("draft_only_when_no_location_stock", False)
            ),
        ))
        inventory_button_key = f"save_inventory_{selected_slug}"
        save_inventory = st.button(
            "Voorraadregel opslaan en toepassen",
            disabled=not selected_inventory_location_id,
            key=inventory_button_key,
        )
        inventory_saved_key = f"inventory_saved_{selected_slug}"
        show_save_button_status(
            inventory_button_key,
            (
                "changed"
                if inventory_settings_changed
                else "saved"
                if st.session_state.get(inventory_saved_key)
                else ""
            ),
        )
    if save_inventory:
        result = save_inventory_mapping(
            selected_slug,
            int(available_stock_quantity),
            inventory_location_id=selected_inventory_location_id,
            availability_mode="value_actions",
            availability_actions=current_availability_actions,
            draft_only_when_no_location_stock=draft_only_when_no_location_stock,
            keep_active_when_out_of_stock=False,
        )
        st.success(
            f"Voorraadregel toegepast op {result['updated']} producten. "
            f"Beschikbaar aantal: {result['available_quantity']}."
        )
        st.session_state[inventory_saved_key] = True
        st.rerun()

with source_enrichment_rules_subtab:
    st.markdown("#### Verrijkingsprofiel")
    st.caption(
        "Deze actieve regels worden opnieuw ingelezen bij handmatige en "
        "catalogusverrijking. Prijs en voorraad blijven altijd beschermd."
    )
    profile = get_enrichment_profile(selected_slug)
    content = profile.get("content") or {}
    translation = profile.get("translation") or {}
    overwrite = profile.get("overwrite") or {}
    execution = profile.get("execution") or {}
    quality = profile.get("quality") or {}
    st.info(
        f"Actief profiel · versie {profile.get('version', 1)} · "
        f"laatst gewijzigd {profile.get('updated_at') or 'leveranciersstandaard'}"
    )
    with st.form(f"enrichment_profile_{selected_slug}"):
        st.markdown("##### Bronnen en inhoud")
        # De tweede omschrijving is langer; geef die kolom voldoende ruimte
        # zodat beide bronregels op dezelfde regel en hoogte blijven staan.
        source_columns = st.columns([0.8, 1.65], vertical_alignment="top")
        exact_sku_required = source_columns[0].checkbox(
            "Exact artikelnummer verplicht",
            value=bool(profile.get("exact_sku_required", True)),
        )
        official_sources_only = source_columns[1].checkbox(
            "Uitsluitend officiële leveranciersbronnen",
            value=bool(profile.get("official_sources_only", True)),
        )
        content_columns = st.columns(3)
        feature_icons_enabled = content_columns[1].checkbox(
            "Functie-iconen", value=bool(content.get("feature_icons", False))
        )
        documents_enabled = content_columns[2].checkbox(
            "Documenten en handleidingen", value=bool(content.get("documents", False))
        )
        preserve_document_image_positions = bool(
            content.get(
                "preserve_document_image_positions",
                selected_slug == "tecweld",
            )
        )
        if selected_slug == "tecweld":
            preserve_document_image_positions = content_columns[2].checkbox(
                "Afbeeldingen en schema’s bij de oorspronkelijke bronpagina plaatsen",
                value=True,
                disabled=True,
                help=(
                    "Vaste Tecweld-regel. Uitschakelen kan een slechte galerij-opmaak "
                    "veroorzaken, waarbij afbeeldingen los van de bijbehorende tekst "
                    "achteraan het document terechtkomen."
                ),
            )
        content_values = {
            "full_description": content_columns[0].checkbox(
                "Volledige omschrijving", value=bool(content.get("full_description", True))
            ),
            "feature_bullets": content_columns[0].checkbox(
                "Kenmerken en voordelen", value=bool(content.get("feature_bullets", True))
            ),
            "technical_specifications": content_columns[0].checkbox(
                "Technische specificaties", value=bool(content.get("technical_specifications", True))
            ),
            "feature_icons": feature_icons_enabled,
            "translate_icon_labels": content_columns[1].checkbox(
                "Icoonlabels naar Nederlands vertalen",
                value=bool(content.get("translate_icon_labels", selected_slug == "tecweld")),
                disabled=not feature_icons_enabled,
            ),
            "render_icons_in_product_html": content_columns[1].checkbox(
                "Iconen zichtbaar in productomschrijving",
                value=bool(content.get("render_icons_in_product_html", selected_slug == "tecweld")),
                disabled=not feature_icons_enabled,
                help="Voegt een aparte iconenrij met Nederlandse labels toe aan de Weldingshop-product-HTML.",
            ),
            "include_icons_in_shopify": content_columns[1].checkbox(
                "Iconen meenemen naar Shopify",
                value=bool(content.get("include_icons_in_shopify", selected_slug == "tecweld")),
                disabled=not feature_icons_enabled,
                help="De iconenrij wordt onderdeel van de productomschrijving wanneer het product naar Shopify wordt opgeslagen.",
            ),
            "accessories": content_columns[1].checkbox(
                "Meegeleverde accessoires", value=bool(content.get("accessories", False))
            ),
            "product_images": content_columns[1].checkbox(
                "Productfoto's", value=bool(content.get("product_images", True))
            ),
            "documents": documents_enabled,
            "preserve_document_image_positions": preserve_document_image_positions,
            "include_documents_in_shopify": content_columns[2].checkbox(
                "Vertaalde documenten in Shopify-omschrijving",
                value=bool(content.get("include_documents_in_shopify", selected_slug == "tecweld")),
                disabled=not documents_enabled,
                help="Uploadt uitsluitend goedgekeurde Nederlandse documenten naar Shopify Files en plaatst downloadlinks in de productomschrijving.",
            ),
        }

        st.markdown("##### Vertaling en Weldingshop-huisstijl")
        translation_enabled = st.checkbox(
            "Inhoud vertalen naar Nederlands",
            value=bool(translation.get("enabled", False)),
        )
        translate_columns = st.columns(3)
        allow_summary = translate_columns[0].checkbox(
            "Samenvatten toestaan", value=bool(translation.get("allow_summary", False)),
            disabled=not translation_enabled,
        )
        preserve_paragraphs = translate_columns[0].checkbox(
            "Alle alinea's behouden", value=bool(translation.get("preserve_paragraphs", True)),
            disabled=not translation_enabled,
        )
        preserve_bullets = translate_columns[1].checkbox(
            "Alle opsommingen behouden", value=bool(translation.get("preserve_bullets", True)),
            disabled=not translation_enabled,
        )
        preserve_values = translate_columns[1].checkbox(
            "Technische waarden behouden", value=bool(translation.get("preserve_technical_values", True)),
            disabled=not translation_enabled,
        )
        minimum_length = translate_columns[2].number_input(
            "Minimale vertalingslengte (%)", min_value=25, max_value=100,
            value=int(translation.get("minimum_length_percent") or 55),
            disabled=not translation_enabled,
        )
        primary_translation_model = str(translation.get("primary_model") or "")
        fallback_translation_model = str(
            translation.get("quality_fallback_model") or ""
        )
        fallback_after_quality_failure = bool(
            translation.get("fallback_only_after_quality_failure", False)
        )
        if selected_slug == "tecweld":
            model_columns = st.columns(2)
            primary_translation_model = "gpt-5.6-terra"
            fallback_translation_model = "gpt-5.6-sol"
            fallback_after_quality_failure = True
            model_columns[0].text_input(
                "Primair vertaalmodel",
                value="GPT-5.6 Terra",
                disabled=True,
                help="Vaste Tecweld-regel: alle eerste vertalingen gebruiken GPT-5.6 Terra.",
            )
            model_columns[1].text_input(
                "Herstelmodel na kwaliteitsfout",
                value="GPT-5.6 Sol",
                disabled=True,
                help=(
                    "GPT-5.6 Sol wordt uitsluitend gebruikt wanneer de Terra-uitvoer "
                    "de Nederlandse taal- of volledigheidscontrole niet haalt."
                ),
            )
            st.caption(
                "Kostenpad: Terra voor alle vertalingen; Sol alleen na een afgekeurde "
                "taal- of volledigheidscontrole."
            )

        st.markdown("##### Overschrijven en uitvoering")
        overwrite_columns = st.columns(3)
        text_overwrite = overwrite_columns[0].selectbox(
            "Tekst bijwerken",
            ["if_empty", "if_more_complete", "always"],
            index=["if_empty", "if_more_complete", "always"].index(
                overwrite.get("text", "if_more_complete")
            ),
            format_func=lambda value: {
                "if_empty": "Alleen wanneer leeg", "if_more_complete": "Alleen wanneer completer",
                "always": "Altijd vervangen",
            }[value],
        )
        image_overwrite = overwrite_columns[1].selectbox(
            "Afbeeldingen bijwerken", ["if_empty", "merge_verified", "replace_verified"],
            index=["if_empty", "merge_verified", "replace_verified"].index(
                overwrite.get("images", "merge_verified")
            ),
            format_func=lambda value: {
                "if_empty": "Alleen wanneer leeg", "merge_verified": "Goedgekeurde foto's samenvoegen",
                "replace_verified": "Vervangen na validatie",
            }[value],
        )
        manual_content = overwrite_columns[2].selectbox(
            "Handmatige inhoud overschrijven", ["never", "with_confirmation"],
            index=["never", "with_confirmation"].index(
                overwrite.get("manual_content", "never")
            ),
            format_func=lambda value: {
                "never": "Nooit", "with_confirmation": "Alleen na bevestiging",
            }[value],
        )
        execution_columns = st.columns(4)
        execution_values = {
            "selected_product": execution_columns[0].checkbox(
                "Geselecteerd product", value=bool(execution.get("selected_product", True))
            ),
            "bulk_enrichment": execution_columns[1].checkbox(
                "Catalogusverrijking", value=bool(execution.get("bulk_enrichment", True))
            ),
            "source_import": execution_columns[2].checkbox(
                "Bronimport", value=bool(execution.get("source_import", False))
            ),
            "scheduled_sync": execution_columns[3].checkbox(
                "Directe/geplande synchronisatie", value=bool(execution.get("scheduled_sync", False))
            ),
        }
        st.warning(
            "Prijs, inkoopprijs en voorraad kunnen via verrijking nooit worden overschreven."
        )
        save_profile = st.form_submit_button(
            "Verrijkingsregels opslaan en activeren", type="primary"
        )
    if save_profile:
        saved = save_enrichment_profile(selected_slug, {
            **profile,
            "exact_sku_required": exact_sku_required,
            "official_sources_only": official_sources_only,
            "content": content_values,
            "translation": {
                "enabled": translation_enabled, "target_language": "nl-NL",
                "allow_summary": allow_summary, "preserve_paragraphs": preserve_paragraphs,
                "preserve_bullets": preserve_bullets,
                "preserve_technical_values": preserve_values,
                "minimum_length_percent": int(minimum_length),
                "primary_model": primary_translation_model,
                "quality_fallback_model": fallback_translation_model,
                "fallback_only_after_quality_failure": fallback_after_quality_failure,
            },
            "overwrite": {
                "text": text_overwrite, "images": image_overwrite,
                "manual_content": manual_content, "prices": "never", "stock": "never",
            },
            "execution": execution_values,
            "quality": quality,
        })
        st.success(f"Verrijkingsprofiel versie {saved['version']} is actief.")
        st.rerun()
    if st.button(
        "Leveranciersstandaard herstellen",
        key=f"reset_enrichment_profile_{selected_slug}",
    ):
        reset_enrichment_profile(selected_slug)
        st.success("Leveranciersstandaard hersteld en geactiveerd.")
        st.rerun()
    with st.expander("Actieve regels als JSON bekijken"):
        st.json(get_enrichment_profile(selected_slug))


with source_sync_subtab:
    (
        source_missing_products_tab,
        source_continue_selling_tab,
        source_sync_planning_tab,
    ) = st.tabs([
        "7.1 Producten uit de bron verwijderen",
        "7.2 Doorgaan met verkopen aan/uit",
        "7.3 Synchronisatie planning",
    ])


with source_missing_products_tab:
    st.markdown("#### Producten die uit de bron verdwijnen")
    st.caption(
        "Deze regel geldt uitsluitend voor producten die eerder met een exacte SKU "
        "voor deze leverancier zijn geïmporteerd. Als een SKU terugkomt, vervalt de "
        "lopende verwijdertermijn automatisch."
    )
    with st.form(f"missing_product_policy_{selected_slug}"):
        draft_missing = st.checkbox(
            "Producten in Shopify die niet in de leveranciersbron staan en "
            "geen voorraad hebben op locatie Weldingshop op Concept zetten",
            value=bool(supplier.get("missing_products_to_draft", 1)),
        )
        delete_missing = st.checkbox(
            "Product na de bewaartermijn definitief uit Shopify verwijderen",
            value=bool(supplier.get("delete_missing_products", 0)),
            disabled=not draft_missing,
        )
        delete_after_months = st.number_input(
            "Verwijderen nadat het product zoveel maanden ontbreekt",
            min_value=1,
            max_value=120,
            value=int(supplier.get("delete_missing_after_months") or 2),
            step=1,
            disabled=not delete_missing,
        )
        save_missing_policy = st.form_submit_button("Regel voor ontbrekende producten opslaan")
    if save_missing_policy:
        save_missing_product_policy(
            selected_slug,
            draft_missing=draft_missing,
            delete_missing=delete_missing,
            delete_after_months=int(delete_after_months),
        )
        st.success("Regel voor ontbrekende producten opgeslagen.")
        st.rerun()


with source_continue_selling_tab:
    st.markdown("#### Doorgaan met verkopen wanneer niet op voorraad")
    st.caption(
        "Deze instelling wijzigt uitsluitend het Shopify-voorraadbeleid van "
        "deze leverancier. De bronverwijdering en synchronisatieplanning "
        "blijven ongewijzigd."
    )
    current_continue_selling = bool(
        stock_options.get("continue_selling_when_out_of_stock", False)
    )
    default_delivery_notice = (
        "Let op: door het ontbreken van live synchronisatie met de "
        "leverancier kan de levertijd afwijken."
    )
    stored_collection_rules = {
        str(rule.get("collection_id") or ""): rule
        for rule in stock_options.get("continue_selling_collection_rules") or []
        if str(rule.get("collection_id") or "")
    }
    try:
        shopify_collections = cached_shopify_collections()
        collection_load_error = ""
    except Exception as exc:
        shopify_collections = []
        collection_load_error = str(exc)
    collection_by_id = {
        str(collection.get("id")): collection
        for collection in shopify_collections
        if collection.get("id")
    }
    for collection_id, rule in stored_collection_rules.items():
        collection_by_id.setdefault(collection_id, {
            "id": collection_id,
            "title": rule.get("collection_title") or "Onbekende collectie",
            "productsCount": {},
        })
    if collection_load_error:
        st.error(
            "Shopify-collecties konden niet worden geladen: "
            + collection_load_error
        )
    if st.button(
        "Collectielijst vernieuwen",
        key=f"refresh_continue_collections_{selected_slug}",
    ):
        cached_shopify_collections.clear()
        st.rerun()
    with st.form(f"continue_selling_policy_{selected_slug}"):
        continue_selling_when_out_of_stock = st.checkbox(
            "Standaard doorgaan met verkopen voor deze leverancier",
            value=current_continue_selling,
            help=(
                "Geldt voor alle producten die niet door een collectieregel "
                "worden overschreven."
            ),
        )
        selected_collection_ids = st.multiselect(
            "Collecties met een afwijkende instelling",
            options=list(collection_by_id),
            default=list(stored_collection_rules),
            format_func=lambda collection_id: (
                str(collection_by_id[collection_id].get("title") or collection_id)
                + (
                    " · "
                    + str(
                        (collection_by_id[collection_id].get("productsCount") or {})
                        .get("count")
                    )
                    + " producten"
                    if (collection_by_id[collection_id].get("productsCount") or {})
                    .get("count") is not None else ""
                )
            ),
            help=(
                "Zoek op collectienaam en selecteer alleen collecties die van "
                "de leveranciersstandaard moeten afwijken."
            ),
        )
        collection_rule_rows = []
        for collection_id in selected_collection_ids:
            stored_rule = stored_collection_rules.get(collection_id) or {}
            collection_rule_rows.append({
                "Collectie": str(
                    collection_by_id[collection_id].get("title")
                    or collection_id
                ),
                "Instelling": (
                    "Aan · doorgaan met verkopen"
                    if stored_rule.get("continue_selling", True)
                    else "Uit · stoppen bij voorraad 0"
                ),
                "collection_id": collection_id,
            })
        collection_rule_editor = st.data_editor(
            pd.DataFrame(
                collection_rule_rows,
                columns=["Collectie", "Instelling", "collection_id"],
            ),
            hide_index=True,
            width="stretch",
            disabled=["Collectie", "collection_id"],
            column_config={
                "Collectie": st.column_config.TextColumn("Collectie"),
                "Instelling": st.column_config.SelectboxColumn(
                    "Doorgaan met verkopen",
                    options=[
                        "Aan · doorgaan met verkopen",
                        "Uit · stoppen bij voorraad 0",
                    ],
                    required=True,
                ),
                "collection_id": None,
            },
            key=f"continue_collection_rules_{selected_slug}",
        )
        st.caption(
            "Veilige conflictregel: staat een product in meerdere gekozen "
            "collecties, dan heeft ‘Uit’ voorrang op ‘Aan’."
        )
        delivery_time_notice_text = st.text_input(
            "Tekst in custom veld Verwachte product levertijd",
            value=str(
                stock_options.get("delivery_time_notice_text")
                or default_delivery_notice
            ),
            max_chars=255,
            help=(
                "Wordt gebruikt voor ieder product waarvoor doorgaan met "
                "verkopen effectief Aan staat."
            ),
        )
        save_continue_selling = st.form_submit_button(
            "Instelling voor doorgaan met verkopen opslaan"
        )
    if save_continue_selling:
        collection_rules = [
            {
                "collection_id": str(row.get("collection_id") or ""),
                "collection_title": str(row.get("Collectie") or ""),
                "continue_selling": (
                    row.get("Instelling") == "Aan · doorgaan met verkopen"
                ),
            }
            for row in collection_rule_editor.to_dict("records")
        ]
        save_inventory_mapping(
            selected_slug,
            int(supplier.get("available_stock_quantity") or 1),
            continue_selling_when_out_of_stock=(
                continue_selling_when_out_of_stock
            ),
            continue_selling_collection_rules=collection_rules,
            delivery_time_notice_text=delivery_time_notice_text,
            apply_existing=False,
        )
        policy_label = (
            "aan" if continue_selling_when_out_of_stock else "uit"
        )
        st.success(
            f"Leveranciersstandaard staat {policy_label} en "
            f"{len(collection_rules)} collectieregels zijn opgeslagen. "
            "Ze worden bij de eerstvolgende synchronisatie toegepast."
        )
        st.rerun()


with source_sync_planning_tab:
    st.markdown("#### Synchronisatieplanning")
    with st.form(f"sync_schedule_{selected_slug}"):
        schedule_columns = st.columns([1, 1, 1])
        recurring_frequency = (
            supplier.get("sync_frequency")
            if supplier.get("sync_frequency") in FREQUENCIES
            else "daily"
        )
        sync_schedule_mode = schedule_columns[0].selectbox(
            "Frequentie",
            ["once", *FREQUENCIES],
            index=(
                ["once", *FREQUENCIES].index(recurring_frequency)
            ),
            format_func=lambda value: (
                "Nu eenmalig" if value == "once" else FREQUENCIES[value]
            ),
        )
        sync_enabled = st.checkbox(
            "Automatisch synchroniseren",
            value=(
                False if sync_schedule_mode == "once"
                else bool(supplier.get("sync_enabled"))
            ),
            disabled=sync_schedule_mode == "once",
            help=(
                "Bij 'Nu eenmalig' start de synchronisatie direct na het "
                "opslaan en wordt geen terugkerende planning bewaard."
            ),
        )
        behavior_columns = st.columns(2)
        sync_product_status = behavior_columns[0].radio(
            "Shopify-productstatus",
            ["draft", "active"],
            index=(
                0 if supplier.get("sync_product_status") == "draft" else 1
            ),
            format_func=lambda value: (
                "Concept" if value == "draft" else "Actief"
            ),
            horizontal=True,
            help=(
                "Actief geldt alleen voor producten met een geldige prijs en "
                "beschikbare voorraad én een goedgekeurde productfoto."
            ),
        )
        sync_channel_mode = behavior_columns[1].radio(
            "Verkoopkanalen",
            ["off", "all"],
            index=0 if not supplier.get("sync_publish_all", 1) else 1,
            format_func=lambda value: (
                "Alle kanalen uit" if value == "off"
                else "Publiceer naar alle kanalen"
            ),
            horizontal=True,
        )
        sync_new_product_policy = st.radio(
            "Producten die nog niet in Shopify staan",
            ["existing_only", "add_complete"],
            index=(
                1
                if supplier.get("sync_new_product_policy")
                == "add_complete"
                else 0
            ),
            format_func=lambda value: (
                "Alleen in Shopify aanwezige producten bijwerken"
                if value == "existing_only"
                else (
                    "Niet gevonden producten toevoegen als alle "
                    "verplichte velden compleet zijn"
                )
            ),
            help=(
                "Voor toevoegen zijn minimaal vereist: SKU, foto, titel, "
                "omschrijving, verkoopprijs en inkoopprijs. Onvolledige "
                "nieuwe producten worden overgeslagen."
            ),
        )
        if sync_new_product_policy == "add_complete":
            st.caption(
                "Verplichte controle voor nieuwe producten: SKU ✓ · foto ✓ · "
                "titel ✓ · omschrijving ✓ · verkoopprijs ✓ · inkoopprijs ✓"
            )
        sync_changed_only = st.checkbox(
            "Alleen nieuwe en gewijzigde bronrecords synchroniseren",
            value=bool(supplier.get("sync_changed_only")),
            help=(
                "Vergelijkt ieder genormaliseerd product met de vorige "
                "opgeslagen bronmeting. Nieuwe en gewijzigde SKU’s gaan naar "
                "Shopify; ongewijzigde SKU’s worden overgeslagen. Verdwenen "
                "producten blijven altijd gecontroleerd worden."
            ),
        )
        if sync_changed_only:
            st.caption(
                "Bij de eerste keer inschakelen wordt de huidige PIM-inhoud "
                "als nulmeting opgeslagen. De volgende broninlezing bepaalt "
                "daarna welke SKU’s werkelijk gewijzigd zijn."
            )
        sync_family_on_change = st.checkbox(
            "Bij een gewijzigde SKU de complete productfamilie bijwerken",
            value=bool(
                supplier.get("request_options", {}).get(
                    "sync_family_on_change"
                )
            ),
            disabled=not sync_changed_only,
            help=(
                "Neemt alle varianten uit de vastgelegde PIM-familie mee. "
                "Bij 'alleen aanwezige producten' worden uitsluitend de "
                "familievarianten bijgewerkt die al in Shopify staan."
            ),
        )
        dealer_pricelist_enabled = False
        if selected_route.uses_certilas_dealer_pricelist:
            dealer_pricelist_enabled = st.checkbox(
                "Bij iedere synchronisatie de actuele dealerprijslijst downloaden",
                value=bool(
                    supplier.get("request_options", {}).get(
                        "dealer_pricelist_enabled"
                    )
                ),
                disabled=not (
                    supplier.get("has_dealer_username")
                    and supplier.get("has_dealer_secret")
                ),
                help=(
                    "Importeert alleen wanneer de SHA-256 van het Excelbestand "
                    "afwijkt van de laatst verwerkte prijslijst."
                ),
            )
        default_start = (
            date.fromisoformat(supplier["sync_start_date"])
            if supplier.get("sync_start_date")
            else date.today()
        )
        sync_start = schedule_columns[1].date_input(
            "Startdatum",
            value=default_start,
        )
        stored_time = supplier.get("sync_time") or "02:00"
        sync_at = schedule_columns[2].time_input(
            "Tijd",
            value=datetime.strptime(stored_time, "%H:%M").time(),
            step=300,
        )
        st.caption(
            "Tijdzone: Europe/Amsterdam. Bij wekelijks bepaalt de startdatum de "
            "weekdag; bij maandelijks bepaalt deze de dag van de maand."
        )
        save_schedule = st.form_submit_button("Planning opslaan")
    if save_schedule:
        next_sync = save_sync_schedule(
            selected_slug,
            enabled=(sync_enabled and sync_schedule_mode != "once"),
            frequency=(
                recurring_frequency
                if sync_schedule_mode == "once"
                else sync_schedule_mode
            ),
            start_date=sync_start.isoformat(),
            sync_time=sync_at.strftime("%H:%M"),
            timezone_name=DEFAULT_TIMEZONE,
            product_status=sync_product_status,
            publish_all=sync_channel_mode == "all",
            new_product_policy=sync_new_product_policy,
            changed_only=sync_changed_only,
            family_on_change=sync_family_on_change,
            dealer_pricelist_enabled=dealer_pricelist_enabled,
        )
        if sync_schedule_mode == "once":
            job = start_background_sync(selected_slug)
            if job.get("status") in ACTIVE_JOB_STATUSES:
                st.success(
                    "Eenmalige synchronisatie is direct gestart; er is geen "
                    "terugkerende planning opgeslagen."
                )
            else:
                st.info("De meest recente synchronisatietaak is geladen.")
        elif next_sync:
            st.success(f"Planning opgeslagen. Volgende uitvoering (UTC): {next_sync}")
        else:
            st.success("Planning opgeslagen; automatische synchronisatie staat uit.")
        st.rerun()

    if supplier.get("next_sync_at") and supplier.get("sync_enabled"):
        next_local = datetime.fromisoformat(supplier["next_sync_at"]).astimezone(
            ZoneInfo(DEFAULT_TIMEZONE)
        )
        st.info(
            "Volgende synchronisatie: "
            f"{next_local:%d-%m-%Y om %H:%M} (Europe/Amsterdam)"
        )

    source_analysis_ready = bool(
        st.session_state.get(f"analysis_{selected_slug}")
    )
    upload_source = "upload" in supplier.get("source_type", "")
    remote_source_configured = bool(
        str(supplier.get("source_location") or "").strip()
    )
    existing_product_count = supplier_stats(selected_slug).get("total", 0)
    sync_source_ready = (
        existing_product_count > 0
        if upload_source
        else remote_source_configured
    )
    if not sync_source_ready:
        st.info(
            (
                "Lees en importeer eerst het Excelbestand. Daarna wordt "
                "Synchroniseer direct beschikbaar."
                if upload_source else
                "Lees eerst de bron in via **Bron lezen en analyseren**. "
                "Daarna wordt Synchroniseer direct beschikbaar."
            )
        )
    latest_sync = get_background_sync(selected_slug) or {}
    sync_is_active = latest_sync.get("status") in (
        "queued", "running", "cancel_requested"
    )
    retry_failed_sync = latest_sync.get("status") == "failed"
    if st.button(
        (
            "Synchronisatie opnieuw proberen"
            if retry_failed_sync else "Synchroniseer direct"
        ),
        type="primary",
        key=f"sync_now_{selected_slug}",
        disabled=(
            not sync_source_ready
            or (
                sync_is_active
            )
        ),
        help=(
            "Importeer eerst brongegevens in PIM."
            if not sync_source_ready
            else "Start de synchronisatie als veilige achtergrondtaak."
        ),
    ):
        try:
            start_background_sync(selected_slug)
            reset_product_table_selection(selected_slug)
            st.success(
                "De synchronisatie is op de server gestart. "
                "Je kunt deze pagina veilig verlaten."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Achtergrondtaak kon niet worden gestart: {exc}")
    if sync_is_active and st.button(
        "Synchronisatie stoppen",
        type="secondary",
        key=f"stop_sync_{selected_slug}",
        disabled=latest_sync.get("status") == "cancel_requested",
    ):
        try:
            stop_background_sync(selected_slug)
            st.warning("Stopverzoek verzonden; de lopende Shopify-stap wordt afgebroken.")
            st.rerun()
        except Exception as exc:
            st.error(f"Synchronisatie kon niet worden gestopt: {exc}")
    show_sync_job(selected_slug)

with source_analysis_subtab:
    analysis = st.session_state.get(f"analysis_{selected_slug}")
    persisted_mapping = supplier.get("field_mapping") or {}
    if not analysis and persisted_mapping:
        st.success(
            "De opgeslagen leveranciersconfiguratie is geladen. Een nieuw "
            "bronbestand is alleen nodig om kolommen opnieuw te analyseren."
        )
        st.markdown("#### Opgeslagen veldkoppelingen")
        st.dataframe(
            pd.DataFrame([
                {"PIM-veld": target, "Bronveld": source}
                for target, source in persisted_mapping.items() if source
            ]),
            hide_index=True,
            width="stretch",
        )
    if analysis:
        st.success(f"{analysis.format}: {analysis.row_count} regels en {len(analysis.fields)} velden gevonden.")
        st.write("Gevonden velden:", ", ".join(analysis.fields))
        st.dataframe(pd.DataFrame(analysis.sample), width="stretch")
        rule_mapping = suggest_source_field_mapping(analysis.fields)
        learned_mapping = learned_source_field_mapping(
            analysis.fields,
            claimed_sources=set(rule_mapping.values()),
            claimed_targets=set(rule_mapping),
        )
        automatic_mapping = {**rule_mapping, **learned_mapping}
        current_supplier_mapping = get_supplier(selected_slug) or {}
        saved_mapping = (
            current_supplier_mapping.get("field_mapping") or {}
        )
        request_options = (
            current_supplier_mapping.get("request_options") or {}
        )
        locks_initialized = "locked_field_mapping" in request_options
        explicit_locked_mapping = (
            request_options.get("locked_field_mapping") or {}
        )
        fixed_mapping = (
            {
                target: source
                for target, source in explicit_locked_mapping.items()
                if target in DEFAULT_MAPPING
            }
            if locks_initialized
            else {
                target: source
                for target, source in saved_mapping.items()
                if (
                    target in DEFAULT_MAPPING
                    and source != automatic_mapping.get(target)
                )
            }
        )
        proposal_mapping = {
            **automatic_mapping, **fixed_mapping,
        }
        mapping_labels = {
            target: target.replace("_", " ").capitalize()
            for target in DEFAULT_MAPPING
        }
        if proposal_mapping:
            mapping_labels = {
                "sku": "SKU", "ean": "EAN", "title": "Productnaam",
                "description": "Omschrijving", "price": "Van-voor prijs",
                "sale_price": "Verkoopprijs", "weight": "Gewicht",
                "cost_price": "Inkoopprijs",
                "weight_kg": "Verzendgewicht (kg)",
                "primary_image": "Hoofdafbeelding",
                "stock": "Voorraad", "product_type": "Producttype",
                "category": "Categorie",
                "category_full": "Volledige categorie",
                "updated_at": "Wijzigingsdatum",
                "purchase_unit": "Inkoopeenheid",
                "sales_unit": "Verkoopeenheid",
                "purchase_units_per_sales_unit": "Inkoop per verkoop",
                "unit_calculation_mode": "Rekenwijze",
                "gross_purchase_price_per_kg": "Bruto inkoopprijs per kg",
                "purchase_discount_percent": "Inkoopkorting (%)",
                "net_purchase_price_per_kg": "Netto inkoopprijs per kg",
                "kg_per_purchase_unit": "Kg per Ceweld Unit",
                "kg_per_sales_unit": "Kg per Ceweld Bundle",
                "product_group_name": "Productgroep",
                "execution": "Uitvoering", "filter": "Filter",
            }
            st.markdown("#### Automatisch koppelingsvoorstel")
            st.caption(
                "PIM vergelijkt kolomnamen zonder hoofdletters, punten, "
                "spaties en streepjes. Pas de gevonden bronkolom zo nodig aan "
                "en controleer het voorstel vóór opslaan. Groen is automatisch; "
                "blauw is handmatig gewijzigd en vastgezet."
            )
            edited_automatic_mapping: dict[str, str] = {}
            locked_mapping_to_save: dict[str, str] = {}
            automatic_transformations = dict(
                current_supplier_mapping.get("source_transformations") or {}
            )
            automatic_example_index = st.session_state.get(
                f"central_example_product_{selected_slug}", -1
            )
            if not isinstance(automatic_example_index, int) or not (
                -1 <= automatic_example_index < len(analysis.records)
            ):
                automatic_example_index = -1
            automatic_example_record = (
                analysis.records[
                    automatic_example_index
                    if automatic_example_index >= 0 else 0
                ]
                if analysis.records else {}
            )
            composition_updates: dict[str, dict] = {}
            proposal_header = st.columns([2.1, 3.0, 3.3, 1.8])
            proposal_header[0].markdown("**PIM-veld**")
            proposal_header[1].markdown("**Gevonden Excel-kolom**")
            proposal_header[2].markdown(
                "**Actuele waarde geselecteerd product**"
            )
            proposal_header[3].markdown("**Status**")
            source_options = list(dict.fromkeys([
                "", *analysis.fields,
                *[
                    source for source in fixed_mapping.values()
                    if source
                ],
            ]))
            for proposal_index, (target, source_field) in enumerate(
                proposal_mapping.items()
            ):
                is_fixed = target in fixed_mapping
                proposal_columns = st.columns([2.1, 3.0, 3.3, 1.8])
                lock_mapping = proposal_columns[3].checkbox(
                    "🔒 Vastzetten",
                    value=is_fixed,
                    key=f"lock_auto_mapping_{selected_slug}_{target}",
                    help=(
                        "Een vastgezette koppeling wordt bij een volgende "
                        "bronanalyse niet door een automatisch voorstel gewijzigd."
                    ),
                )
                proposal_columns[0].markdown(
                    (
                        f":blue[**{mapping_labels.get(target, target)}**]"
                        if lock_mapping
                        else f":green[**{mapping_labels.get(target, target)}**]"
                    )
                )
                proposal_widget_key = (
                    f"editable_auto_mapping_{selected_slug}_"
                    f"{proposal_index}_{target}"
                )
                composition_field = f"__samengesteld_{target}"
                saved_composition = automatic_transformations.get(
                    composition_field, {}
                )
                compose_value = source_field == composition_field
                compose_mapping = proposal_columns[3].checkbox(
                    "Samenstellen",
                    value=compose_value,
                    key=f"compose_auto_mapping_{selected_slug}_{target}",
                    help=(
                        "Combineer meerdere bronkolommen en vrije tekst tot "
                        "één waarde voor dit PIM-veld. Bronkolommen mogen ook "
                        "voor andere doelvelden worden gebruikt."
                    ),
                )
                selected_source = proposal_columns[1].selectbox(
                    f"Bronkolom voor {mapping_labels.get(target, target)}",
                    source_options,
                    index=(
                        source_options.index(source_field)
                        if source_field in source_options else 0
                    ),
                    format_func=lambda value: value or "Niet koppelen",
                    label_visibility="collapsed",
                    key=proposal_widget_key,
                    disabled=compose_mapping,
                )
                if compose_mapping:
                    collection = saved_composition.get(
                        "field_collection", {}
                    )
                    saved_items = collection.get("items") or []
                    selected_composition_fields = st.multiselect(
                        f"Bronvelden voor {mapping_labels.get(target, target)}",
                        analysis.fields,
                        default=[
                            item.get("field") for item in saved_items
                            if item.get("field") in analysis.fields
                        ],
                        key=(
                            f"compose_fields_{selected_slug}_{target}"
                        ),
                    )
                    default_template = " ".join(
                        f"{{{{ {field} }}}}"
                        for field in selected_composition_fields
                    )
                    composition_template = st.text_area(
                        f"Samenstelling voor {mapping_labels.get(target, target)}",
                        value=str(
                            collection.get("template") or default_template
                        ),
                        placeholder=(
                            "{{ Product description }} · EAN {{ EAN }} · "
                            "Leverancier {{ Supplier name }}"
                        ),
                        help=(
                            "Gebruik {{ Bronkolom }} voor een veldwaarde. "
                            "Alle overige tekst is vrije invoer."
                        ),
                        key=(
                            f"compose_template_{selected_slug}_{target}"
                        ),
                    )
                    referenced_fields = {
                        match.strip()
                        for match in re.findall(
                            r"{{\s*([^{}]+?)\s*}}", composition_template
                        )
                    }
                    unknown_fields = referenced_fields.difference(
                        analysis.fields
                    )
                    if unknown_fields:
                        st.warning(
                            "Onbekende veldplaats(en): "
                            + ", ".join(sorted(unknown_fields))
                            + ". Kies de exacte bronkolomnaam."
                        )
                    composition_updates[composition_field] = {
                        "custom_field": True,
                        "processing_type": "collection",
                        "field_collection": {
                            "items": [
                                {
                                    "field": field,
                                    "label": field,
                                    "unit": "",
                                }
                                for field in selected_composition_fields
                            ],
                            "format": "template",
                            "template": composition_template,
                        },
                    }
                    selected_source = (
                        composition_field
                        if composition_template.strip() and not unknown_fields
                        else ""
                    )
                show_fixed_field_status(
                    proposal_widget_key, lock_mapping
                )
                transformed_example = apply_source_transformations(
                    automatic_example_record,
                    {
                        **automatic_transformations,
                        **composition_updates,
                    },
                )
                current_example_value = (
                    transformed_example.get(selected_source, "")
                    if selected_source else ""
                )
                if isinstance(current_example_value, (dict, list)):
                    current_example_text = json.dumps(
                        current_example_value, ensure_ascii=False
                    )
                else:
                    current_example_text = str(
                        current_example_value or ""
                    )
                proposal_columns[2].markdown(
                    html.escape(current_example_text[:240]) or "—"
                )
                if selected_source:
                    edited_automatic_mapping[target] = selected_source
                    if not lock_mapping:
                        proposal_columns[3].caption(
                            "Automatisch gekoppeld"
                        )
                else:
                    proposal_columns[3].caption("Niet koppelen")
                if lock_mapping:
                    locked_mapping_to_save[target] = selected_source
            if st.button(
                "Automatisch koppelingsvoorstel opslaan",
                type="primary",
                key=f"save_auto_mapping_{selected_slug}",
            ):
                updated_transformations = dict(automatic_transformations)
                for target in proposal_mapping:
                    field = f"__samengesteld_{target}"
                    if field in composition_updates:
                        updated_transformations[field] = composition_updates[field]
                    else:
                        updated_transformations.pop(field, None)
                save_source_transformations(
                    selected_slug, updated_transformations
                )
                # Ook bewust op 'Niet koppelen' gezette voorstellen doorgeven.
                # save_source_field_mapping verwijdert een koppeling alleen
                # wanneer voor dat doel expliciet een lege bron wordt bewaard.
                mapping_with_removals = {
                    target: edited_automatic_mapping.get(target, "")
                    for target in proposal_mapping
                }
                save_source_field_mapping(selected_slug, mapping_with_removals)
                save_source_field_mapping_locks(
                    selected_slug, locked_mapping_to_save
                )
                confirmed_proposals = [
                    {
                        "source_field": source,
                        "target_field": target,
                        "confidence": 1,
                        "reason": "Bevestigde automatische koppeling",
                    }
                    for target, source in automatic_mapping.items()
                ]
                record_field_mapping_decisions(
                    selected_slug,
                    confirmed_proposals,
                    {
                        (target, source)
                        for target, source in edited_automatic_mapping.items()
                    },
                )
                st.success(
                    f"{len(edited_automatic_mapping)} veldkoppelingen opgeslagen."
                )
                st.rerun()
        else:
            st.info(
                "Geen eenduidige automatische veldkoppelingen gevonden. "
                "Gebruik de handmatige veldkoppelingen hieronder."
            )
        ai_mapping_error = st.session_state.get(
            f"ai_mapping_error_{selected_slug}"
        )
        if ai_mapping_error:
            st.warning(
                "De gewone veldherkenning is uitgevoerd, maar de aanvullende "
                f"AI-analyse lukte niet: {ai_mapping_error}"
            )
        ai_proposals = st.session_state.get(
            f"ai_mapping_proposals_{selected_slug}", []
        )
        if ai_proposals:
            st.markdown("#### AI-voorstellen voor onbekende velden")
            st.caption(
                "AI gebruikt kolomnaam, voorbeeldwaarden en datatype. "
                "Bevestig uitsluitend juiste voorstellen. Niet-bevestigde "
                "voorstellen worden na opslaan als afgewezen onthouden."
            )
            ai_editor = st.data_editor(
                pd.DataFrame([
                    {
                        "Bevestigen": False,
                        "PIM-veld": mapping_labels.get(
                            proposal["target_field"],
                            proposal["target_field"],
                        ),
                        "Bronveld": proposal["source_field"],
                        "Zekerheid": f"{proposal['confidence']:.0%}",
                        "Reden": proposal["reason"],
                        "_target": proposal["target_field"],
                    }
                    for proposal in ai_proposals
                ]),
                hide_index=True,
                width="stretch",
                disabled=[
                    "PIM-veld", "Bronveld", "Zekerheid", "Reden", "_target"
                ],
                column_config={
                    "Bevestigen": st.column_config.CheckboxColumn(
                        "Bevestigen"
                    ),
                    "_target": None,
                },
                key=f"ai_mapping_editor_{selected_slug}",
            )
            if st.button(
                "AI-koppelingen verwerken",
                type="primary",
                key=f"save_ai_mapping_{selected_slug}",
            ):
                accepted_mapping = {
                    row["_target"]: row["Bronveld"]
                    for row in ai_editor.to_dict("records")
                    if row["Bevestigen"]
                }
                accepted_pairs = {
                    (target, source)
                    for target, source in accepted_mapping.items()
                }
                record_field_mapping_decisions(
                    selected_slug, ai_proposals, accepted_pairs
                )
                if accepted_mapping:
                    save_source_field_mapping(
                        selected_slug, accepted_mapping
                    )
                st.session_state.pop(
                    f"ai_mapping_proposals_{selected_slug}", None
                )
                st.success(
                    f"{len(accepted_mapping)} AI-koppeling(en) bevestigd; "
                    f"{len(ai_proposals) - len(accepted_mapping)} afgewezen."
                )
                st.rerun()
        if upload_source:
            source_already_imported = (
                existing_product_count > 0
                and supplier.get("last_run_status") == "success"
            )
            if source_already_imported:
                st.success(
                    f"Brongegevens staan in PIM: {existing_product_count} "
                    "producten. Je kunt nu naar Shopify synchroniseren of "
                    "de gewijzigde veldkoppelingen opnieuw toepassen."
                )
            else:
                st.warning(
                    "Controleer eerst de veldkoppelingen. Sla het Excelbestand "
                    "daarna op in PIM voordat je naar Shopify synchroniseert."
                )
            if st.button(
                (
                    "Brongegevens opnieuw in PIM opslaan"
                    if source_already_imported else
                    "Brongegevens in PIM opslaan"
                ),
                type="primary",
                key=f"import_upload_{selected_slug}",
            ):
                try:
                    imported = import_records(
                        selected_slug,
                        analysis,
                        supplier.get("field_mapping") or None,
                    )
                    if list_discount_rules(
                        selected_slug, include_disabled=False
                    ):
                        apply_purchase_costs(selected_slug)
                    refreshed_supplier = get_supplier(selected_slug) or {}
                    if (
                        list_sales_price_rules(selected_slug, include_disabled=False)
                        or (refreshed_supplier.get("request_options") or {}).get(
                            "sales_price_rule_enabled"
                        )
                    ):
                        apply_sales_prices(selected_slug)
                    enrichment_result = None
                    if enrichment_enabled(selected_slug, "source_import"):
                        with st.spinner(
                            "Bronproducten verrijken volgens tab 8…"
                        ):
                            enrichment_result = run_profile_enrichment(
                                selected_slug, "source_import"
                            )
                    st.success(
                        f"Excelbestand opgeslagen: {imported['seen']} regels; "
                        f"{imported['inserted']} nieuw en "
                        f"{imported['updated']} gewijzigd."
                        + (
                            f" Verrijking: {enrichment_result['processed']} verwerkt, "
                            f"{enrichment_result['failed']} mislukt."
                            if enrichment_result else ""
                        )
                    )
                    reset_product_table_selection(selected_slug)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Excelbestand kon niet worden opgeslagen: {exc}")

        with st.expander("Filterveldkoppelingen", expanded=True):
            st.caption(
                "Koppel een rechtstreeks leveranciersveld als dit aanwezig is. "
                "Bij Niet gekoppeld leidt PIM de waarde gecontroleerd af uit "
                "productgroep, titel en omschrijving."
            )
            filter_targets = {
                "product_group_name": "Productgroep - naam",
                "execution": "Uitvoering",
                "filter": "Filter",
            }
            current_source_mapping = supplier.get("field_mapping") or {}
            source_options = ["", *analysis.fields]
            filter_mapping = {}
            filter_columns = st.columns(3)
            for index, (target, label) in enumerate(filter_targets.items()):
                current = (
                    current_source_mapping.get(target, "")
                    or automatic_mapping.get(target, "")
                )
                if current and current not in source_options:
                    source_options.append(current)
                widget_key = f"source_filter_map_{selected_slug}_{target}"
                filter_mapping[target] = filter_columns[index].selectbox(
                    label,
                    source_options,
                    index=source_options.index(current) if current in source_options else 0,
                    format_func=lambda value: value or "Niet gekoppeld (automatisch afleiden)",
                    key=widget_key,
                )
                show_linked_field_status(
                    widget_key, bool(filter_mapping[target])
                )
            if st.button(
                "Filterveldkoppelingen opslaan",
                key=f"save_filter_mapping_{selected_slug}",
            ):
                save_source_field_mapping(selected_slug, filter_mapping)
                st.success("Filterveldkoppelingen opgeslagen.")
                st.rerun()

with source_purchase_pricing_subtab:
    pricing_analysis = st.session_state.get(f"analysis_{selected_slug}")
    current_unit_mapping = supplier.get("field_mapping") or {}
    saved_source_fields = list(dict.fromkeys(
        str(source) for source in current_unit_mapping.values() if source
    ))
    if pricing_analysis:
        pricing_rule_mapping = suggest_source_field_mapping(
            pricing_analysis.fields
        )
        pricing_learned_mapping = learned_source_field_mapping(
            pricing_analysis.fields,
            claimed_sources=set(pricing_rule_mapping.values()),
            claimed_targets=set(pricing_rule_mapping),
        )
        pricing_automatic_mapping = {
            **pricing_rule_mapping,
            **pricing_learned_mapping,
        }
    else:
        pricing_automatic_mapping = {}

    with st.expander("Inkoop- en verkoopeenheden", expanded=True):
            if pricing_analysis:
                st.caption(
                    "De opgeslagen koppelingen zijn geladen en aangevuld met "
                    "kolommen uit het zojuist onderzochte bronbestand."
                )
            else:
                st.caption(
                    "Deze koppelingen zijn duurzaam bij de leverancier opgeslagen. "
                    "Een nieuw bestand is alleen nodig om andere bronkolommen te kiezen."
                )
            st.caption(
                "Koppel de eenheden en omrekenfactor uit de prijslijst. "
                "Laat een veld leeg wanneer de leverancier uitsluitend per "
                "stuk levert; PIM gebruikt dan stuk → stuk met factor 1."
            )
            unit_targets = {
                "purchase_unit": "Inkoopeenheid",
                "sales_unit": "Verkoopeenheid",
                "purchase_units_per_sales_unit": (
                    "Inkoopeenheden per verkoopeenheid"
                ),
                "unit_calculation_mode": (
                    "Rekenwijze (multiply of divide)"
                ),
                "gross_purchase_price_per_kg": (
                    "Bruto inkoopprijs per kg"
                ),
                "purchase_discount_percent": "Inkoopkorting (%)",
                "net_purchase_price_per_kg": (
                    "Netto inkoopprijs per kg"
                ),
                "kg_per_purchase_unit": "Kg per Ceweld Unit",
                "kg_per_sales_unit": "Kg per Ceweld Bundle",
            }
            unit_options = list(dict.fromkeys([
                "",
                *(pricing_analysis.fields if pricing_analysis else []),
                *saved_source_fields,
            ]))
            unit_mapping = {}
            unit_columns = st.columns(2)
            for index, (target, label) in enumerate(unit_targets.items()):
                current = (
                    current_unit_mapping.get(target, "")
                    or pricing_automatic_mapping.get(target, "")
                )
                if current and current not in unit_options:
                    unit_options.append(current)
                widget_key = f"source_unit_map_{selected_slug}_{target}"
                unit_mapping[target] = unit_columns[index % 2].selectbox(
                    label,
                    unit_options,
                    index=(
                        unit_options.index(current)
                        if current in unit_options else 0
                    ),
                    format_func=lambda value: value or "Niet gekoppeld",
                    key=widget_key,
                )
                show_linked_field_status(
                    widget_key, bool(unit_mapping[target])
                )
            if st.button(
                "Eenheidsveldkoppelingen opslaan",
                key=f"save_unit_mapping_{selected_slug}",
            ):
                save_source_field_mapping(selected_slug, unit_mapping)
                st.success("Eenheidsveldkoppelingen opgeslagen.")
                st.rerun()

    st.markdown("#### Inkoopkortingen")
    st.caption(
        "De regels berekenen uitsluitend de interne inkoopprijs. Verkoopprijzen "
        "en Shopify-prijzen worden hierdoor niet aangepast."
    )
    with st.form(f"discount_rule_{selected_slug}"):
        discount_name = st.text_input(
            "Naam kortingsregel",
            placeholder="Bijvoorbeeld standaard SP Tools-korting",
        )
        discount_scope = st.radio(
            "Toepassen op",
            list(MATCH_FIELDS),
            format_func=lambda value: MATCH_FIELDS[value],
            horizontal=True,
            key=f"discount_scope_{selected_slug}",
        )
        available_values = distinct_match_values(selected_slug, discount_scope)
        if discount_scope == "all":
            discount_value = ""
            st.caption("Deze regel geldt voor alle producten zonder specifiekere regel.")
        elif discount_scope == "sku":
            discount_value = st.text_input(
                "Specifieke SKU",
                placeholder="Bijvoorbeeld 7812873",
                help=(
                    "Vul het volledige leveranciersartikelnummer in. Bij het "
                    "toevoegen wordt de SKU exact tegen de actuele PIM gecontroleerd."
                ),
                key=f"discount_sku_{selected_slug}",
            ).strip()
        elif available_values:
            discount_value = st.selectbox(
                "Productgroep, categorie of SKU",
                available_values,
                key=f"discount_value_{selected_slug}_{discount_scope}",
            )
        else:
            discount_value = st.text_input("Productgroep, categorie of SKU")
        discount_columns = st.columns(3)
        discount_percent = discount_columns[0].number_input(
            "Inkoopkorting (%)",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=0.1,
        )
        discount_basis = discount_columns[1].selectbox(
            "Prijsbasis",
            list(BASIS_FIELDS),
            index=list(BASIS_FIELDS).index("lowest_price"),
            format_func=lambda value: BASIS_FIELDS[value],
        )
        discount_priority = discount_columns[2].number_input(
            "Prioriteit",
            min_value=0,
            max_value=999,
            value=0,
            step=1,
            help="Hogere prioriteit wint. Bij gelijke prioriteit wint de specifiekste regel.",
        )
        add_discount = st.form_submit_button("Kortingsregel toevoegen")
    if add_discount:
        try:
            if discount_scope == "sku" and not lookup_discount_product(
                selected_slug, discount_value
            ):
                raise ValueError(
                    "Deze SKU staat niet in de actuele PIM van deze leverancier."
                )
            save_discount_rule(
                selected_slug,
                name=discount_name or f"{MATCH_FIELDS[discount_scope]} {discount_percent:g}%",
                match_field=discount_scope,
                match_value=discount_value,
                discount_percent=float(discount_percent),
                basis_field=discount_basis,
                priority=int(discount_priority),
            )
            st.success("Kortingsregel toegevoegd.")
            st.rerun()
        except Exception as exc:
            st.error(f"Regel kon niet worden opgeslagen: {exc}")

    discount_rules = list_discount_rules(selected_slug)
    if discount_rules:
        rule_rows = [
            {
                "ID": rule["id"],
                "Actief": bool(rule["enabled"]),
                "Naam": rule["name"],
                "Niveau": MATCH_FIELDS.get(rule["match_field"], rule["match_field"]),
                "Waarde": rule["match_value"] or "Alle producten",
                "Korting %": rule["discount_percent"],
                "Prijsbasis": BASIS_FIELDS.get(rule["basis_field"], rule["basis_field"]),
                "Prioriteit": rule["priority"],
            }
            for rule in discount_rules
        ]
        st.dataframe(pd.DataFrame(rule_rows), width="stretch", hide_index=True)
        rule_options = {
            f"#{rule['id']} · {rule['name']} ({'actief' if rule['enabled'] else 'uitgeschakeld'})": rule
            for rule in discount_rules
        }
        selected_rule_label = st.selectbox(
            "Regel beheren",
            list(rule_options),
            key=f"manage_discount_{selected_slug}",
        )
        selected_rule = rule_options[selected_rule_label]
        new_enabled = st.toggle(
            "Regel actief",
            value=bool(selected_rule["enabled"]),
            key=f"discount_enabled_{selected_slug}_{selected_rule['id']}",
        )
        if new_enabled != bool(selected_rule["enabled"]):
            set_discount_rule_enabled(selected_slug, selected_rule["id"], new_enabled)
            st.rerun()

        selected_basis = st.selectbox(
            "Prijsbasis van geselecteerde regel",
            list(BASIS_FIELDS),
            index=(
                list(BASIS_FIELDS).index(selected_rule["basis_field"])
                if selected_rule["basis_field"] in BASIS_FIELDS else 0
            ),
            format_func=lambda value: BASIS_FIELDS[value],
            key=f"discount_basis_{selected_slug}_{selected_rule['id']}",
        )
        if st.button(
            "Prijsbasis van regel opslaan",
            key=f"save_discount_basis_{selected_slug}_{selected_rule['id']}",
        ):
            set_discount_rule_basis(selected_slug, selected_rule["id"], selected_basis)
            st.success("Prijsbasis aangepast.")
            st.rerun()

        delete_confirmation = st.checkbox(
            f"Ik wil kortingsregel #{selected_rule['id']} definitief verwijderen",
            key=f"confirm_delete_discount_{selected_slug}_{selected_rule['id']}",
        )
        if st.button(
            "Geselecteerde kortingsregel definitief verwijderen",
            disabled=not delete_confirmation,
            type="secondary",
            key=f"delete_discount_{selected_slug}_{selected_rule['id']}",
        ):
            delete_discount_rule(selected_slug, selected_rule["id"])
            st.success(f"Kortingsregel #{selected_rule['id']} is definitief verwijderd.")
            st.rerun()

        preview_limit = st.number_input(
            "Aantal producten in kostprijs-preview",
            min_value=10,
            max_value=1000,
            value=100,
            step=10,
            key=f"discount_preview_limit_{selected_slug}",
        )
        if st.button("Inkoopprijzen voorvertonen", key=f"preview_discount_{selected_slug}"):
            st.session_state[f"discount_preview_{selected_slug}"] = preview_purchase_costs(
                selected_slug, int(preview_limit)
            )
        discount_preview = st.session_state.get(f"discount_preview_{selected_slug}")
        if discount_preview:
            discount_preview_frame = pd.DataFrame(discount_preview)
            # Een smalle eindruimte voorkomt dat de laatste inhoudelijke kolom
            # onder de rechter rand/scrollbar van de datatabel valt.
            discount_preview_frame["_eindruimte"] = ""
            st.dataframe(
                discount_preview_frame,
                width="stretch",
                hide_index=True,
                column_order=[
                    "SKU",
                    "Product",
                    "Productgroep",
                    "Categorie",
                    "Basisprijs",
                    "Kortingsregel",
                    "Korting %",
                    "Berekende inkoopprijs",
                    "Huidige inkoopprijs",
                    "_eindruimte",
                ],
                column_config={
                    "_eindruimte": st.column_config.TextColumn(
                        "", width=35
                    ),
                    "SKU": st.column_config.TextColumn(
                        "SKU", width=135
                    ),
                    "Product": st.column_config.TextColumn(
                        "Product", width=280
                    ),
                    "Productgroep": st.column_config.TextColumn(
                        "Productgroep", width=155
                    ),
                    "Categorie": st.column_config.TextColumn(
                        "Categorie", width=145
                    ),
                    "Basisprijs": st.column_config.NumberColumn(
                        "Basisprijs", format="€ %.2f", width=110
                    ),
                    "Kortingsregel": st.column_config.TextColumn(
                        "Kortingsregel", width=180
                    ),
                    "Korting %": st.column_config.NumberColumn(
                        "Korting %", format="%.2f %%", width=95
                    ),
                    "Berekende inkoopprijs": st.column_config.NumberColumn(
                        "Berekende inkoopprijs",
                        format="€ %.2f",
                        width=165,
                    ),
                    "Huidige inkoopprijs": st.column_config.NumberColumn(
                        "Huidige inkoopprijs",
                        format="€ %.2f",
                        width=155,
                    ),
                },
            )
            confirmation = st.checkbox(
                "Ik heb de preview gecontroleerd en wil de inkoopprijzen opslaan",
                key=f"confirm_discount_{selected_slug}",
            )
            if st.button(
                "Kortingsregels toepassen",
                type="primary",
                disabled=not confirmation,
                key=f"apply_discount_{selected_slug}",
            ):
                result = apply_purchase_costs(selected_slug)
                st.success(
                    f"Inkoopprijzen opgeslagen: {result['updated']}; "
                    f"overgeslagen: {result['skipped']}."
                )
                st.session_state.pop(f"discount_preview_{selected_slug}", None)
                st.rerun()
    else:
        st.info("Nog geen inkoopkortingsregels vastgelegd.")

with source_sales_pricing_subtab:
    st.markdown("#### Verkoopprijsregels")
    st.caption(
        "Maak meerdere regels zoals bij inkoopprijzen. Per product wint eerst "
        "de hoogste prioriteit en daarna het meest specifieke niveau."
    )
    with st.form(f"scoped_sales_rule_{selected_slug}"):
        sales_name = st.text_input(
            "Naam verkoopprijsregel", placeholder="Bijvoorbeeld Tecweld kleppen 30% marge"
        )
        sales_scope = st.radio(
            "Toepassen op", list(MATCH_FIELDS),
            format_func=lambda value: MATCH_FIELDS[value], horizontal=True,
            key=f"sales_scope_{selected_slug}",
        )
        sales_available_values = distinct_match_values(selected_slug, sales_scope)
        if sales_scope == "all":
            sales_match_value = ""
        elif sales_scope == "sku":
            sales_match_value = st.text_input(
                "Specifieke SKU", placeholder="Bijvoorbeeld 7812873",
                help="Wordt bij opslaan exact tegen de actuele PIM gecontroleerd.",
            ).strip()
        elif sales_available_values:
            sales_match_value = st.selectbox(
                "Productgroep of categorie", sales_available_values,
                key=f"sales_match_value_{selected_slug}_{sales_scope}",
            )
        else:
            sales_match_value = st.text_input("Productgroep of categorie").strip()
        sales_form_columns = st.columns(3)
        scoped_sales_type = sales_form_columns[0].selectbox(
            "Rekenmethode", list(SALES_RULE_TYPES),
            format_func=lambda value: SALES_RULE_TYPES[value],
        )
        scoped_sales_value = sales_form_columns[1].number_input(
            "Vaste opslag (€)" if scoped_sales_type == "fixed_markup" else "Waarde (%)",
            min_value=0.0, max_value=100000.0 if scoped_sales_type == "fixed_markup" else 100.0,
            value=0.0 if scoped_sales_type == "none" else 20.0, step=0.5,
            disabled=scoped_sales_type == "none",
        )
        scoped_sales_priority = sales_form_columns[2].number_input(
            "Prioriteit", min_value=0, max_value=999, value=0, step=1,
        )
        add_scoped_sales_rule = st.form_submit_button("Verkoopprijsregel toevoegen")
    if add_scoped_sales_rule:
        try:
            if sales_scope == "sku" and not lookup_discount_product(
                selected_slug, sales_match_value
            ):
                raise ValueError("Deze SKU staat niet in de actuele PIM van deze leverancier.")
            save_scoped_sales_price_rule(
                selected_slug,
                name=sales_name or f"{MATCH_FIELDS[sales_scope]} · {SALES_RULE_TYPES[scoped_sales_type]}",
                match_field=sales_scope, match_value=sales_match_value,
                rule_type=scoped_sales_type, rule_value=float(scoped_sales_value),
                priority=int(scoped_sales_priority),
            )
            st.success("Verkoopprijsregel toegevoegd.")
            st.rerun()
        except Exception as exc:
            st.error(f"Regel kon niet worden opgeslagen: {exc}")

    scoped_sales_rules = list_sales_price_rules(selected_slug)
    if scoped_sales_rules:
        st.dataframe(
            pd.DataFrame([
                {
                    "ID": rule["id"], "Actief": bool(rule["enabled"]),
                    "Naam": rule["name"],
                    "Niveau": MATCH_FIELDS.get(rule["match_field"], rule["match_field"]),
                    "Waarde": rule["match_value"] or "Alle producten",
                    "Rekenmethode": SALES_RULE_TYPES.get(rule["rule_type"], rule["rule_type"]),
                    "Getal": rule["rule_value"], "Prioriteit": rule["priority"],
                } for rule in scoped_sales_rules
            ]),
            hide_index=True,
            width="stretch",
            # Header plus minimaal vijf zichtbare gegevensregels.
            height=220,
        )
        scoped_rule_by_label = {
            f"#{rule['id']} · {rule['name']}": rule for rule in scoped_sales_rules
        }
        scoped_rule_label = st.selectbox(
            "Verkoopprijsregel beheren", list(scoped_rule_by_label),
            key=f"manage_scoped_sales_{selected_slug}",
        )
        managed_sales_rule = scoped_rule_by_label[scoped_rule_label]
        managed_sales_enabled = st.toggle(
            "Regel actief", value=bool(managed_sales_rule["enabled"]),
            key=f"scoped_sales_enabled_{selected_slug}_{managed_sales_rule['id']}",
        )
        if managed_sales_enabled != bool(managed_sales_rule["enabled"]):
            set_sales_price_rule_enabled(
                selected_slug, managed_sales_rule["id"], managed_sales_enabled
            )
            st.rerun()
        with st.expander("Geselecteerde verkoopprijsregel wijzigen", expanded=False):
            with st.form(
                f"edit_scoped_sales_{selected_slug}_{managed_sales_rule['id']}"
            ):
                edit_sales_name = st.text_input(
                    "Naam", value=managed_sales_rule["name"]
                )
                edit_sales_scope = st.radio(
                    "Toepassen op", list(MATCH_FIELDS),
                    index=list(MATCH_FIELDS).index(managed_sales_rule["match_field"]),
                    format_func=lambda value: MATCH_FIELDS[value], horizontal=True,
                    key=f"edit_sales_scope_{selected_slug}_{managed_sales_rule['id']}",
                )
                edit_available_values = distinct_match_values(
                    selected_slug, edit_sales_scope
                )
                if edit_sales_scope == "all":
                    edit_sales_match_value = ""
                elif edit_sales_scope == "sku":
                    edit_sales_match_value = st.text_input(
                        "Specifieke SKU",
                        value=(
                            managed_sales_rule["match_value"]
                            if managed_sales_rule["match_field"] == "sku" else ""
                        ),
                    ).strip()
                elif edit_available_values:
                    current_edit_value = (
                        managed_sales_rule["match_value"]
                        if managed_sales_rule["match_field"] == edit_sales_scope
                        and managed_sales_rule["match_value"] in edit_available_values
                        else edit_available_values[0]
                    )
                    edit_sales_match_value = st.selectbox(
                        "Productgroep of categorie", edit_available_values,
                        index=edit_available_values.index(current_edit_value),
                    )
                else:
                    edit_sales_match_value = st.text_input(
                        "Productgroep of categorie",
                        value=managed_sales_rule["match_value"],
                    ).strip()
                edit_columns = st.columns(3)
                edit_sales_type = edit_columns[0].selectbox(
                    "Rekenmethode", list(SALES_RULE_TYPES),
                    index=list(SALES_RULE_TYPES).index(managed_sales_rule["rule_type"]),
                    format_func=lambda value: SALES_RULE_TYPES[value],
                )
                edit_sales_value = edit_columns[1].number_input(
                    "Vaste opslag (€)" if edit_sales_type == "fixed_markup" else "Waarde (%)",
                    min_value=0.0,
                    max_value=100000.0 if edit_sales_type == "fixed_markup" else 100.0,
                    value=float(managed_sales_rule["rule_value"]), step=0.5,
                    disabled=edit_sales_type == "none",
                )
                edit_sales_priority = edit_columns[2].number_input(
                    "Prioriteit", min_value=0, max_value=999,
                    value=int(managed_sales_rule["priority"]), step=1,
                )
                save_sales_changes = st.form_submit_button("Wijzigingen opslaan")
            if save_sales_changes:
                try:
                    if edit_sales_scope == "sku" and not lookup_discount_product(
                        selected_slug, edit_sales_match_value
                    ):
                        raise ValueError(
                            "Deze SKU staat niet in de actuele PIM van deze leverancier."
                        )
                    update_sales_price_rule(
                        selected_slug, managed_sales_rule["id"],
                        name=edit_sales_name, match_field=edit_sales_scope,
                        match_value=edit_sales_match_value,
                        rule_type=edit_sales_type, rule_value=float(edit_sales_value),
                        priority=int(edit_sales_priority),
                    )
                    st.success("Verkoopprijsregel gewijzigd.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Regel kon niet worden gewijzigd: {exc}")
        confirm_delete_sales = st.checkbox(
            f"Ik wil verkoopprijsregel #{managed_sales_rule['id']} definitief verwijderen",
            key=f"confirm_delete_sales_{selected_slug}_{managed_sales_rule['id']}",
        )
        if st.button(
            "Geselecteerde verkoopprijsregel definitief verwijderen",
            disabled=not confirm_delete_sales,
            key=f"delete_scoped_sales_{selected_slug}_{managed_sales_rule['id']}",
        ):
            delete_sales_price_rule(selected_slug, managed_sales_rule["id"])
            st.success("Verkoopprijsregel definitief verwijderd.")
            st.rerun()
        preview_col, apply_col = st.columns(2)
        if preview_col.button(
            "Alle verkoopprijsregels voorvertonen",
            key=f"preview_scoped_sales_{selected_slug}", width="stretch",
        ):
            st.session_state[f"scoped_sales_preview_{selected_slug}"] = (
                preview_sales_prices(selected_slug, limit=None)
            )
        scoped_preview = st.session_state.get(f"scoped_sales_preview_{selected_slug}")
        if scoped_preview:
            st.dataframe(pd.DataFrame(scoped_preview), hide_index=True, width="stretch")
            confirm_scoped_apply = st.checkbox(
                "Ik heb de voorvertoning gecontroleerd en wil deze verkoopprijzen opslaan",
                key=f"confirm_scoped_sales_{selected_slug}",
            )
            if apply_col.button(
                "Regels toepassen", type="primary", width="stretch",
                disabled=not confirm_scoped_apply,
                key=f"apply_scoped_sales_{selected_slug}",
            ):
                result = apply_sales_prices(selected_slug)
                st.success(
                    f"Verkoopprijzen opgeslagen: {result['updated']}; overgeslagen: {result['skipped']}."
                )
                st.session_state.pop(f"scoped_sales_preview_{selected_slug}", None)
                st.rerun()
    else:
        st.info("Nog geen specifieke verkoopprijsregels. De bestaande algemene instelling blijft actief.")

    st.divider()
    st.markdown("#### Rekenhulp en bestaande algemene instelling")
    st.markdown("#### Verkoopprijsregel")
    st.warning(
        "Let op bij het combineren van verkoopprijsregels: iedere volgende "
        "regel rekent verder met het resultaat van de vorige regel. Daardoor "
        "kunnen korting-op-korting en meerdere toeslagen worden gestapeld. "
        "Controleer altijd het rekenvoorbeeld en de voorvertoning."
    )
    st.caption(
        "De verkoopprijs wordt berekend als bruto prijs min het ingestelde "
        "percentage van de netto inkoopprijs. Voorbeeld: bruto € 100, netto "
        "inkoop € 75 en 20% geeft € 100 − € 15 = € 85 verkoopprijs."
    )
    sales_options = supplier.get("request_options") or {}
    sales_rule_labels = {
        "none": "Doe niets — gebruik het leveranciersveld",
        "discount_from_cost": "Klantkorting als percentage van netto inkoopprijs",
        "max_discount_over_discount": "Maximale korting over onze korting",
        "markup_on_cost": "Vaste opslag per product (%) op de netto inkoopprijs",
        "gross_margin": "Gewenste brutomarge",
        "fixed_markup": "Vaste opslag per product (€) op de netto inkoopprijs",
    }
    sales_rule_type = st.selectbox(
        "Kies korting of toeslag",
        list(sales_rule_labels),
        index=list(sales_rule_labels).index(
            sales_options.get(
                "sales_price_rule_type", "none"
            )
            if sales_options.get(
                "sales_price_rule_type", "none"
            ) in sales_rule_labels else "none"
        ),
        format_func=lambda value: sales_rule_labels[value],
        key=f"sales_rule_type_{selected_slug}",
    )
    max_sales_discount = st.number_input(
        (
            "Vaste opslag (€)" if sales_rule_type == "fixed_markup" else
            "Waarde (%)"
        ),
        min_value=0.0,
        max_value=100.0,
        value=float(sales_options.get("sales_max_discount_percent", 20)),
        step=0.5,
        disabled=sales_rule_type == "none",
        key=f"sales_max_discount_{selected_slug}",
    )
    example_cost = (
        40.0 if sales_rule_type == "max_discount_over_discount" else 75.0
    )
    example_gross = 100.0
    from app.suppliers.discounts import _calculated_sales_price
    example_sales = (
        example_gross if sales_rule_type == "none" else
        _calculated_sales_price(
            example_gross, example_cost, max_sales_discount, sales_rule_type
        ) or 0
    )
    example_discount = example_gross - example_sales
    example_formula = {
        "none": "Geen berekening; bestaande verkoopprijs blijft staan",
        "discount_from_cost": (
            f"€ {example_gross:.2f} − ({max_sales_discount:.1f}% × "
            f"€ {example_cost:.2f})"
        ),
        "max_discount_over_discount": (
            f"€ {example_gross:.2f} × (1 − (60% × "
            f"{max_sales_discount:.1f}%))"
        ),
        "markup_on_cost": (
            f"€ {example_cost:.2f} × (100% + {max_sales_discount:.1f}%)"
        ),
        "gross_margin": (
            f"€ {example_cost:.2f} ÷ (100% − {max_sales_discount:.1f}%)"
        ),
        "fixed_markup": (
            f"€ {example_cost:.2f} + € {max_sales_discount:.2f}"
        ),
    }[sales_rule_type]
    st.markdown(
        f"""
        <div style="font-size:1.35rem;line-height:1.65;padding:1rem 1.2rem;
                    margin:.75rem 0 1rem;border:2px solid #f59e0b;
                    border-radius:.6rem;background:#fffbeb;color:#78350f">
          <strong>Voorbeeld rekenmethode</strong><br>
          {example_formula} =
          <strong>verkoopprijs € {example_sales:.2f}</strong><br>
          <span style="font-size:1.05rem">Klantkorting:
          € {example_discount:.2f}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.info(
        "De leveranciersregel kan direct worden opgeslagen, ook als deze "
        "leverancier nog geen producten in de PIM heeft. Een productvoorvertoning "
        "is alleen een latere controle en is niet vereist voor opslaan."
    )
    if st.button(
        "Verkoopprijsregel direct bij leverancier opslaan",
        key=f"save_sales_rule_config_{selected_slug}",
        help=(
            "Slaat de gekozen rekenmethode direct en duurzaam op. Hiervoor is "
            "geen bronbestand of prijsvoorvertoning nodig."
        ),
    ):
        save_sales_price_rule(
            selected_slug, max_sales_discount,
            rule_type=sales_rule_type, apply_existing=False,
        )
        st.success("Verkoopprijsregel duurzaam bij de leverancier opgeslagen.")
        st.rerun()
    all_actual_products = [
        product for product in list_products(selected_slug, limit=10000)
        if product.get("source_present")
    ]
    def actual_gross_price(product: dict) -> float | None:
        if product.get("price") is not None:
            return float(product["price"])
        if (
            product.get("gross_purchase_price_per_kg") is not None
            and product.get("kg_per_sales_unit") is not None
        ):
            return round(
                float(product["gross_purchase_price_per_kg"])
                * float(product["kg_per_sales_unit"]), 2
            )
        return None

    # Houd ieder actueel artikel vindbaar. Ontbrekende prijsgrondslagen worden
    # na selectie uitgelegd; een product volledig verbergen wekt ten onrechte
    # de indruk dat de SKU niet in de PIM staat.
    actual_products = all_actual_products
    if actual_products:
        actual_by_sku = {product["sku"]: product for product in actual_products}
        product_widget_key = f"sales_example_product_{selected_slug}"
        retained_product_key = f"retained_sales_example_product_{selected_slug}"
        retained_sku = st.session_state.get(retained_product_key)
        if retained_sku in actual_by_sku:
            st.session_state.setdefault(product_widget_key, retained_sku)

        def retain_sales_example_product() -> None:
            st.session_state[retained_product_key] = st.session_state.get(
                product_widget_key
            )

        actual_sku = st.selectbox(
            "Zoek en kies een artikel op SKU, EAN of productnaam",
            list(actual_by_sku),
            format_func=lambda sku: (
                f"{sku} — {actual_by_sku[sku].get('ean') or 'geen EAN'} — "
                f"{actual_by_sku[sku].get('source_title') or actual_by_sku[sku].get('source_description') or 'geen productnaam'}"
                + (
                    " — netto inkoopprijs ontbreekt"
                    if sales_rule_type != "none"
                    and actual_by_sku[sku].get("cost_price") is None
                    else ""
                )
            ),
            key=product_widget_key,
            on_change=retain_sales_example_product,
        )
        st.session_state[retained_product_key] = actual_sku
        actual = actual_by_sku[actual_sku]
        scoped_live_rows = (
            preview_sales_prices(selected_slug, limit=None)
            if list_sales_price_rules(selected_slug, include_disabled=False)
            else []
        )
        scoped_live_row = next(
            (row for row in scoped_live_rows if str(row["SKU"]) == actual_sku),
            None,
        )
        actual_result = (
            scoped_live_row.get("Berekende verkoopprijs")
            if scoped_live_row else
            (
                actual.get("sale_price")
                if actual.get("sale_price") is not None
                else actual_gross_price(actual)
            )
            if sales_rule_type == "none" else
            _calculated_sales_price(
                actual_gross_price(actual), actual.get("cost_price"),
                max_sales_discount, sales_rule_type,
            )
        )
        if scoped_live_row and scoped_live_row.get("Verkoopprijsregel"):
            st.caption(
                "Toegepaste opgeslagen regelketen: "
                f"{scoped_live_row['Verkoopprijsregel']}"
            )
        if actual_result is None:
            if sales_rule_type == "none":
                st.info(
                    "Doe niets is geselecteerd en voor dit artikel is nog geen "
                    "bestaande verkoopprijs opgeslagen. Kies een rekenregel om "
                    "een nieuw resultaat te bekijken."
                )
            else:
                missing_price_fields = []
                if actual_gross_price(actual) is None:
                    missing_price_fields.append("bruto prijs")
                if actual.get("cost_price") is None:
                    missing_price_fields.append("netto inkoopprijs")
                st.error(
                    "Voor dit artikel ontbreekt: "
                    + (
                        " en ".join(missing_price_fields)
                        if missing_price_fields else
                        "een geldige prijsgrondslag voor de gekozen berekening"
                    )
                    + ". Stel deze eerst in bij 5. Inkoopprijzen of controleer "
                    "de prijskoppeling bij 3. Koppelingen."
                )
        else:
            st.markdown(
                f"""
                <div style="font-size:1.45rem;line-height:1.65;padding:1rem 1.2rem;
                            margin:.75rem 0;border:2px solid #2563eb;
                            border-radius:.6rem;background:#eff6ff;color:#1e3a8a">
                  <strong>Live rekenvoorbeeld — {html.escape(actual_sku)}</strong><br>
                  Huidige productprijs: € {float(actual.get('sale_price') if actual.get('sale_price') is not None else actual_gross_price(actual) or 0):.2f}<br>
                  <strong>Resultaat: € {float(actual_result):.2f}</strong>
                  {' — ongewijzigd' if sales_rule_type == 'none' else ''}
                </div>
                """,
                unsafe_allow_html=True,
            )
            product_by_sku = {
                product["sku"]: product for product in all_actual_products
            }
            series_skus: list[str] = []
            series_title = "Oplopende artikelen vanaf gekozen SKU"
            if selected_route.supports_product_families:
                families = get_or_create_product_families(
                    selected_slug, supplier=supplier
                )
                family = next((
                    item for item in families
                    if any(
                        variant.get("sku") == actual_sku
                        for variant in item.get("variants") or []
                    )
                ), None)
                if family:
                    series_skus = [
                        variant["sku"] for variant in family.get("variants") or []
                        if variant.get("sku") in product_by_sku
                    ]
                    series_title = f"Productfamilie: {family['title']}"
            if not series_skus:
                ordered_skus = sorted(
                    product_by_sku,
                    key=lambda sku: (int(sku) if sku.isdigit() else float("inf"), sku),
                )
                start = ordered_skus.index(actual_sku)
                series_skus = ordered_skus[start:start + 10]
            series_rows = []
            for sku in series_skus:
                item = product_by_sku[sku]
                current_price = (
                    item.get("sale_price")
                    if item.get("sale_price") is not None
                    else actual_gross_price(item)
                )
                result_price = (
                    current_price if sales_rule_type == "none" else
                    _calculated_sales_price(
                        actual_gross_price(item), item.get("cost_price"),
                        max_sales_discount, sales_rule_type,
                    )
                )
                series_rows.append({
                    "SKU": sku,
                    "Product": item.get("source_title") or item.get("source_description") or "",
                    "Huidige prijs": current_price,
                    "Resultaat": result_price,
                    "_eindruimte": "",
                })
            st.markdown(f"#### {series_title}")
            st.dataframe(
                pd.DataFrame(series_rows), hide_index=True, width="stretch",
                column_config={
                    "Huidige prijs": st.column_config.NumberColumn(format="€ %.2f"),
                    "Resultaat": st.column_config.NumberColumn(format="€ %.2f"),
                    "_eindruimte": st.column_config.TextColumn("", width=55),
                },
            )
        incomplete_example_products = sum(
            1 for product in all_actual_products
            if actual_gross_price(product) is None
            or product.get("cost_price") is None
        )
        if sales_rule_type != "none" and incomplete_example_products:
            st.caption(
                f"{incomplete_example_products} artikelen zijn wel vindbaar, maar "
                "missen nog een geldige bruto- of netto inkoopprijs. Bij selectie "
                "wordt aangegeven welke prijsgrondslag ontbreekt."
            )
    elif sales_rule_type != "none":
        st.warning(
            "Er zijn nog geen artikelen met zowel een geldige bruto prijs als "
            "netto inkoopprijs. Controleer eerst de prijsvelden bij "
            "3. Koppelingen en de regels bij 5. Inkoopprijzen."
        )
    if st.button(
        "Verkoopprijzen voorvertonen",
        key=f"preview_sales_prices_{selected_slug}",
    ):
        active_scoped_sales_rules = list_sales_price_rules(
            selected_slug, include_disabled=False
        )
        preview_rows = (
            preview_sales_prices(selected_slug, limit=None)
            if active_scoped_sales_rules else
            preview_sales_prices(
                selected_slug, max_sales_discount, limit=None,
                rule_type=sales_rule_type,
            )
        )
        chosen_sku = st.session_state.get(
            f"sales_example_product_{selected_slug}"
        )
        preferred_skus: list[str] = []
        if chosen_sku and selected_route.supports_product_families:
            preview_families = get_or_create_product_families(
                selected_slug, supplier=supplier
            )
            chosen_family = next((
                family for family in preview_families
                if any(
                    variant.get("sku") == chosen_sku
                    for variant in family.get("variants") or []
                )
            ), None)
            if chosen_family:
                preferred_skus = [
                    variant["sku"]
                    for variant in chosen_family.get("variants") or []
                ]
        if chosen_sku and not preferred_skus:
            ordered_preview_skus = sorted(
                (str(row["SKU"]) for row in preview_rows),
                key=lambda sku: (
                    int(sku) if sku.isdigit() else float("inf"), sku
                ),
            )
            if chosen_sku in ordered_preview_skus:
                start = ordered_preview_skus.index(chosen_sku)
                preferred_skus = ordered_preview_skus[start:]
        preferred_position = {
            sku: position for position, sku in enumerate(preferred_skus)
        }
        preview_rows.sort(key=lambda row: (
            0 if str(row["SKU"]) in preferred_position else 1,
            preferred_position.get(str(row["SKU"]), 0),
            str(row["SKU"]),
        ))
        st.session_state[f"sales_price_preview_{selected_slug}"] = preview_rows
    sales_preview = st.session_state.get(f"sales_price_preview_{selected_slug}")
    if sales_preview:
        sales_preview_frame = pd.DataFrame(sales_preview)
        sales_preview_frame["_eindruimte"] = ""
        st.dataframe(
            sales_preview_frame, hide_index=True, width="stretch",
            column_config={
                "Bruto prijs": st.column_config.NumberColumn(format="€ %.2f"),
                "Netto inkoopprijs": st.column_config.NumberColumn(format="€ %.2f"),
                "Onze korting": st.column_config.NumberColumn(format="€ %.2f"),
                "Ingestelde waarde %": st.column_config.NumberColumn(format="%.2f %%"),
                "Klantkorting %": st.column_config.NumberColumn(format="%.2f %%"),
                "Klantkorting": st.column_config.NumberColumn(format="€ %.2f"),
                "Berekende verkoopprijs": st.column_config.NumberColumn(format="€ %.2f"),
                "Huidige verkoopprijs": st.column_config.NumberColumn(format="€ %.2f"),
                "_eindruimte": st.column_config.TextColumn("", width=55),
            },
        )
        confirm_sales_prices = st.checkbox(
            "Ik heb de voorvertoning gecontroleerd en wil de verkoopprijzen opslaan",
            key=f"confirm_sales_prices_{selected_slug}",
        )
        if st.button(
            "Verkoopprijsregel opslaan en toepassen",
            type="primary", disabled=not confirm_sales_prices,
            key=f"apply_sales_prices_{selected_slug}",
        ):
            active_scoped_sales_rules = list_sales_price_rules(
                selected_slug, include_disabled=False
            )
            result = (
                apply_sales_prices(selected_slug)
                if active_scoped_sales_rules else
                save_sales_price_rule(
                    selected_slug, max_sales_discount, rule_type=sales_rule_type
                )
            )
            st.success(
                f"Verkoopprijzen opgeslagen: {result['updated']}; "
                f"overgeslagen zonder bruto- of inkoopprijs: {result['skipped']}."
            )
            st.session_state.pop(f"sales_price_preview_{selected_slug}", None)
            st.rerun()

with source_mapping_subtab:
    mapping_title_column, mapping_load_column = st.columns(
        [2, 1],
        vertical_alignment="center",
    )
    mapping_title_column.markdown("### Koppelingen")
    if mapping_load_column.button(
        "Beschikbare Shopify- en metavelden laden",
        key=f"load_mapping_fields_top_{selected_slug}",
        width="stretch",
    ):
        try:
            with st.spinner("Shopify-velden laden…"):
                st.session_state[
                    f"shopify_writable_fields_{selected_slug}"
                ] = get_shopify_writable_fields()
                st.session_state[
                    f"product_metafields_{selected_slug}"
                ] = get_metafield_definitions("PRODUCT")
                st.session_state[
                    f"variant_metafields_{selected_slug}"
                ] = get_metafield_definitions("PRODUCTVARIANT")
            st.success("Shopify-velden en metavelden zijn geladen.")
            st.rerun()
        except Exception as exc:
            st.error(
                f"Shopify-velden konden niet worden geladen: {exc}"
            )
    st.caption(
        "Dit centrale koppelingsscherm vervangt de oude afzonderlijke vakken "
        "voor standaard Shopify-velden en metavelden."
    )
    mapping_analysis = st.session_state.get(f"analysis_{selected_slug}")
    if not mapping_analysis:
        st.info(
            "Klik eerst in ‘Bron & import’ op ‘Bron lezen en analyseren’. "
            "Daarna verschijnen hier alle leveranciersvelden."
        )
    else:
        mapping_supplier = get_supplier(selected_slug) or {}
        central_selected_index = st.session_state.get(
            f"central_example_product_{selected_slug}", -1
        )
        if not isinstance(central_selected_index, int) or not (
            -1 <= central_selected_index < len(mapping_analysis.records)
        ):
            central_selected_index = -1
        effective_example_index = (
            central_selected_index
            if central_selected_index >= 0 else 0
        )
        central_example_record = (
            mapping_analysis.records[effective_example_index]
            if mapping_analysis.records else {}
        )
        dialog_example_records = (
            [
                central_example_record,
                *[
                    record
                    for index, record in enumerate(mapping_analysis.records)
                    if index != effective_example_index
                ],
            ]
            if mapping_analysis.records else []
        )
        transformations = (
            mapping_supplier.get("source_transformations") or {}
        )
        derived_fields = [
            str(config.get("output_field") or "").strip()
            for config in transformations.values()
            if str(config.get("output_field") or "").strip()
        ]
        custom_fields = [
            field for field, config in transformations.items()
            if config.get("custom_field")
        ]
        mapping_fields = list(dict.fromkeys([
            *mapping_analysis.fields, *custom_fields, *derived_fields,
        ]))
        st.caption(
            f"{len(mapping_analysis.fields)} bronvelden en "
            f"{len(custom_fields)} extra velden en "
            f"{len(derived_fields)} berekende velden beschikbaar. Kies per "
            "bronveld het soort doelveld en daarna het concrete doel."
        )
        writable = st.session_state.get(
            f"shopify_writable_fields_{selected_slug}", []
        )
        product_meta = st.session_state.get(
            f"product_metafields_{selected_slug}", []
        )
        variant_meta = st.session_state.get(
            f"variant_metafields_{selected_slug}", []
        )
        pim_targets = {
            target: target.replace("_", " ").capitalize()
            for target in DEFAULT_MAPPING
        }
        pim_targets.update({
            "price": "Van-voor prijs",
            "sale_price": "Verkoopprijs",
        })
        shopify_targets = {
            field["target"]: (
                f"{field['section']} · {field['field']} ({field['type']})"
            )
            for field in writable
        }
        product_meta_targets = {
            f"product:{field['namespace']}.{field['key']}": (
                f"{field['name']} · {field['namespace']}.{field['key']} "
                f"({(field.get('type') or {}).get('name', '')})"
            )
            for field in product_meta
        }
        variant_meta_targets = {
            f"variant:{field['namespace']}.{field['key']}": (
                f"{field['name']} · {field['namespace']}.{field['key']} "
                f"({(field.get('type') or {}).get('name', '')})"
            )
            for field in variant_meta
        }
        for identifier, config in (
            mapping_supplier.get("shopify_metafield_mapping") or {}
        ).items():
            label = (
                f"{config.get('name') or config.get('key')} · "
                f"{config.get('namespace')}.{config.get('key')} "
                f"({config.get('type') or 'tekst'})"
            )
            if config.get("owner") == "product":
                product_meta_targets.setdefault(identifier, label)
            elif config.get("owner") == "variant":
                variant_meta_targets.setdefault(identifier, label)
        meta_definitions = {
            f"{owner}:{field['namespace']}.{field['key']}": {
                "owner": owner,
                "namespace": field["namespace"],
                "key": field["key"],
                "type": (field.get("type") or {}).get("name", ""),
                "name": field.get("name") or "",
            }
            for owner, definitions in (
                ("product", product_meta), ("variant", variant_meta)
            )
            for field in definitions
        }
        meta_definitions.update({
            identifier: {
                "owner": config.get("owner") or "product",
                "namespace": config.get("namespace") or "",
                "key": config.get("key") or "",
                "type": config.get("type") or "",
                "name": config.get("name") or "",
            }
            for identifier, config in (
                mapping_supplier.get("shopify_metafield_mapping") or {}
            ).items()
        })
        current_assignments: dict[str, tuple[str, str]] = {}
        duplicate_sources: set[str] = set()

        def register_assignment(source: str, kind: str, target: str) -> None:
            if not source:
                return
            if source in current_assignments:
                duplicate_sources.add(source)
                return
            current_assignments[source] = (kind, target)

        for target, source in (
            mapping_supplier.get("field_mapping") or {}
        ).items():
            register_assignment(source, "pim", target)
        for target, source in (
            mapping_supplier.get("shopify_field_mapping") or {}
        ).items():
            register_assignment(source, "shopify", target)
        for identifier, config in (
            mapping_supplier.get("shopify_metafield_mapping") or {}
        ).items():
            register_assignment(
                config.get("source_field") or "",
                config.get("owner") or "product",
                identifier,
            )
        if duplicate_sources:
            st.warning(
                "Deze bronvelden zijn momenteel aan meerdere doelen gekoppeld "
                "en worden hier éénmaal getoond: "
                + ", ".join(sorted(duplicate_sources))
            )

        kind_labels = {
            "ignore": "Niet koppelen",
            "pim": "PIM-standaardveld",
            "shopify": "Shopify-standaardveld",
            "product": "Productmetaveld",
            "variant": "Variantmetaveld",
        }
        selected_rows: list[dict[str, str]] = []
        header = st.columns([2.1, 1.9, 2.1, 3.5, 1.1])
        header[0].markdown("**Bronveld / voorbeeld**")
        header[1].markdown("**Veldtype**")
        header[2].markdown("**Soort koppeling**")
        header[3].markdown("**Beschikbaar doelveld**")
        header[4].markdown("**Opties**")
        for field_index, source_field in enumerate(mapping_fields):
            field_widget_id = hashlib.sha1(
                source_field.encode("utf-8")
            ).hexdigest()[:12]
            example_value = apply_source_transformations(
                central_example_record, transformations
            ).get(source_field, "")
            field_config = transformations.get(source_field, {})
            current_kind, current_target = current_assignments.get(
                source_field,
                (
                    field_config.get("suggested_target_kind") or "ignore",
                    field_config.get("suggested_target") or "",
                ),
            )
            row_columns = st.columns([2.1, 1.9, 2.1, 3.5, 1.1])
            row_columns[0].markdown(
                f"**{html.escape(source_field)}**  \n"
                f"<span style='color:#777'>"
                f"{html.escape(str(example_value)[:80])}</span>",
                unsafe_allow_html=True,
            )
            current_processing_type = infer_processing_type(
                transformations.get(source_field, {})
            )
            processing_type_key = (
                f"processing_type_{selected_slug}_{field_widget_id}"
            )
            selected_processing_type = row_columns[1].selectbox(
                f"Veldtype voor {source_field}",
                list(PROCESSING_TYPE_LABELS),
                index=list(PROCESSING_TYPE_LABELS).index(
                    current_processing_type
                ),
                format_func=lambda value: PROCESSING_TYPE_LABELS[value],
                label_visibility="collapsed",
                key=processing_type_key,
            )
            kind_key = (
                f"unified_kind_{selected_slug}_{field_widget_id}"
            )
            selected_kind = row_columns[2].selectbox(
                f"Soort voor {source_field}",
                list(kind_labels),
                index=list(kind_labels).index(current_kind),
                format_func=lambda value: kind_labels[value],
                label_visibility="collapsed",
                key=kind_key,
            )
            target_maps = {
                "ignore": {}, "pim": pim_targets,
                "shopify": shopify_targets,
                "product": product_meta_targets,
                "variant": variant_meta_targets,
            }
            available_target_map = target_maps[selected_kind]
            target_options = ["", *available_target_map]
            target_key = (
                f"unified_target_{selected_slug}_{field_widget_id}_"
                f"{selected_kind}"
            )
            selected_target = row_columns[3].selectbox(
                f"Doel voor {source_field}",
                target_options,
                index=(
                    target_options.index(current_target)
                    if current_target in target_options else 0
                ),
                format_func=lambda value: (
                    available_target_map.get(value)
                    if value else "Selecteer doelveld"
                ),
                label_visibility="collapsed",
                disabled=selected_kind == "ignore",
                key=target_key,
            )
            show_linked_field_status(
                kind_key,
                selected_kind != "ignore" and bool(selected_target),
            )
            rules_button_key = (
                f"transform_rules_{selected_slug}_{field_widget_id}"
            )
            has_rules = transformation_has_rules(
                transformations.get(source_field, {})
            )
            if row_columns[4].button(
                "Regels" + (" ✓" if has_rules else ""),
                key=rules_button_key,
                disabled=selected_processing_type == "none",
            ):
                show_transformation_dialog(
                    selected_slug,
                    source_field,
                    mapping_analysis.fields,
                    central_example_record,
                    selected_processing_type,
                    dialog_example_records,
                )
            show_rules_button_status(rules_button_key, has_rules)
            selected_rows.append({
                "source": source_field,
                "kind": selected_kind,
                "target": selected_target,
                "processing_type": selected_processing_type,
            })

        selected_target_identities = {
            (row["kind"], row["target"])
            for row in selected_rows
            if row["kind"] != "ignore" and row["target"]
        }
        extra_target_maps = {
            "shopify": shopify_targets,
            "product": product_meta_targets,
            "variant": variant_meta_targets,
        }
        extra_target_options = [
            (kind, target)
            for kind, targets in extra_target_maps.items()
            for target in targets
            if (kind, target) not in selected_target_identities
        ]
        with st.expander("Extra veld toevoegen"):
            if st.button(
                "Beschikbare Shopify- en metavelden laden",
                key=f"load_unified_mapping_fields_{selected_slug}",
            ):
                try:
                    with st.spinner("Shopify-velden laden…"):
                        st.session_state[
                            f"shopify_writable_fields_{selected_slug}"
                        ] = get_shopify_writable_fields()
                        st.session_state[
                            f"product_metafields_{selected_slug}"
                        ] = get_metafield_definitions("PRODUCT")
                        st.session_state[
                            f"variant_metafields_{selected_slug}"
                        ] = get_metafield_definitions("PRODUCTVARIANT")
                    st.rerun()
                except Exception as exc:
                    st.error(
                        f"Shopify-velden konden niet worden geladen: {exc}"
                    )
            with st.form(f"add_custom_field_{selected_slug}"):
                selected_extra_target = st.selectbox(
                    "Nog beschikbaar veld",
                    extra_target_options,
                    format_func=lambda option: (
                        f"{kind_labels[option[0]]} · "
                        f"{extra_target_maps[option[0]][option[1]]}"
                    ),
                    disabled=not extra_target_options,
                    help=(
                        "Hier staan alleen Shopify-standaardvelden, "
                        "productmetavelden en variantmetavelden die nog niet "
                        "in de koppelingen gebruikt worden."
                    ),
                )
                custom_field_type = st.selectbox(
                    "Veldtype",
                    [
                        "collection", "nested", "text",
                        "calculation", "stock",
                    ],
                    format_func=lambda value: PROCESSING_TYPE_LABELS[value],
                )
                add_custom_field = st.form_submit_button(
                    "Extra veld toevoegen",
                    disabled=not extra_target_options,
                )
            if add_custom_field:
                target_kind, target_name = selected_extra_target
                clean_custom_name = re.sub(
                    r"[^A-Za-z0-9_]+", "_", target_name
                ).strip("_")
                clean_custom_name = clean_custom_name or "extra_veld"
                existing_field_names = {
                    *mapping_analysis.fields,
                    *transformations,
                }
                base_name = clean_custom_name
                suffix = 2
                while clean_custom_name in existing_field_names:
                    clean_custom_name = f"{base_name}_{suffix}"
                    suffix += 1
                transformations[clean_custom_name] = {
                    "custom_field": True,
                    "processing_type": custom_field_type,
                    "suggested_target_kind": target_kind,
                    "suggested_target": target_name,
                }
                save_source_transformations(
                    selected_slug, transformations
                )
                st.success(
                    f"Extra veld ‘{clean_custom_name}’ toegevoegd en "
                    "vooraf gekoppeld. Open Regels om de bronvelden te kiezen."
                )
                st.rerun()

        missing_stock_locations = [
            row["source"] for row in selected_rows
            if (
                row["processing_type"] == "stock"
                and not (
                    transformations.get(row["source"], {}).get(
                        "inventory_location_id"
                    )
                )
            )
        ]
        if missing_stock_locations:
            st.warning(
                "Kies via Regels verplicht een Shopify-magazijn voor: "
                + ", ".join(missing_stock_locations)
            )
        if st.button(
            "Alle koppelingen opslaan",
            type="primary",
            key=f"save_unified_mappings_{selected_slug}",
            disabled=bool(missing_stock_locations),
        ):
            current_fields = set(mapping_fields)
            new_pim = {
                target: source
                for target, source in (
                    mapping_supplier.get("field_mapping") or {}
                ).items() if source not in current_fields
            }
            new_shopify = {
                target: source
                for target, source in (
                    mapping_supplier.get("shopify_field_mapping") or {}
                ).items() if source not in current_fields
            }
            new_meta = {
                identifier: config
                for identifier, config in (
                    mapping_supplier.get(
                        "shopify_metafield_mapping"
                    ) or {}
                ).items()
                if config.get("source_field") not in current_fields
            }
            used_targets: set[tuple[str, str]] = set()
            conflicts = []
            for row in selected_rows:
                source, kind, target = (
                    row["source"], row["kind"], row["target"]
                )
                if kind == "ignore" or not target:
                    continue
                identity = (kind, target)
                if identity in used_targets:
                    conflicts.append(target)
                    continue
                used_targets.add(identity)
                if kind == "pim":
                    new_pim[target] = source
                elif kind == "shopify":
                    new_shopify[target] = source
                elif kind in {"product", "variant"}:
                    new_meta[target] = {
                        **meta_definitions[target],
                        "source_field": source,
                    }
            if conflicts:
                st.error(
                    "Een doelveld kan maar één bronveld hebben. Dubbel gekozen: "
                    + ", ".join(sorted(set(conflicts)))
                )
            else:
                new_transformations = dict(transformations)
                for row in selected_rows:
                    source = row["source"]
                    processing_type = row["processing_type"]
                    if processing_type == "none":
                        new_transformations.pop(source, None)
                        continue
                    field_rules = dict(
                        new_transformations.get(source) or {}
                    )
                    field_rules["processing_type"] = processing_type
                    field_rules.pop("suggested_target_kind", None)
                    field_rules.pop("suggested_target", None)
                    new_transformations[source] = field_rules
                save_unified_source_mappings(
                    selected_slug,
                    pim_mapping=new_pim,
                    shopify_mapping=new_shopify,
                    metafield_mapping=new_meta,
                )
                save_source_transformations(
                    selected_slug, new_transformations
                )
                st.success("Alle bronveldkoppelingen opgeslagen.")
                st.rerun()


with families_tab:
    st.markdown("#### Vastgelegde productfamilies")
    st.info(
        "De families worden in de PIM-database bewaard en bij gewone imports "
        "niet opnieuw berekend. Alleen regels "
        "met een exacte SKU en positieve bruto- én nettoprijs worden als "
        "verkoopbare variant opgenomen."
    )
    if selected_slug == "kentie":
        st.markdown("##### Nieuwe Kentie-productfamilie samenstellen")
        st.caption(
            "Zoekt uitsluitend in de Kentie-PIM. Gebruik meerdere SKU’s "
            "gescheiden door komma’s, of één gelijk deel van de productnaam. "
            "Er wordt nooit met een andere leverancier gekoppeld."
        )
        kentie_family_query = st.text_input(
            "Kentie-SKU’s of gelijk productnaamdeel",
            key="kentie_family_candidate_query",
            placeholder="PR6, PR115, PR18 of bijvoorbeeld Stronghand C-klem",
        )
        if st.button(
            "Zoek Kentie-producten",
            key="search_kentie_family_candidates",
            disabled=not kentie_family_query.strip(),
        ):
            st.session_state["kentie_family_candidates"] = (
                search_kentie_family_candidates(kentie_family_query)
            )
        kentie_candidates = st.session_state.get(
            "kentie_family_candidates", []
        )
        if kentie_candidates:
            candidate_rows = pd.DataFrame([{
                "Selecteren": True,
                "SKU": item["sku"],
                "Leveranciers-SKU": item.get("supplier_sku") or "",
                "Productnaam": item.get("ai_title") or item.get("source_title") or "",
                "EAN": item.get("ean") or "",
                "Verkoopprijs": item.get("sale_price"),
                "Foto's": len(item.get("images") or []),
            } for item in kentie_candidates])
            edited_candidates = st.data_editor(
                candidate_rows,
                key="kentie_family_candidate_selection",
                hide_index=True,
                width="stretch",
                disabled=[
                    "SKU", "Leveranciers-SKU", "Productnaam", "EAN",
                    "Verkoopprijs", "Foto's",
                ],
                column_config={
                    "Selecteren": st.column_config.CheckboxColumn(required=True),
                },
            )
            selected_kentie_skus = edited_candidates.loc[
                edited_candidates["Selecteren"], "SKU"
            ].tolist()
            st.caption(
                f"{len(selected_kentie_skus)} van {len(kentie_candidates)} "
                "Kentie-producten geselecteerd."
            )
            kentie_family_title = st.text_input(
                "Naam van het gezamenlijke Shopify-product",
                key="kentie_family_title",
                placeholder="Laat leeg om de gezamenlijke productnaam te gebruiken",
            )
            if st.button(
                "Controleer bestaande Shopify-SKU’s en maak verwijdervoorstel",
                key="preview_kentie_shopify_removals",
                disabled=len(selected_kentie_skus) < 2,
            ):
                with st.spinner("Shopify uitsluitend op exacte SKU controleren…"):
                    st.session_state["kentie_shopify_removal_preview"] = (
                        preview_kentie_shopify_removals(selected_kentie_skus)
                    )
            removal_preview = st.session_state.get(
                "kentie_shopify_removal_preview"
            )
            if removal_preview:
                existing_rows = removal_preview["existing_variants"]
                if existing_rows:
                    st.warning(
                        f"{len(existing_rows)} geselecteerde SKU('s) bestaan al "
                        "in Shopify. Dit is uitsluitend een voorstel; er is niets verwijderd."
                    )
                    st.dataframe(
                        pd.DataFrame([{
                            "SKU": item["sku"],
                            "Bestaand Shopify-product": item["product_title"],
                            "Variant": item["variant_title"],
                            "Leverancier": item["vendor"],
                            "Status": item["status"],
                            "Product-ID": item["product_id"],
                            "Variant-ID": item["variant_id"],
                        } for item in existing_rows]),
                        hide_index=True, width="stretch",
                    )
                    with st.expander("Veilig migratie- en verwijdervoorstel", expanded=True):
                        for index, step in enumerate(
                            removal_preview["safe_sequence"], 1
                        ):
                            st.write(f"{index}. {step}")
                        st.caption(
                            "Niet-geselecteerde varianten en producten van andere "
                            "leveranciers blijven buiten het voorstel."
                        )
                else:
                    st.success(
                        "Geen geselecteerde Kentie-SKU bestaat momenteel in Shopify; "
                        "een verwijdervoorstel is niet nodig."
                    )
            if st.button(
                "Selectie met officiële Kentie-gegevens verrijken",
                key="enrich_kentie_family_selection",
                disabled=len(selected_kentie_skus) < 2,
            ):
                progress = st.progress(0, text="Kentie-varianten verrijken…")
                messages = []
                for index, sku in enumerate(selected_kentie_skus, 1):
                    try:
                        result = import_official_website_product("kentie", sku)
                        messages.append(
                            f"{sku}: {result.get('images', 0)} foto('s)"
                        )
                    except Exception as exc:
                        # De PIM-regel blijft bruikbaar; alleen aanvullende
                        # officiële verrijking van deze SKU ontbreekt dan.
                        messages.append(f"{sku}: PIM behouden; {exc}")
                    progress.progress(
                        index / len(selected_kentie_skus),
                        text=f"{index}/{len(selected_kentie_skus)} verwerkt",
                    )
                st.session_state["kentie_family_enrichment_messages"] = messages
                st.session_state["kentie_family_candidates"] = (
                    search_kentie_family_candidates(
                        ",".join(selected_kentie_skus)
                    )
                )
                st.success("Kentie-selectie opnieuw vanuit de PIM geladen.")
                st.rerun()
            if st.session_state.get("kentie_family_enrichment_messages"):
                with st.expander("Resultaat Kentie-verrijking"):
                    for line in st.session_state[
                        "kentie_family_enrichment_messages"
                    ]:
                        st.write(line)
            if st.button(
                "Productfamilie opslaan",
                key="save_kentie_product_family",
                type="primary",
                disabled=len(selected_kentie_skus) < 2,
            ):
                family = save_kentie_product_family(
                    selected_kentie_skus, kentie_family_title
                )
                st.session_state["saved_kentie_family"] = family
                st.success(
                    f"Kentie-productfamilie opgeslagen met "
                    f"{len(family['variants'])} varianten."
                )
            saved_kentie_family = st.session_state.get("saved_kentie_family")
            if saved_kentie_family:
                st.markdown(
                    f"**Opgeslagen:** {saved_kentie_family['title']} · "
                    f"{len(saved_kentie_family['variants'])} varianten"
                )
                if st.button(
                    "Maak Kentie-productfamilie als Shopify-concept",
                    key="create_kentie_shopify_family_draft",
                    type="primary",
                ):
                    with st.spinner(
                        "Nieuw, zelfstandig Kentie-productconcept maken…"
                    ):
                        result = create_kentie_shopify_family_draft(
                            saved_kentie_family["family_key"]
                        )
                    st.success(
                        f"Shopify-concept {result['title']} gemaakt met "
                        f"{len(result['variants'])} varianten."
                    )
                    st.markdown(
                        f"[Open product in Shopify]({result['admin_url']})"
                    )
        else:
            st.info("Nog geen Kentie-producten gevonden.")
    elif not selected_route.supports_product_families:
        st.caption(
            "De eerste gecontroleerde productfamilie-engine is beschikbaar "
            "voor Certilas."
        )
    else:
        try:
            st.markdown("##### Shopify-familieherbouw (alleen Certilas)")
            st.caption(
                "Maakt vanuit de PIM een Certilas-familievoorstel. Shopify wordt alleen "
                "uitgelezen voor product-ID's, SKU's, handles en status. Er "
                "wordt in deze stap niets gewijzigd of verwijderd."
            )
            if st.button(
                "Migratiesnapshot en familievoorstel maken",
                key="certilas_family_rebuild_plan",
                type="secondary",
            ):
                with st.spinner("Certilas-migratiesnapshot en familieplan maken…"):
                    st.session_state["certilas_family_rebuild_plan_result"] = (
                        create_family_rebuild_backup_and_plan("certilas")
                    )
                st.success("Certilas-migratiesnapshot en familievoorstel zijn gemaakt.")
            rebuild_plan = st.session_state.get(
                "certilas_family_rebuild_plan_result"
            )
            if rebuild_plan:
                plan_metrics = st.columns(5)
                plan_metrics[0].metric(
                    "Shopify-producten", rebuild_plan["shopify_products"]
                )
                plan_metrics[1].metric(
                    "PIM-families", rebuild_plan["pim_families"]
                )
                plan_metrics[2].metric(
                    "Verkoopbare families",
                    rebuild_plan.get("families_to_build", "—"),
                )
                plan_metrics[3].metric(
                    "Samenvoegingen", rebuild_plan["families_to_merge"]
                )
                plan_metrics[4].metric(
                    "Nieuwe families", rebuild_plan["families_to_create"]
                )
                st.code(
                    rebuild_plan.get("migration_snapshot_path")
                    or rebuild_plan["backup_path"],
                    language=None,
                )
                st.code(rebuild_plan["plan_path"], language=None)
                if st.button(
                    "Nieuwe families als Concept opbouwen",
                    key="start_certilas_family_rebuild",
                    type="primary",
                ):
                    start_family_rebuild("certilas", rebuild_plan["plan_path"])
                    st.success("De conceptopbouw is op de achtergrond gestart.")
            rebuild_state = get_family_rebuild_state()
            if rebuild_state:
                display_progress = int(rebuild_state.get("progress") or 0)
                display_message = str(rebuild_state.get("message") or "")
                finalize_counts = ""
                if rebuild_state.get("status") == "finalizing":
                    built_total = max(1, len(rebuild_state.get("built") or {}))
                    activated = len(
                        rebuild_state.get("activated_product_ids") or []
                    )
                    deleted = len(
                        rebuild_state.get("deleted_product_ids") or []
                    )
                    redirected = len(
                        rebuild_state.get("created_redirect_paths") or []
                    )
                    old_total = redirect_total = 0
                    try:
                        migration_plan = json.loads(
                            Path(rebuild_state["plan_path"]).read_text()
                        )
                        old_total = len({
                            product_id
                            for family in migration_plan.get("families") or []
                            for product_id in family.get("shopify_product_ids") or []
                        })
                        redirect_total = len({
                            handle
                            for family in migration_plan.get("families") or []
                            for handle in family.get("old_handles") or []
                            if handle
                        })
                    except (OSError, KeyError, json.JSONDecodeError):
                        pass
                    phase = rebuild_state.get("phase")
                    if phase == "activate":
                        display_progress = int(60 * activated / built_total)
                        display_message = (
                            f"Nieuwe families activeren: {activated} van "
                            f"{built_total} gereed."
                        )
                    elif phase == "remove_old":
                        display_progress = 60 + int(
                            25 * deleted / max(1, old_total)
                        )
                        display_message = (
                            f"Oude Certilas-producten vervangen: {deleted} van "
                            f"{old_total} verwijderd."
                        )
                    elif phase == "redirects":
                        display_progress = 85 + int(
                            15 * redirected / max(1, redirect_total)
                        )
                        display_message = (
                            f"Shopify-redirects aanmaken: {redirected} van "
                            f"{redirect_total} gereed."
                        )
                    finalize_counts = (
                        f" · Geactiveerd: {activated}/{built_total}"
                        f" · Verwijderd: {deleted}/{old_total or '—'}"
                        f" · Redirects: {redirected}/{redirect_total or '—'}"
                    )
                st.progress(
                    max(0, min(100, display_progress)),
                    text=display_message,
                )
                st.caption(
                    f"Status: {rebuild_state.get('status')} · "
                    f"Conceptfamilies gereed: {len(rebuild_state.get('built') or {})}"
                    f"{finalize_counts}"
                )
                if st.button("Migratiestatus vernieuwen", key="refresh_certilas_rebuild"):
                    st.rerun()
                if rebuild_state.get("status") == "redirects_pending":
                    st.warning(
                        "De productmigratie is voltooid. De redirects wachten "
                        "op de Shopify-scope write_online_store_navigation. "
                        "Voeg die scope toe en hervat daarna de afronding."
                    )
                elif rebuild_state.get("status") == "failed":
                    st.error(rebuild_state.get("error") or "De migratie is mislukt.")
                resumable_finalize = (
                    rebuild_state.get("status") in {
                        "failed", "redirects_pending"
                    }
                    and rebuild_state.get("phase")
                    in {"activate", "remove_old", "redirects"}
                )
                if (
                    rebuild_state.get("status") == "ready_to_finalize"
                    or resumable_finalize
                ):
                    if rebuild_state.get("status") == "redirects_pending":
                        st.info(
                            "Na het toevoegen van de Shopify-scope kun je met "
                            "dezelfde bevestiging uitsluitend de ontbrekende "
                            "redirects hervatten."
                        )
                    else:
                        st.success(
                            "Alle nieuwe families staan gecontroleerd op Concept. "
                            "Pas na onderstaande bevestiging worden ze geactiveerd "
                            "en worden de oude Certilas-producten vervangen."
                        )
                    confirmation = st.text_input(
                        f"Typ exact: {FINAL_CONFIRMATION}",
                        key="certilas_finalize_confirmation",
                    )
                    understood = st.checkbox(
                        "Ik begrijp dat daarna uitsluitend de oude Certilas-producten worden verwijderd.",
                        key="certilas_finalize_understood",
                    )
                    if st.button(
                        "Nieuwe families activeren en oude Certilas-producten vervangen",
                        key="finalize_certilas_family_rebuild",
                        type="primary",
                        disabled=not (
                            understood and confirmation == FINAL_CONFIRMATION
                        ),
                    ):
                        with st.spinner("Certilas-familiemigratie afronden…"):
                            finalize_family_rebuild("certilas", confirmation)
                        st.success(
                            "De gecontroleerde activering en vervanging zijn "
                            "op de achtergrond gestart."
                        )
            action_column, source_column = st.columns([1, 3])
            with action_column:
                if st.button(
                    "Productfamilies opnieuw berekenen",
                    key="rebuild_certilas_product_families",
                    help=(
                        "Vervangt de opgeslagen families door een nieuwe berekening "
                        "op basis van de actuele Certilas-producten."
                    ),
                ):
                    family_proposals = rebuild_product_families(
                        selected_slug, supplier=supplier
                    )
                    st.success("De opgeslagen productfamilies zijn opnieuw berekend.")
                else:
                    family_proposals = get_or_create_product_families(
                        selected_slug, supplier=supplier
                    )
            research_source = (
                family_proposals[0].get("research_source", {})
                if family_proposals else {}
            )
            with source_column:
                if research_source.get("ai_research_allowed"):
                    st.success(
                        "Aanvullend AI-brononderzoek toegestaan via "
                        f"{research_source.get('website_url')}. Exacte SKU- of "
                        "EAN-koppeling blijft verplicht."
                    )
                else:
                    st.caption("Aanvullend AI-brononderzoek is niet ingeschakeld.")
            bulk_status = catalogue_enrichment_status(selected_slug)
            bulk_job = bulk_status.get("job") or {}
            bulk_complete = (
                bulk_status["eligible"] > 0 and bulk_status["pending"] == 0
            )
            bulk_color = "#16803a" if bulk_complete else "#c62828"
            st.markdown(
                f"""
                <style>
                [class~="st-key-certilas_bulk_enrichment"] button {{
                    background: {bulk_color} !important;
                    border-color: {bulk_color} !important;
                    color: white !important;
                    font-weight: 800 !important;
                }}
                </style>
                """,
                unsafe_allow_html=True,
            )
            button_label = (
                "Catalogus volledig verrijkt"
                if bulk_complete
                else f"Gehele catalogus met AI verrijken ({bulk_status['pending']} families)"
            )
            if st.button(
                button_label,
                key="certilas_bulk_enrichment",
                width="stretch",
                help=(
                    "Rood: er zijn families die nog verrijkt moeten worden. "
                    "Groen: alle via EAN koppelbare families zijn verrijkt."
                ),
            ):
                if bulk_complete:
                    st.success("Alle via EAN koppelbare productfamilies zijn al verrijkt.")
                elif bulk_job.get("status") in {"queued", "running"}:
                    st.info("De catalogusverrijking draait al op de achtergrond.")
                else:
                    start_catalogue_enrichment(selected_slug)
                    st.success("Catalogusverrijking is op de achtergrond gestart.")
                    st.rerun()
            if bulk_job.get("status") in {"queued", "running"}:
                total = max(1, int(bulk_job.get("total") or bulk_status["pending"] or 1))
                completed = int(bulk_job.get("completed") or 0)
                st.progress(
                    min(1.0, completed / total),
                    text=(
                        f"{completed} van {total} families verwerkt"
                        + (f" · {bulk_job['current_family']}" if bulk_job.get("current_family") else "")
                    ),
                )
                if st.button("Voortgang vernieuwen", key="refresh_bulk_enrichment"):
                    st.rerun()
            elif bulk_job.get("message"):
                if bulk_job.get("status") == "completed":
                    st.success(bulk_job["message"])
                elif bulk_job.get("status") == "completed_with_errors":
                    st.warning(bulk_job["message"])
                    if bulk_job.get("error"):
                        with st.expander("Laatste fouten"):
                            st.text(bulk_job["error"])
            family_search = st.text_input(
                "Zoeken in productfamilies",
                placeholder="Zoek op familienaam, product, SKU of EAN",
                key="certilas_family_search",
                help="Zoekt ook in alle varianten binnen een familie.",
            ).strip().casefold()
            visible_families = [
                family for family in family_proposals
                if not family_search
                or family_search in family["title"].casefold()
                or family_search in family["family_key"].casefold()
                or any(
                    family_search in str(item.get(field) or "").casefold()
                    for item in family["variants"]
                    for field in ("sku", "ean", "variant_title", "source_description")
                )
            ]
            st.caption(
                f"{len(visible_families)} van {len(family_proposals)} families gevonden."
            )
            metrics = st.columns(4)
            metrics[0].metric("Productfamilies", len(family_proposals))
            metrics[1].metric(
                "Verkoopbare varianten",
                sum(len(family["variants"]) for family in family_proposals),
            )
            metrics[2].metric(
                "Uitgesloten nulprijzen",
                sum(len(family["excluded"]) for family in family_proposals),
            )
            metrics[3].metric(
                "Families met afbeelding",
                sum(bool(family["image_url"]) for family in family_proposals),
            )
            family_limit = st.selectbox(
                "Aantal voorstellen tonen", [10, 25, 50, 100], index=1,
                key="certilas_family_limit",
            )
            for family in visible_families[:int(family_limit)]:
                with st.container(border=True):
                    image_column, detail_column = st.columns([1, 5])
                    with image_column:
                        if family["image_url"]:
                            st.image(family["image_url"], width="stretch")
                        else:
                            st.markdown(
                                "<div class='pim-image-placeholder'>"
                                "Geen hoofdafbeelding</div>",
                                unsafe_allow_html=True,
                            )
                    with detail_column:
                        st.markdown(f"##### {html.escape(family['title'])}")
                        st.caption(
                            f"Familiesleutel: `{family['family_key']}` · "
                            f"{family['process']} · {family['form']} · "
                            f"{len(family['variants'])} verkoopbare varianten"
                        )
                    variant_rows = [
                        {
                            "Variant": item["variant_title"],
                            "SKU": item["sku"],
                            "EAN": item.get("ean") or "",
                            "Bruto/kg": item.get("gross_purchase_price_per_kg"),
                            "Netto/kg": item.get("net_purchase_price_per_kg"),
                            "Verpakking kg": item.get("kg_per_purchase_unit"),
                            "Verrijkt": "Ja" if item.get("html_description") else "Nee",
                            "Foto's": len(item.get("images") or []),
                        }
                        for item in family["variants"]
                    ]
                    st.dataframe(
                        pd.DataFrame(variant_rows),
                        width="stretch", hide_index=True,
                    )
                    enriched_variants = [
                        item for item in family["variants"]
                        if item.get("html_description")
                    ]
                    family_images = list(dict.fromkeys(
                        image["image_url"]
                        for item in family["variants"]
                        for image in item.get("images") or []
                        if image.get("image_url")
                    ))
                    if enriched_variants:
                        with st.expander(
                            "Gevonden productgegevens en beschrijving",
                            expanded=True,
                        ):
                            representative = enriched_variants[0]
                            st.markdown(
                                safe_description(
                                    representative["html_description"]
                                ),
                                unsafe_allow_html=True,
                            )
                    if family_images:
                        with st.expander(
                            f"Alle gevonden productfoto's ({len(family_images)})"
                        ):
                            image_columns = st.columns(min(4, len(family_images)))
                            for index, image_url in enumerate(family_images):
                                with image_columns[index % len(image_columns)]:
                                    st.image(image_url, width="stretch")
                    if st.button(
                        "AI: Certilas-gegevens en foto's ophalen",
                        key=f"research_family_{family['family_key']}",
                        help=(
                            "Zoekt uitsluitend op certilas.com en slaat gegevens "
                            "alleen op voor varianten waarvan de EAN exact is gevonden."
                        ),
                    ):
                        with st.spinner("Certilas-productpagina onderzoeken…"):
                            result = research_certilas_family(
                                family["family_key"], supplier=supplier
                            )
                        st.success(
                            f"{result['updated']} product(en) verrijkt en "
                            f"{result['images']} foto('s) gevonden."
                        )
                        st.rerun()
                    if family["excluded"]:
                        with st.expander(
                            f"{len(family['excluded'])} uitgesloten regel(s)"
                        ):
                            st.dataframe(
                                pd.DataFrame([
                                    {
                                        "Omschrijving": item["source_description"],
                                        "SKU": item["sku"],
                                        "Reden": "Geen positieve bruto- en nettoprijs",
                                    }
                                    for item in family["excluded"]
                                ]),
                                width="stretch", hide_index=True,
                            )
        except Exception as exc:
            st.error(f"Productfamilievoorstel kon niet worden gemaakt: {exc}")


@st.fragment(run_every=10)
def show_valkenpower_breadcrumb_progress() -> None:
    status = official_breadcrumb_status()
    total = int(status.get("total") or 0)
    processed = int(status.get("processed") or 0)
    remaining = int(status.get("remaining") or 0)
    heartbeat_age = int(status.get("heartbeat_age_seconds") or 0)
    if status.get("stale"):
        st.error(
            "De officiële breadcrumbtaak lijkt vastgelopen: al "
            f"{heartbeat_age // 60} minuten geen voortgang. "
            "De watchdog hoort de worker automatisch te herstellen."
        )
    elif status.get("status") == "running":
        st.success(
            "Officiële breadcrumbtaak actief · "
            f"laatste heartbeat {heartbeat_age} seconden geleden"
        )
    else:
        st.info(f"Officiële breadcrumbtaak: {status.get('status', 'onbekend')}")
    st.progress(processed / total if total else 0.0)
    counters = st.columns(5)
    counters[0].metric("Verwerkt", f"{processed} / {total}")
    counters[1].metric("Resterend", remaining)
    counters[2].metric("Bijgewerkt", int(status.get("updated") or 0))
    counters[3].metric("Niet gevonden", int(status.get("not_found") or 0))
    counters[4].metric("Fouten", int(status.get("errors") or 0))
    st.caption(
        f"Laatste SKU: {status.get('last_sku') or 'nog niet begonnen'} · "
        f"Totaal officieel bevestigd: {status.get('confirmed_total', 0)}. "
        "Deze teller wordt iedere 10 seconden automatisch vernieuwd."
    )


with products_tab:
    st.markdown("#### Product collecties")
    complementary = complementary_stats(selected_slug)
    st.caption(
        f"{complementary['products']} producten met samen "
        f"{complementary['links']} relaties; "
        f"{complementary['locked']} bestaande of handmatige relaties vergrendeld. "
        f"Shopify accepteert maximaal {COMPLEMENTARY_MAX_LINKS} relaties per product."
    )
    collection_action_count = 4 if selected_slug == "valkenpower" else 1
    collection_actions = st.columns(collection_action_count)
    complementary_action = collection_actions[-1]
    if selected_slug == "valkenpower":
        show_valkenpower_breadcrumb_progress()
        if collection_actions[0].button(
            "Bekijk de collecties", key="view_valkenpower_collections"
        ):
            st.session_state["show_valkenpower_collections"] = True
        current_collection_job = collection_index_status() or {}
        collection_job_active = current_collection_job.get("status") in (
            COLLECTION_INDEX_ACTIVE
        )
        if collection_actions[1].button(
            "Beperkt opnieuw opbouwen",
            key="index_missing_valkenpower_collections",
            disabled=collection_job_active,
            help=(
                "Controleert op valkenpower.com alleen producten waaraan nog "
                "geen collectie is toegewezen."
            ),
        ):
            result = start_collection_index("missing")
            st.success(
                f"Beperkte achtergrondcontrole gestart voor {result['total']} producten."
            )
            st.rerun()
        with collection_actions[2].popover("Compleet opnieuw opbouwen"):
            st.warning(
                "Waarschuwing: alle Valkenpower-producten worden één voor één "
                "op de officiële website gecontroleerd. Dit kan heel lang duren."
            )
            confirm_complete_index = st.checkbox(
                "Ik begrijp dat dit lang kan duren",
                key="confirm_complete_valkenpower_index",
            )
            if st.button(
                "Volledige indexering starten",
                key="index_all_valkenpower_collections",
                disabled=collection_job_active or not confirm_complete_index,
                type="primary",
            ):
                result = start_collection_index("all")
                st.success(
                    f"Volledige achtergrondcontrole gestart voor {result['total']} producten."
                )
                st.rerun()
        if st.session_state.get("show_valkenpower_collections"):
            collection_rows = collection_counts()
            st.caption("Selecteer een collectie om de onderliggende producten te bekijken.")
            collection_table_event = st.dataframe(
                pd.DataFrame(collection_rows),
                width="stretch",
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="valkenpower_collection_overview",
                column_config={
                    "product_group": "Collectie",
                    "products": "Producten",
                },
            )
            selected_collection_rows = collection_table_event.selection.rows
            if selected_collection_rows:
                selected_collection = collection_rows[
                    selected_collection_rows[0]
                ]["product_group"]
                products_in_collection = collection_products(
                    selected_collection
                )
                st.markdown(f"##### {selected_collection}")
                st.caption(
                    f"{len(products_in_collection)} onderliggende producten"
                )
                st.dataframe(
                    pd.DataFrame(products_in_collection),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "sku": "SKU",
                        "source_title": "Productnaam",
                        "execution": "Uitvoering",
                        "category_full": "Categoriepad",
                    },
                )
        if current_collection_job:
            completed = int(current_collection_job.get("completed") or 0)
            total = int(current_collection_job.get("total") or 0)
            st.progress(completed / total if total else 0.0)
            st.caption(
                f"Collectie-index: {current_collection_job.get('status')} · "
                f"{completed}/{total} gecontroleerd · "
                f"{current_collection_job.get('changed', 0)} gewijzigd · "
                f"{current_collection_job.get('not_found', 0)} niet eenduidig · "
                f"{current_collection_job.get('failed', 0)} mislukt."
            )
    if complementary_action.button(
        "Aanvullende producten opbouwen",
        key=f"complementary_safe_sync_{selected_slug}",
        type="primary",
        help=(
            "Bouwt aanvullende producten op volgens het vastgelegde protocol: "
            "bestaande relaties eerst vergrendelen, aanvullingen genereren en "
            "het gecontroleerde eindresultaat naar Shopify sturen."
        ),
    ):
        result = synchronize_complementary_products(selected_slug)
        st.success(
            f"{result['uploaded']['updated']} Shopify-producten bijgewerkt; "
            f"{result['generated']['links']} relaties veilig gegenereerd."
        )
        st.rerun()
    complementary_plan = st.session_state.get(
        f"complementary_plan_{selected_slug}"
    )
    if complementary_plan:
        st.info(
            f"Voorstel: {complementary_plan['links']} relaties voor "
            f"{complementary_plan['eligible_products']} producten in "
            f"{complementary_plan['groups']} groepen; "
            f"{complementary_plan['groups_over_limit']} groepen vereisen een "
            "gebalanceerde verdeling."
        )
        group_details = complementary_plan.get("group_details") or []
        group_names = [
            str(item.get("product_group") or "Onbekende productgroep")
            for item in group_details
        ]
        if group_names:
            selected_group = st.selectbox(
                "Productgroep",
                group_names,
                key=f"complementary_group_{selected_slug}",
            )
            selected_group_detail = next(
                item for item in group_details
                if str(item.get("product_group") or "Onbekende productgroep")
                == selected_group
            )
            detail_columns = st.columns(3)
            detail_columns[0].metric(
                "Producten", selected_group_detail.get("products", 0)
            )
            detail_columns[1].metric(
                "Voorgestelde relaties",
                selected_group_detail.get("proposed_links", 0),
            )
            detail_columns[2].metric(
                "Verdeling nodig",
                (
                    "Ja"
                    if selected_group_detail.get("over_shopify_limit")
                    else "Nee"
                ),
            )
            examples = selected_group_detail.get("example_products")
            if examples:
                st.caption(f"Voorbeelden: {examples}")
            proposal_search = st.text_input(
                "Zoek product of SKU binnen deze groep",
                placeholder="Producttitel of SKU",
                key=f"complementary_proposal_search_{selected_slug}",
            ).strip().casefold()
            matching_proposals = [
                item for item in complementary_plan.get("proposals") or []
                if str(item.get("product_group") or "") == selected_group
                and (
                    not proposal_search
                    or proposal_search in " ".join((
                        str(item.get("source_sku") or ""),
                        str(item.get("source_title") or ""),
                        str(item.get("target_sku") or ""),
                        str(item.get("target_title") or ""),
                    )).casefold()
                )
            ]
            st.caption(
                f"{len(matching_proposals)} voorgestelde relaties. "
                "Maximaal 500 regels worden getoond."
            )
            st.dataframe(
                pd.DataFrame([{
                    "Van SKU": item.get("source_sku"),
                    "Van product": item.get("source_title"),
                    "Naar SKU": item.get("target_sku"),
                    "Naar product": item.get("target_title"),
                    "Positie": item.get("position"),
                    "Bestaand/vergrendeld": (
                        "Ja" if item.get("preserved") else "Nee"
                    ),
                    "Reden": item.get("reason"),
                    "   ": "",
                } for item in matching_proposals[:500]]),
                width="stretch",
                hide_index=True,
            )
    if selected_slug == "tecweld":
        with st.container(border=True):
            show_tecweld_translation_controls()
    products_search_col, products_limit_col = st.columns([4, 1])
    with products_search_col:
        products_search = st.text_input(
            "Zoek producten",
            placeholder="SKU of een deel van de productnaam",
            key=f"products_search_{selected_slug}",
            help="Zoekt op SKU, productnaam, AI-productnaam of EAN.",
        )
    with products_limit_col:
        products_limit_by_supplier = st.session_state.setdefault(
            "products_limit_by_supplier", {}
        )
        saved_products_limit = products_limit_by_supplier.get(
            selected_slug, 50
        )
        products_limit_options = [50, 100, 250]
        products_limit = st.selectbox(
            "Aantal producten",
            products_limit_options,
            index=products_limit_options.index(saved_products_limit),
            key=f"products_limit_{selected_slug}",
        )
        products_limit_by_supplier[selected_slug] = products_limit
    products = list_products(
        selected_slug,
        limit=products_limit,
        query=products_search,
    )
    if products_search.strip():
        st.caption(f"{len(products)} product(en) gevonden voor ‘{products_search.strip()}’.")
    if products:
        invoice_counts = invoice_evidence_counts(
            selected_slug, [str(product.get("sku") or "") for product in products]
        )
        products_display = [
            {
                **product,
                "invoice_count": invoice_counts.get(
                    str(product.get("sku") or "").casefold(), 0
                ),
                "display_title": (
                    (
                        product.get("ai_title")
                        if selected_slug == "tecweld" else None
                    )
                    or product.get("display_title")
                    or product.get("source_title")
                    or product.get("ai_title")
                    or product.get("source_description")
                    or ""
                ),
            }
            for product in products
        ]
        if len(products_display) == 1:
            products_display.append({
                key: "" for key in products_display[0]
            })
        products_table_event = st.dataframe(
            pd.DataFrame(products_display),
            width="stretch",
            height=913,
            row_height=35,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key=(
                f"products_table_{selected_slug}_"
                f"{st.session_state.get(f'products_table_revision_{selected_slug}', 0)}"
            ),
            column_order=[
                "sku", "invoice_count", "product_group_name", "display_title", "execution",
                "filter", "ean", "price", "sale_price", "stock_quantity",
                "purchase_unit", "sales_unit",
                "purchase_units_per_sales_unit", "unit_calculation_mode",
                "gross_purchase_price_per_kg",
                "purchase_discount_percent",
                "net_purchase_price_per_kg",
                "kg_per_purchase_unit", "kg_per_sales_unit", "cost_price",
                "available", "category", "source_present", "source_updated_at",
            ],
            column_config={
                "sku": "SKU",
                "invoice_count": st.column_config.NumberColumn(
                    "Facturen", help="Selecteer de productregel en open Inkoopfacturen."
                ),
                "display_title": "Productnaam",
                "product_group_name": "1. Productgroep - naam",
                "execution": "2. Uitvoering",
                "filter": "3. Filter",
                "purchase_unit": "Inkoopeenheid",
                "sales_unit": "Verkoopeenheid",
                "purchase_units_per_sales_unit": "Inkoop per verkoop",
                "unit_calculation_mode": "Rekenwijze",
                "gross_purchase_price_per_kg": "Bruto inkoop/kg",
                "purchase_discount_percent": "Korting %",
                "net_purchase_price_per_kg": "Netto inkoop/kg",
                "kg_per_purchase_unit": "Kg Ceweld Unit",
                "kg_per_sales_unit": "Kg Ceweld Bundle",
                "cost_price": "Inkoop per verkoopeenheid",
            },
        )
        selected_product_rows = products_table_event.selection.rows
        if selected_product_rows:
            selected_product = products_display[selected_product_rows[0]]
            selected_product_sku = str(selected_product.get("sku") or "")
            if selected_product_sku:
                selected_products_by_supplier = st.session_state.setdefault(
                    "selected_products_by_supplier", {}
                )
                selected_products_by_supplier[selected_slug] = (
                    selected_product_sku
                )
                show_supplier_product_details(
                    selected_slug, selected_product_sku
                )
    else:
        st.info("Nog geen producten geïmporteerd.")

with viewer_tab:
    st.markdown("#### Product vanaf leverancierswebsite toevoegen")
    st.info(
        f"Actieve leverancier: **{supplier['name']}**. Er wordt uitsluitend in "
        "de PIM en officiële bronnen van deze leverancier gezocht."
    )
    import_button, import_field, shopify_button = st.columns(
        [0.25, 0.5, 0.25], vertical_alignment="bottom"
    )
    with import_field:
        website_article_number = st.text_input(
            "Leveranciersartikelnummer",
            key=f"website_article_{selected_slug}",
            placeholder="Exact artikelnummer van de geselecteerde leverancier",
        )
    with import_button:
        import_website_product = st.button(
            (
                "Geselecteerd product verwerken"
                if selected_slug == "tecweld"
                else "Zoek, verrijk en sla op"
            ),
            key=f"website_import_{selected_slug}",
            type="primary",
            width="stretch",
            disabled=not website_article_number.strip(),
        )
    with shopify_button:
        save_website_product_to_shopify = st.button(
            "Sla op in Shopify",
            key=f"website_shopify_{selected_slug}",
            disabled=not website_article_number.strip(),
            width="stretch",
        )
    st.caption(
        f"Zoekt eerst exact via de eigen shoproute en daarna uitsluitend via "
        f"officiële websitedata van {supplier['name']}. "
        "Het resultaat wordt zonder prijs als concept opgeslagen en voor alle kanalen gemarkeerd."
    )
    st.caption(
        "‘Sla op in Shopify’ maakt of actualiseert een los product. "
        "Wil je deze SKU onder een bestaand product plaatsen, gebruik dan "
        "‘Voeg toe als variant’ hieronder en kies eerst een SKU uit het doelproduct."
    )
    if import_website_product:
        try:
            product_progress = st.progress(
                0,
                text="Stap 0 van 7 · Verwerking voorbereiden…",
            )

            def update_product_progress(
                step: int, total: int, message: str
            ) -> None:
                product_progress.progress(
                    min(100, int(step * 100 / max(total, 1))),
                    text=f"Stap {step} van {total} · {message}",
                )

            with st.spinner(
                f"{supplier['name']}: PIM, officiële productpagina, foto's en "
                "technische gegevens zoeken…"
            ):
                result = import_official_website_product(
                    selected_slug,
                    website_article_number,
                    progress_callback=update_product_progress,
                    execution_context="selected_product",
                )
            st.success(
                f"{supplier['name']}: product {result['sku']} is "
                f"{'aangemaakt' if result['created'] else 'bijgewerkt'} "
                f"als concept met {result['images']} foto('s)."
                + (
                    f" {result.get('feature_icons', 0)} iconen en "
                    f"{result.get('documents_translated', 0)}/"
                    f"{result.get('documents_found', 0)} documenten vertaald."
                    if selected_slug == "tecweld" else ""
                )
                + (
                    f" {result.get('enrichment_message')}"
                    if result.get("enrichment_skipped") else ""
                )
            )
            st.session_state[f"viewer_search_{selected_slug}"] = result["sku"]
            st.rerun()
        except Exception as exc:
            saved_product = get_supplier_product(
                selected_slug, website_article_number.strip()
            )
            if saved_product:
                st.success(
                    f"{supplier['name']}: product {saved_product['sku']} staat "
                    "opgeslagen in de PIM. De aanvullende verrijking gaf nog "
                    f"de melding: {exc}"
                )
                st.session_state[f"viewer_search_{selected_slug}"] = (
                    saved_product["sku"]
                )
            else:
                st.error(
                    f"{supplier['name']}: product kon niet worden toegevoegd: {exc}"
                )
    if save_website_product_to_shopify:
        try:
            with st.spinner("Product als concept in Shopify opslaan…"):
                result = upload_pim_product_draft(
                    selected_slug, website_article_number.strip()
                )
            st.success(
                f"Product {result['sku']} is als Shopify-concept opgeslagen."
            )
            st.markdown(f"[Open product in Shopify]({result['admin_url']})")
        except Exception as exc:
            st.error(f"Opslaan in Shopify mislukt: {exc}")

    with st.expander(
        "Voeg toe als variant van een bestaand Shopify-product",
        expanded=True,
    ):
        st.caption(
            "Het bestaande Shopify-product blijft ongewijzigd; alleen een nieuwe "
            "variant met de PIM-SKU, prijs en kostprijs wordt toegevoegd."
        )
        target_shopify_sku = st.text_input(
            "Bestaande Shopify-SKU binnen het doelproduct",
            key=f"variant_target_sku_{selected_slug}",
            placeholder="Bijvoorbeeld ZCQ-20-B2-AC-12-V",
        )
        inspect_variant_target = st.button(
            "Controleer doelproduct",
            key=f"inspect_variant_target_{selected_slug}",
            disabled=not target_shopify_sku.strip(),
        )
        target_state_key = f"variant_target_{selected_slug}"
        if inspect_variant_target:
            try:
                inspected_target = get_shopify_variant_target(target_shopify_sku)
                inspected_target["_lookup_sku"] = target_shopify_sku.strip().upper()
                st.session_state[target_state_key] = inspected_target
            except Exception as exc:
                st.session_state.pop(target_state_key, None)
                st.error(f"Doelproduct controleren mislukt: {exc}")
        variant_target = st.session_state.get(target_state_key)
        if (
            variant_target
            and variant_target.get("_lookup_sku")
            == target_shopify_sku.strip().upper()
        ):
            st.success(
                f"Doelproduct gevonden: {variant_target['title']} "
                f"({variant_target.get('vendor') or 'geen leverancier'})."
            )
            variant_option_values = {}
            current_option_values = (
                variant_target.get("_target_selected_options") or {}
            )
            if not current_option_values:
                current_variant = next(
                    (
                        row
                        for row in (variant_target.get("variants") or {}).get(
                            "nodes", []
                        )
                        if str(row.get("sku") or "").strip().upper()
                        == target_shopify_sku.strip().upper()
                    ),
                    {},
                )
                current_option_values = {
                    str(item.get("name") or ""): str(item.get("value") or "")
                    for item in current_variant.get("selectedOptions") or []
                    if item.get("name")
                }
            with st.form(
                f"add_shopify_variant_form_{selected_slug}",
                clear_on_submit=False,
            ):
                for option in variant_target.get("options") or []:
                    option_name = str(option["name"])
                    current_option_value = str(
                        current_option_values.get(option_name) or ""
                    )
                    variant_option_values[option_name] = st.text_input(
                        f"Nieuwe waarde voor {option_name}",
                        key=(
                            f"variant_option_{selected_slug}_"
                            f"{hashlib.sha256(option_name.encode()).hexdigest()[:10]}"
                        ),
                        placeholder=(
                            f"Leeg laten = {current_option_value} behouden"
                            if current_option_value else "Vul een waarde in"
                        ),
                    )
                    existing_values = [
                        value["name"]
                        for value in option.get("optionValues") or []
                    ]
                    st.caption(
                        "Bestaande waarden: " + " · ".join(existing_values)
                    )
                    if current_option_value:
                        st.caption(
                            f"Huidige waarde van de doel-SKU: "
                            f"{current_option_value}. Laat het veld leeg om deze "
                            "te behouden."
                        )
                submit_variant = st.form_submit_button(
                    "Voeg toe als Shopify-variant",
                    type="primary",
                    width="stretch",
                )
            if submit_variant:
                if not website_article_number.strip():
                    st.error(
                        "Vul bovenaan eerst het leveranciersartikelnummer in."
                    )
                else:
                    try:
                        with st.spinner(
                            "Variant veilig aan bestaand Shopify-product toevoegen…"
                        ):
                            result = add_pim_product_as_shopify_variant(
                                selected_slug,
                                website_article_number.strip(),
                                target_shopify_sku.strip(),
                                variant_option_values,
                            )
                        st.success(
                            f"SKU {result['sku']} is "
                            f"{'bijgewerkt' if result.get('updated') else 'toegevoegd'} "
                            f"als variant van {result['product_title']}."
                        )
                        st.markdown(
                            f"[Open product in Shopify]({result['admin_url']})"
                        )
                    except Exception as exc:
                        st.error(f"Variant toevoegen mislukt: {exc}")

    st.markdown("#### Productpagina bekijken")
    search_query = st.text_input(
        "Zoek op titel, SKU of EAN",
        key=f"viewer_search_{selected_slug}",
        placeholder="Bijvoorbeeld ratel, SP30865 of een EAN",
    )
    matches = search_supplier_products(selected_slug, search_query, 100)
    if matches:
        labels = {
            f"{row['source_title'] or 'Naamloos product'} · {row['sku']}": row["sku"]
            for row in matches
        }
        chosen_label = st.selectbox(
            "Product",
            list(labels),
            key=f"viewer_product_{selected_slug}",
        )
        product = get_supplier_product(selected_slug, labels[chosen_label])
    else:
        product = None
        st.info("Geen producten gevonden.")

    if product:
        title = product.get("ai_title") or product.get("source_title") or product["sku"]
        try:
            product_filters = json.loads(
                product.get("filter_values_json") or "[]"
            )
        except json.JSONDecodeError:
            product_filters = []
        images = product.get("images") or []
        display_price = (
            product.get("sale_price")
            if product.get("sale_price") is not None
            else product.get("price")
        )
        uses_gross_price = (
            product.get("sale_price") is None
            and product.get("price") is not None
        )
        st.markdown(
            f"<div class='ws-breadcrumb'>Home › {html.escape(supplier['name'])} › {html.escape(product.get('category') or 'Product')}</div>",
            unsafe_allow_html=True,
        )
        gallery, information = st.columns([1.12, 0.88], gap="large")
        with gallery:
            if images:
                st.image(images[0]["image_url"], width="stretch")
                if len(images) > 1:
                    thumbnail_columns = st.columns(min(5, len(images)))
                    for index, image in enumerate(images[:10]):
                        with thumbnail_columns[index % len(thumbnail_columns)]:
                            st.image(image["image_url"], width="stretch")
            else:
                st.info("Voor dit product is geen afbeelding beschikbaar.")

        with information:
            st.markdown(f"<div class='ws-brand'>{html.escape(product.get('brand') or supplier['name'])}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='ws-product-title'>{html.escape(title)}</div>", unsafe_allow_html=True)
            stock_class = "ws-stock-ok" if product.get("available") else "ws-stock-no"
            stock_text = "Op voorraad" if product.get("available") else "Niet op voorraad"
            if not product.get("source_present"):
                stock_text = "Niet meer in actuele leveranciersbron"
                stock_class = "ws-stock-no"
            st.markdown(f"<span class='{stock_class}'>{stock_text}</span>", unsafe_allow_html=True)
            price_column, details_column = st.columns(
                [5, 1], vertical_alignment="center"
            )
            with price_column:
                price_text = (
                    euro(display_price)
                    if display_price is not None
                    else "Verkoopprijs nog niet berekend"
                )
                st.markdown(
                    f"<div class='ws-price'>{price_text} "
                    "<small style='font-size:.42em;font-weight:500'>"
                    "excl. btw</small></div>",
                    unsafe_allow_html=True,
                )
                if uses_gross_price:
                    st.caption(
                        "Standaardverkoopprijs: brutoprijs (geen aparte "
                        "verkoopprijsregel toegepast)."
                    )
            with details_column:
                if st.button(
                    "👁",
                    key=(
                        f"purchase_price_details_{selected_slug}_"
                        f"{product['sku']}"
                    ),
                    help=(
                        "Bruto prijs, inkoopkorting en netto "
                        "inkoopprijs bekijken"
                    ),
                ):
                    show_purchase_price_details(product)
            st.markdown(
                "<div class='ws-meta'>"
                f"<div><b>SKU:</b> {html.escape(product['sku'])}</div>"
                f"<div><b>EAN:</b> {html.escape(product.get('ean') or '—')}</div>"
                f"<div><b>Leverancier:</b> {html.escape(supplier['name'])}</div>"
                f"<div><b>Categorie:</b> {html.escape(product.get('category_full') or product.get('category') or '—')}</div>"
                f"<div><b>Productgroep - naam:</b> {html.escape(product.get('product_group_name') or '—')}</div>"
                f"<div><b>Uitvoering:</b> {html.escape(product.get('execution') or '—')}</div>"
                f"<div><b>Filter:</b> {html.escape(' | '.join(product_filters) or '—')}</div>"
                f"<div><b>Inkoopeenheid:</b> {html.escape(product.get('purchase_unit') or 'stuk')}</div>"
                f"<div><b>Verkoopeenheid:</b> {html.escape(product.get('sales_unit') or 'stuk')}</div>"
                f"<div><b>Inkoop per verkoop:</b> {html.escape(str(product.get('purchase_units_per_sales_unit') or 1))}</div>"
                f"<div><b>Rekenwijze:</b> {html.escape(product.get('unit_calculation_mode') or 'multiply')}</div>"
                "</div>",
                unsafe_allow_html=True,
            )
            st.caption("Voorbeeldweergave voor Shopify — er wordt vanuit deze viewer niets gepubliceerd.")

        raw = product.get("raw_data") or {}
        website_import = raw.get("website_import") or {}
        feature_icons = website_import.get("feature_icons") or []
        description = product.get("html_description") or product.get("source_description") or ""
        st.markdown("<div class='ws-section'><h2>Productomschrijving</h2></div>", unsafe_allow_html=True)
        if description:
            st.markdown(safe_description(description), unsafe_allow_html=True)
        else:
            st.info("Nog geen productomschrijving beschikbaar.")

        if feature_icons:
            st.markdown(
                "<div class='ws-section'><h2>Functies en productkenmerken</h2></div>",
                unsafe_allow_html=True,
            )
            icon_columns = st.columns(min(7, len(feature_icons)))
            for index, item in enumerate(feature_icons):
                with icon_columns[index % len(icon_columns)]:
                    image_url = str(item.get("image_url") or "")
                    label = str(
                        item.get("label_nl") or item.get("source_label") or ""
                    ).strip()
                    if image_url.startswith("https://"):
                        st.image(image_url, width=60)
                    if label:
                        st.markdown(
                            f"<div style='text-align:center;font-size:.86rem'>"
                            f"{html.escape(label)}</div>",
                            unsafe_allow_html=True,
                        )

        detail_fields = {
            "Producttype": product.get("product_type"),
            "Categorie": product.get("category_full") or product.get("category"),
            "Productgroep - naam": product.get("product_group_name"),
            "Uitvoering": product.get("execution"),
            "Filter": " | ".join(product_filters),
            "Gewicht": f"{product['weight_grams']:g} g" if product.get("weight_grams") is not None else None,
            "Voorraadwaarde bron": product.get("stock_quantity"),
            "Laatst gewijzigd bij leverancier": product.get("source_updated_at"),
        }
        if raw.get("features"):
            detail_fields["Kenmerken bron"] = raw["features"]
        details = [
            {"Eigenschap": key, "Waarde": str(value)}
            for key, value in detail_fields.items()
            if value not in (None, "")
        ]
        if details:
            st.markdown("<div class='ws-section'><h2>Aanvullende informatie</h2></div>", unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(details), width="stretch", hide_index=True)
        if raw:
            excel_fields = [
                {
                    "Excel-veld": str(field),
                    "Waarde": (
                        "—"
                        if value is None
                        or str(value).strip().casefold() == "nan"
                        else json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (dict, list))
                        else str(value)
                    ),
                }
                for field, value in raw.items()
            ]
            st.markdown(
                "<div class='ws-section'><h2>Alle velden uit het "
                "Excel-bestand</h2></div>",
                unsafe_allow_html=True,
            )
            st.dataframe(
                pd.DataFrame(excel_fields), width="stretch", hide_index=True
            )

with shopify_tab:
    stats = supplier_stats(selected_slug)
    shopify_settings = get_shopify_settings()
    st.markdown("#### Shopify Admin API")
    with st.expander("Verbindingsinstellingen", expanded=not shopify_settings.get("has_token")):
        with st.form("shopify_settings"):
            shop_domain = st.text_input(
                "Shopify-shop",
                value=shopify_settings.get("shop_domain") or "",
                placeholder="winkelnaam.myshopify.com",
            )
            api_version = st.text_input(
                "Admin API-versie",
                value=shopify_settings.get("api_version") or DEFAULT_API_VERSION,
            )
            access_token = st.text_input(
                "Nieuw Admin API-token (leeg = huidige behouden)",
                type="password",
                placeholder="Veilig opgeslagen" if shopify_settings.get("has_token") else "",
                autocomplete="new-password",
            )
            webhook_secret = st.text_input(
                "Shopify app-clientgeheim voor webhooks (leeg = huidige behouden)",
                type="password",
                placeholder=(
                    "Veilig opgeslagen"
                    if shopify_settings.get("has_webhook_secret") else ""
                ),
                autocomplete="new-password",
                help=(
                    "Hiermee wordt gecontroleerd dat verkoop-, annulerings- en "
                    "retourberichten werkelijk door Shopify zijn verstuurd."
                ),
            )
            sync_enabled = st.checkbox(
                "Automatische synchronisatie toestaan",
                value=bool(shopify_settings.get("enabled")),
                help="Dit activeert nog geen nachtelijke taak; het legt alleen toestemming vast.",
            )
            save_shopify = st.form_submit_button("Shopify-instellingen opslaan")
        if save_shopify:
            save_shopify_settings(
                shop_domain=shop_domain,
                access_token=access_token if access_token else None,
                api_version=api_version,
                location_id=shopify_settings.get("location_id") or "",
                enabled=sync_enabled,
                webhook_secret=webhook_secret if webhook_secret else None,
            )
            st.success("Shopify-instellingen versleuteld opgeslagen.")
            st.rerun()

    if st.button("Shopify-verbinding testen"):
        with st.spinner("Shopify-verbinding controleren…"):
            result = test_shopify_connection()
        st.session_state["shopify_connection_result"] = result
    connection_result = st.session_state.get("shopify_connection_result")
    if connection_result:
        if connection_result["ok"]:
            st.success(connection_result["message"])
            locations = connection_result.get("locations") or []
            if locations:
                st.dataframe(pd.DataFrame(locations), width="stretch", hide_index=True)
        else:
            st.error(connection_result["message"])

    st.markdown("#### Deelverkoop-webhooks")
    st.caption(
        "Activeert directe voorraadverwerking voor verkopen, annuleringen en "
        "ontvangen retouren. Bestaande abonnementen worden niet dubbel aangemaakt."
    )
    if not shopify_settings.get("has_webhook_secret"):
        st.warning(
            "Nog niet gereed: open hierboven **Verbindingsinstellingen**, vul het "
            "**Shopify app-clientgeheim voor webhooks** in en sla de instellingen op."
        )
    if st.button(
        "Shopify-webhooks activeren",
        key="activate_derived_inventory_webhooks",
    ):
        if not shopify_settings.get("has_webhook_secret"):
            st.error(
                "De webhooks kunnen nog niet worden geactiveerd: het Shopify "
                "app-clientgeheim ontbreekt in Verbindingsinstellingen."
            )
        else:
            try:
                with st.spinner("Shopify-webhooks controleren en activeren…"):
                    webhook_result = ensure_derived_inventory_webhooks()
                st.success(
                    f"Deelverkoop-webhooks actief. "
                    f"{len(webhook_result['created'])} nieuw aangemaakt."
                )
            except Exception as exc:
                st.error(f"Shopify-webhooks konden niet worden geactiveerd: {exc}")

    st.markdown("#### Exacte SKU-matching")
    st.caption(
        "Deze controle leest Shopify uitsluitend uit. Er worden geen producten, "
        "prijzen of voorraden gewijzigd."
    )
    match_limit = st.number_input(
        "Aantal producten controleren",
        min_value=1,
        max_value=100,
        value=10,
        key=f"shopify_match_limit_{selected_slug}",
    )
    if st.button("SKU-matches analyseren", key=f"shopify_match_{selected_slug}"):
        try:
            with st.spinner("Exacte SKU-matches ophalen…"):
                matches = preview_supplier_matches(selected_slug, int(match_limit))
            st.session_state[f"shopify_matches_{selected_slug}"] = matches
        except Exception as exc:
            st.error(f"Shopify-analyse mislukt: {exc}")
    matches = st.session_state.get(f"shopify_matches_{selected_slug}")
    if matches:
        st.dataframe(pd.DataFrame(matches), width="stretch", hide_index=True)

    st.markdown("#### Shopify-formaat")
    st.write(
        "De export gebruikt de officiële Shopify CSV-kolommen. AI- of handmatig "
        "verbeterde inhoud krijgt voorrang op de ruwe leveranciersinhoud."
    )
    preview_max = max(int(stats["active"]), 1)
    preview_limit = st.number_input(
        "Aantal producten in preview",
        min_value=1,
        max_value=preview_max,
        value=min(10, preview_max),
        help=f"Maximaal {preview_max}: het actuele aantal producten van deze leverancier.",
    )
    preview_title_query = st.text_input(
        "Zoek in producttitel",
        placeholder="Bijvoorbeeld ratel, inbus of gereedschapswagen",
        key=f"shopify_preview_title_{selected_slug}",
        help="Zoekt op een deel van de bron- of AI-titel; hoofdletters maken niet uit.",
    )
    csv_bytes = shopify_csv_bytes(
        selected_slug,
        int(preview_limit),
        title_query=preview_title_query,
    )
    preview = pd.read_csv(io.BytesIO(csv_bytes), encoding="utf-8-sig")
    if preview.empty:
        st.info("Geen producttitels gevonden met deze zoektekst.")
    else:
        st.dataframe(preview, width="stretch", hide_index=True)
    st.download_button(
        "Shopify CSV downloaden",
        data=shopify_csv_bytes(selected_slug),
        file_name=f"{selected_slug}-shopify.csv",
        mime="text/csv",
        disabled=stats["active"] == 0,
    )
    if st.button("Exportbestand op server bewaren", disabled=stats["active"] == 0):
        path = save_shopify_export(selected_slug)
        st.success(f"Export opgeslagen: {path}")
