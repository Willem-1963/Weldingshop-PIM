from __future__ import annotations

import html
import re
from pathlib import Path

import streamlit as st

from app.product_families import add_product_to_stored_family
from app.bundles import (
    bundle_editor_options,
    bundle_stats,
    certilas_surcharge_bundle_stats,
    delete_bundle,
    get_bundle_for_edit,
    import_bundle_csv,
    import_bundle_csv_path,
    list_bundle_previews,
    list_certilas_surcharge_bundles,
    refresh_bundle_images,
    save_bundle_draft,
    set_all_bundle_selection,
    set_bundle_selection,
)
from app.shopify.derived_inventory import (
    backfill_piece_sales,
    build_derived_sku,
    delete_mapping,
    find_product_family,
    initialize_pool_from_shopify,
    list_family_product_options,
    list_mappings,
    list_piece_package_counts,
    product_name_for_sku,
    save_mapping,
    save_piece_package_count,
    suggested_mapping_values,
    synchronize_pool_to_shopify,
)


def euro(value: object) -> str:
    if value is None or value == "":
        return "Prijs niet beschikbaar"
    try:
        return f"€{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(value)


def show_derived_inventory_rules() -> None:
    st.markdown("## Deelverkoop uit gedeelde voorraad")
    st.caption(
        "Maak een verkoopvariant per gewicht of aantal. Iedere verkoop verlaagt "
        "de voorraad van de basis-SKU; annuleringen en ontvangen retouren boeken terug."
    )
    base_key = "derived_base_sku"
    quantity_key = "derived_sale_quantity"
    package_key = "derived_package_quantity"
    unit_key = "derived_unit_label"
    sku_key = "derived_sale_sku"
    editing_key = "derived_editing_sku"
    family_result_key = "derived_family_standard_result"
    pending_selection_key = "derived_pending_selection"
    pending_base_selection_key = "derived_pending_base_selection"

    st.markdown(
        """
        <style>
        [class~="st-key-new_derived_inventory"] button {
            background: #1565c0 !important; border-color: #1565c0 !important;
            color: white !important; font-weight: 750 !important;
        }
        [class*="st-key-add_family_membership_"] button {
            background: #1565c0 !important; border-color: #1565c0 !important;
            color: white !important; font-weight: 750 !important;
        }
        [class~="st-key-save_derived_inventory_mapping"] button,
        [class~="st-key-save_family_standard_rules"] button {
            background: #16803a !important; border-color: #16803a !important;
            color: white !important; font-weight: 800 !important;
        }
        [class~="st-key-sync_selected_derived"] button {
            background: #ed8b00 !important; border-color: #ed8b00 !important;
            color: white !important; font-weight: 750 !important;
        }
        [class~="st-key-delete_selected_derived"] button,
        [class*="st-key-delete_family_variants_"] button {
            background: #c62828 !important; border-color: #c62828 !important;
            color: white !important; font-weight: 750 !important;
        }
        [class*="st-key-delete_family_variants_"] button:disabled {
            background: #8f5656 !important; border-color: #8f5656 !important;
            color: #f5dddd !important; opacity: .72 !important;
        }
        [class*="st-key-bulk_delete_panel_"] details > summary {
            background: #c62828 !important;
            color: white !important;
            border: 1px solid #c62828 !important;
            font-weight: 800 !important;
            position: relative !important;
        }
        [class*="st-key-bulk_delete_panel_"] details > summary p {
            position: absolute !important;
            left: 50% !important;
            top: 50% !important;
            transform: translate(-50%, -50%) !important;
            width: max-content !important;
            max-width: calc(100% - 72px) !important;
            text-align: center !important;
            color: white !important;
            white-space: nowrap !important;
            margin: 0 !important;
            line-height: 1.2 !important;
        }
        [class*="st-key-bulk_delete_panel_"] details > summary svg {
            fill: white !important;
            color: white !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    def start_new_mapping() -> None:
        defaults = {
            base_key: "",
            quantity_key: 500.0,
            package_key: 5000.0,
            unit_key: "gram",
            sku_key: "",
            "derived_customer_notice": "",
            "derived_price_adjustment_type": "Geen opslag",
            "derived_price_adjustment_value": 0.0,
            editing_key: "",
            "derived_inventory_search": "",
            "derived_unified_search": "",
            "derived_product_query": "",
            "derived_product_search": "",
            family_result_key: None,
        }
        st.session_state.update(defaults)

    def edit_mapping(mapping: dict[str, object]) -> None:
        adjustment_labels = {
            "none": "Geen opslag",
            "fixed": "Vast bedrag",
            "percent": "Percentage",
        }
        loaded_sku = str(mapping["derived_sku"])
        if (
            str(mapping.get("unit_label") or "").casefold() == "kilogram"
            and loaded_sku.upper().endswith("G")
            and not loaded_sku.upper().endswith("KG")
        ):
            loaded_sku = build_derived_sku(
                str(mapping["base_sku"]),
                float(mapping["base_quantity"]),
                "kilogram",
            )
        st.session_state.update({
            base_key: mapping["base_sku"],
            quantity_key: float(mapping["base_quantity"]),
            package_key: float(mapping["base_package_quantity"]),
            unit_key: mapping.get("unit_label") or "stuk",
            sku_key: loaded_sku,
            "derived_customer_notice": mapping.get("customer_notice") or "",
            "derived_price_adjustment_type": adjustment_labels.get(
                str(mapping.get("price_adjustment_type") or "none"), "Geen opslag"
            ),
            "derived_price_adjustment_value": float(
                mapping.get("price_adjustment_value") or 0
            ),
            editing_key: mapping["derived_sku"],
            family_result_key: None,
        })

    def remove_mapping(derived_sku: str) -> None:
        removed_base_sku = ""
        for saved_mapping in list_mappings():
            if str(saved_mapping.get("derived_sku") or "") == derived_sku:
                removed_base_sku = str(saved_mapping.get("base_sku") or "").strip()
                break
        preserved_product_query = st.session_state.get(
            "derived_product_query", ""
        )
        delete_mapping(derived_sku)
        if st.session_state.get(editing_key) == derived_sku:
            start_new_mapping()
            st.session_state["derived_product_query"] = preserved_product_query
            if removed_base_sku:
                st.session_state[base_key] = removed_base_sku
                update_derived_defaults()

    def add_mapping_for_base(base_sku: str) -> None:
        start_new_mapping()
        st.session_state[base_key] = base_sku
        update_derived_defaults()

    def remove_selected_mapping(derived_sku: str) -> None:
        remove_mapping(derived_sku)
        st.session_state["derived_unified_search"] = ""

    def update_derived_defaults() -> None:
        base = st.session_state.get(base_key, "")
        if st.session_state.get(unit_key) == "stuk":
            counts = list_piece_package_counts()
            st.session_state[package_key] = float(next(
                (count for sku, count in counts.items() if sku.casefold() == base.casefold()),
                1,
            ))
        else:
            suggestion = suggested_mapping_values(base)
            package_quantity = float(suggestion["base_package_quantity"])
            if st.session_state.get(unit_key) == "kilogram":
                package_quantity /= 1000
            st.session_state[package_key] = package_quantity
        st.session_state[sku_key] = build_derived_sku(
            base,
            st.session_state.get(quantity_key, 500),
            st.session_state.get(unit_key, "gram"),
        )

    def update_derived_sku() -> None:
        st.session_state[sku_key] = build_derived_sku(
            st.session_state.get(base_key, ""),
            st.session_state.get(quantity_key, 500),
            st.session_state.get(unit_key, "gram"),
        )

    def update_derived_unit() -> None:
        update_derived_defaults()

    def select_unified_result() -> None:
        selected = str(
            st.session_state.get("derived_unified_search") or ""
        )
        if selected.startswith("regel::"):
            st.session_state[pending_selection_key] = selected.split("::", 1)[1]
        elif selected.startswith("product::"):
            st.session_state[pending_base_selection_key] = selected.split("::", 1)[1]

    mappings = list_mappings()
    show_all_value = "__show_all_derived_inventory__"
    mapping_by_sku = {str(mapping["derived_sku"]): mapping for mapping in mappings}
    pending_selection = st.session_state.pop(pending_selection_key, None)
    if pending_selection in mapping_by_sku:
        st.session_state["derived_unified_search"] = f"regel::{pending_selection}"
    pending_base_selection = st.session_state.pop(
        pending_base_selection_key, None
    )
    if pending_base_selection:
        start_new_mapping()
        st.session_state[base_key] = str(pending_base_selection)
        st.session_state["derived_unified_search"] = (
            f"product::{pending_base_selection}"
        )
        update_derived_defaults()
    mapping_labels = {
        sku: f"{sku} — {mapping['base_sku']} — "
        f"{product_name_for_sku(mapping['base_sku']) or 'naam onbekend'}"
        for sku, mapping in mapping_by_sku.items()
    }
    family_products = list_family_product_options()
    family_product_by_sku = {item["sku"]: item for item in family_products}
    unified_options = [
        show_all_value,
        "",
        *(f"regel::{sku}" for sku in mapping_by_sku),
        *(f"product::{sku}" for sku in family_product_by_sku),
    ]

    def unified_label(value: str) -> str:
        if value == show_all_value:
            return "Alle opgeslagen deelverkoopregels tonen"
        if not value:
            return "Zoek op productfamilie, materiaal, maat, verpakking of SKU…"
        kind, sku = value.split("::", 1)
        if kind == "regel":
            return "Opgeslagen regel — " + mapping_labels[sku]
        item = family_product_by_sku[sku]
        return (
            f"PIM-product — {item['family_title']} — "
            f"{item['variant_title']} — {sku}"
        )

    toolbar = st.columns([3, 1], vertical_alignment="bottom")
    selected_result = toolbar[0].selectbox(
        "Zoek en selecteer",
        unified_options,
        format_func=unified_label,
        key="derived_unified_search",
        on_change=select_unified_result,
    )
    selected_rule_sku = ""
    if selected_result.startswith("regel::"):
        selected_rule_sku = selected_result.split("::", 1)[1]
    elif selected_result == show_all_value:
        selected_rule_sku = show_all_value
    toolbar[1].button(
        "＋ Nieuwe regel", on_click=start_new_mapping,
        use_container_width=True, type="secondary", key="new_derived_inventory",
    )
    if (
        selected_rule_sku
        and selected_rule_sku != show_all_value
        and selected_rule_sku != st.session_state.get(editing_key)
    ):
        family_result = st.session_state.get(family_result_key)
        edit_mapping(mapping_by_sku[selected_rule_sku])
        if family_result:
            st.session_state[family_result_key] = family_result
        st.rerun()

    if selected_rule_sku == show_all_value:
        with st.container(border=True):
            st.markdown("### Alle voorraad-deelregels")
            st.caption(f"{len(mappings)} regels gevonden")
            if mappings:
                overview_piece_counts = list_piece_package_counts()
                st.dataframe(
                    [
                        {
                            "Verkoop-SKU": mapping["derived_sku"],
                            "Basis-SKU": mapping["base_sku"],
                            "Productnaam": product_name_for_sku(mapping["base_sku"]) or "—",
                            "Inhoud verkoopvariant": mapping["base_quantity"],
                            "Inhoud basisverpakking": mapping["base_package_quantity"],
                            "Eenheid": mapping["unit_label"],
                            "Stuks per verpakking": overview_piece_counts.get(
                                mapping["base_sku"]
                            ),
                            "Tekst bij variant": mapping.get("customer_notice") or "",
                            "Prijsaanpassing": mapping.get("price_adjustment_type") or "none",
                            "Waarde prijsaanpassing": mapping.get("price_adjustment_value") or 0,
                            "Actief": bool(mapping.get("enabled")),
                            "Aangemaakt": mapping.get("created_at") or "",
                            "Bijgewerkt": mapping.get("updated_at") or "",
                            "\u00a0\u00a0\u00a0": "   ",
                        }
                        for mapping in mappings
                    ],
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "\u00a0\u00a0\u00a0": st.column_config.TextColumn(
                            "\u00a0\u00a0\u00a0", width="small"
                        ),
                    },
                )
            else:
                st.info("Er zijn nog geen voorraad-deelregels vastgelegd.")

    # Binnen een productfamilie werken we in één compact blok. Het algemene
    # formulier en de familietabel mogen niet boven en onder elkaar staan.
    compact_family = find_product_family(
        str(st.session_state.get(base_key) or "")
    )
    if compact_family:
        family_sku_keys = {
            str(value).casefold() for value in compact_family["skus"]
        }
        family_mappings = [
            mapping for mapping in mappings
            if str(mapping.get("base_sku") or "").casefold() in family_sku_keys
        ]
        compact_options = [
            *(f"base::{sku}" for sku in compact_family["skus"]),
            *(f"rule::{mapping['derived_sku']}" for mapping in family_mappings),
        ]
        compact_mapping_by_sku = {
            str(mapping["derived_sku"]): mapping for mapping in family_mappings
        }
        family_values = {
            item["sku"]: item["value"]
            for item in compact_family.get("variants") or []
        }

        def compact_option_label(value: str) -> str:
            kind, sku = value.split("::", 1)
            if kind == "base":
                return (
                    f"{sku} — hoofdproduct — "
                    f"{family_values.get(sku) or 'variant'}"
                )
            mapping = compact_mapping_by_sku[sku]
            return (
                f"{mapping['base_sku']} — deelvariant {sku} — "
                f"{float(mapping['base_quantity']):g} "
                f"{mapping.get('unit_label') or ''}"
            )

        current_base = str(st.session_state.get(base_key) or "")
        current_editing = str(st.session_state.get(editing_key) or "")
        preferred_option = (
            f"rule::{current_editing}"
            if current_editing in compact_mapping_by_sku
            else f"base::{current_base}"
        )
        preferred_index = (
            compact_options.index(preferred_option)
            if preferred_option in compact_options else 0
        )
        with st.container(border=True):
            st.markdown(f"### {html.escape(compact_family['title'])}")
            compact_message = st.session_state.pop(
                "compact_family_message", ""
            )
            if compact_message:
                st.success(compact_message)
            compact_choice = st.selectbox(
                "Kies een hoofdproduct om toe te voegen of een variant om te wijzigen",
                compact_options,
                index=preferred_index,
                format_func=compact_option_label,
                key=f"compact_family_choice_{compact_family['family_key']}",
            )
            compact_kind, compact_sku = compact_choice.split("::", 1)
            compact_mapping = (
                compact_mapping_by_sku.get(compact_sku)
                if compact_kind == "rule" else None
            )
            compact_base_sku = str(
                compact_mapping["base_sku"] if compact_mapping else compact_sku
            )
            widget_suffix = re.sub(r"[^a-zA-Z0-9]+", "_", compact_choice)
            default_unit = str(
                (compact_mapping or {}).get("unit_label") or "stuk"
            )
            compact_columns = st.columns([1, 1, 1])
            compact_quantity = compact_columns[0].number_input(
                "Aantal of gewicht",
                min_value=0.001,
                value=float(
                    (compact_mapping or {}).get("base_quantity") or 10
                ),
                step=0.1 if default_unit == "kilogram" else 1.0,
                key=f"compact_quantity_{widget_suffix}",
            )
            compact_unit = compact_columns[1].selectbox(
                "Eenheid",
                ["stuk", "kilogram", "gram", "lengte"],
                index=["stuk", "kilogram", "gram", "lengte"].index(default_unit),
                key=f"compact_unit_{widget_suffix}",
            )
            confirmed_counts = list_piece_package_counts()
            suggested_package = suggested_mapping_values(compact_base_sku)
            default_package = float(
                (compact_mapping or {}).get("base_package_quantity")
                or confirmed_counts.get(compact_base_sku)
                or suggested_package["base_package_quantity"]
                or 1
            )
            compact_package = compact_columns[2].number_input(
                "Inhoud volledige verpakking",
                min_value=0.001,
                value=default_package,
                step=0.1 if compact_unit == "kilogram" else 1.0,
                key=f"compact_package_{widget_suffix}",
            )
            generated_compact_sku = build_derived_sku(
                compact_base_sku, compact_quantity, compact_unit
            )
            compact_sale_sku = st.text_input(
                "SKU deelvariant",
                value=(
                    str(compact_mapping["derived_sku"])
                    if compact_mapping else generated_compact_sku
                ),
                key=f"compact_sku_{widget_suffix}",
            )
            compact_notice = st.text_input(
                "Tekst bij deze variant (optioneel)",
                value=str((compact_mapping or {}).get("customer_notice") or ""),
                key=f"compact_notice_{widget_suffix}",
            )
            compact_actions = st.columns([1.5, 1, 2])
            compact_save = compact_actions[0].button(
                "Variant wijzigen" if compact_mapping else "Variant toevoegen",
                type="primary", use_container_width=True,
                key=f"compact_save_{widget_suffix}",
            )
            compact_delete = compact_actions[1].button(
                "Variant verwijderen",
                disabled=not bool(compact_mapping),
                use_container_width=True,
                key=f"compact_delete_{widget_suffix}",
            )
            compact_actions[2].caption(
                "Alles voor deze familie staat in dit ene blok."
            )
            if compact_save:
                try:
                    if compact_unit == "stuk":
                        save_piece_package_count(
                            compact_base_sku, compact_package
                        )
                    save_mapping(
                        compact_sale_sku, compact_base_sku, compact_quantity,
                        base_package_quantity=compact_package,
                        unit_label=compact_unit,
                        customer_notice=compact_notice,
                        price_adjustment_type=str(
                            (compact_mapping or {}).get(
                                "price_adjustment_type", "none"
                            )
                        ),
                        price_adjustment_value=float(
                            (compact_mapping or {}).get(
                                "price_adjustment_value", 0
                            )
                        ),
                        replace_existing=bool(compact_mapping),
                    )
                    if (
                        compact_mapping
                        and compact_sku.casefold() != compact_sale_sku.casefold()
                    ):
                        delete_mapping(compact_sku)
                    st.session_state["compact_family_message"] = (
                        f"Deelvariant {compact_sale_sku} is opgeslagen."
                    )
                    st.session_state[editing_key] = ""
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
            if compact_delete and compact_mapping:
                delete_mapping(compact_sku)
                st.session_state["compact_family_message"] = (
                    f"Deelvariant {compact_sku} is verwijderd."
                )
                st.session_state[editing_key] = ""
                st.rerun()

            st.markdown("#### Overzicht")
            st.dataframe(
                [
                    {
                        "Type": "Hoofdproduct",
                        "SKU": sku,
                        "Uitvoering": family_values.get(sku) or "—",
                    }
                    for sku in compact_family["skus"]
                ] + [
                    {
                        "Type": "Deelvariant",
                        "SKU": mapping["derived_sku"],
                        "Uitvoering": (
                            f"{float(mapping['base_quantity']):g} "
                            f"{mapping.get('unit_label') or ''} van "
                            f"{mapping['base_sku']}"
                        ),
                    }
                    for mapping in family_mappings
                ],
                hide_index=True,
                use_container_width=True,
            )
        return

    editing_sku = st.session_state.get(editing_key, "")
    editing_base_sku = st.session_state.get(base_key, "") if editing_sku else ""
    editing_product_name = product_name_for_sku(editing_base_sku)
    st.markdown(
        (
            f"### Regel wijzigen: `{editing_sku}`"
            + (f" — {html.escape(editing_product_name)}" if editing_product_name else "")
        )
        if editing_sku else "### Nieuwe regel"
    )
    columns = st.columns([1.2, 1.2, 0.8, 0.8, 0.8])
    base_sku = columns[0].text_input(
        "Basis-SKU", placeholder="12510L", key=base_key,
        on_change=update_derived_defaults,
    )
    base_quantity = columns[1].number_input(
        "Inhoud verkoopvariant", min_value=0.001, value=500.0,
        step=0.1 if st.session_state.get(unit_key) == "kilogram" else 1.0,
        help=(
            "Vul gram als gram in (2600), kilogram als kilogram (2,6), "
            "lengte als millimeters en stuks als aantallen."
        ),
        key=quantity_key, on_change=update_derived_sku,
    )
    base_package_quantity = columns[2].number_input(
        (
            "Aantal stuks per basisverpakking"
            if st.session_state.get(unit_key) == "stuk"
            else "Inhoud basisverpakking"
        ),
        min_value=0.001, value=5000.0,
        step=0.1 if st.session_state.get(unit_key) == "kilogram" else 1.0,
        help=(
            "Vul bij stuks uitsluitend het werkelijk getelde aantal in. "
            "Bij een basisverpakking van 5 kg vul je voor gewicht 5000 gram in."
        ),
        key=package_key,
    )
    unit_label = columns[3].selectbox(
        "Verkoopeenheid", ["gram", "kilogram", "stuk", "lengte"],
        key=unit_key, on_change=update_derived_unit,
        help=(
            "Voor verkoop in stuks is een bevestigd aantal stuks per "
            "basisverpakking verplicht."
        ),
    )
    derived_sku = columns[4].text_input(
        "SKU verkoopvariant", placeholder="12510L-500G", key=sku_key,
        help="Wordt automatisch opgebouwd en kan daarna handmatig worden aangepast.",
    )
    customer_notice = st.text_input(
        "Tekst bij deze variant",
        placeholder="Let op: knipmaat wordt 500 mm",
        key="derived_customer_notice",
    )
    price_columns = st.columns([1, 1, 3])
    price_adjustment_label = price_columns[0].selectbox(
        "Extra op verkoopprijs",
        ["Geen opslag", "Vast bedrag", "Percentage"],
        key="derived_price_adjustment_type",
    )
    price_adjustment_value = price_columns[1].number_input(
        "Bedrag (€) of percentage (%)",
        min_value=0.0,
        value=0.0,
        step=0.5,
        disabled=price_adjustment_label == "Geen opslag",
        key="derived_price_adjustment_value",
    )
    price_columns[2].caption(
        "De opslag komt boven op de evenredige prijs van de deelverpakking. "
        "Voorbeeld: basisprijs €100, deel 20% = €20; met 10% opslag wordt €22."
    )
    action_columns = st.columns([1.5, 1, 1])
    submitted = action_columns[0].button(
        "Wijzigingen opslaan" if editing_sku else "Nieuwe regel opslaan", type="primary",
        key="save_derived_inventory_mapping", use_container_width=True,
    )
    if editing_sku and action_columns[1].button(
        "↻ Voorraad bijwerken",
        key="sync_selected_derived",
        use_container_width=True,
    ):
        try:
            initialize_pool_from_shopify(base_sku)
            desired = synchronize_pool_to_shopify(base_sku)
            st.success(
                "Voorraad bijgewerkt: "
                + ", ".join(f"{sku}: {qty}" for sku, qty in desired.items())
            )
        except Exception as exc:
            st.error(str(exc))
    if editing_sku:
        action_columns[2].button(
            "🗑 Regel verwijderen",
            key="delete_selected_derived",
            on_click=remove_mapping,
            args=(editing_sku,),
            use_container_width=True,
        )
    family_action_columns = st.columns([1.5, 1, 1])
    bulk_action_placeholder = family_action_columns[0].empty()
    standard_action_placeholder = family_action_columns[1].empty()
    if submitted:
        try:
            sku_to_save = derived_sku
            original_mapping = mapping_by_sku.get(editing_sku)
            editing_contents_changed = bool(
                original_mapping
                and (
                    float(original_mapping.get("base_quantity") or 0)
                    != float(base_quantity)
                    or str(original_mapping.get("unit_label") or "").casefold()
                    != unit_label.casefold()
                )
            )
            if editing_contents_changed:
                sku_to_save = build_derived_sku(
                    base_sku, base_quantity, unit_label
                )
            if not editing_sku:
                generated_sku = build_derived_sku(
                    base_sku, base_quantity, unit_label
                )
                existing_candidate = mapping_by_sku.get(sku_to_save)
                if existing_candidate and (
                    float(existing_candidate.get("base_quantity") or 0)
                    != float(base_quantity)
                    or str(existing_candidate.get("unit_label") or "").casefold()
                    != unit_label.casefold()
                ):
                    sku_to_save = generated_sku
            if unit_label == "stuk":
                save_piece_package_count(base_sku, base_package_quantity)
            save_mapping(
                sku_to_save, base_sku, base_quantity,
                base_package_quantity=base_package_quantity,
                unit_label=unit_label, customer_notice=customer_notice,
                price_adjustment_type={
                    "Geen opslag": "none",
                    "Vast bedrag": "fixed",
                    "Percentage": "percent",
                }[price_adjustment_label],
                price_adjustment_value=price_adjustment_value,
                replace_existing=bool(editing_sku)
                and not editing_contents_changed,
            )
            if (
                editing_sku
                and not editing_contents_changed
                and editing_sku.casefold() != sku_to_save.casefold()
            ):
                delete_mapping(editing_sku)
            st.session_state[editing_key] = sku_to_save
            st.session_state[pending_selection_key] = sku_to_save
            st.success(f"Deelverkoopregel {sku_to_save} is opgeslagen.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    selected_family = find_product_family(base_sku)
    if selected_family:
        st.divider()
        mappings_by_base: dict[str, list[dict]] = {}
        for saved_mapping in mappings:
            mappings_by_base.setdefault(
                str(saved_mapping["base_sku"]).casefold(), []
            ).append(saved_mapping)
        with st.container(border=True):
            st.markdown(f"#### {html.escape(selected_family['title'])}")
            st.caption(
                f"{len(selected_family['skus'])} producten in deze familie"
            )
            membership_result = st.session_state.pop(
                "manual_family_membership_result", None
            )
            if membership_result:
                st.success(
                    f"SKU {membership_result['sku']} is aan deze familie toegevoegd: "
                    f"{membership_result['variant_title']}"
                )
            membership_columns = st.columns(
                [3, 1], vertical_alignment="bottom"
            )
            membership_sku = membership_columns[0].text_input(
                "Los PIM-product aan deze familie toevoegen",
                placeholder="Vul bijvoorbeeld 10382 in",
                key=f"family_membership_sku_{selected_family['family_key']}",
                help=(
                    "De koppeling wordt blijvend in de PIM opgeslagen en blijft "
                    "behouden bij volgende familie-opbouwen."
                ),
            )
            if membership_columns[1].button(
                "＋ Aan familie toevoegen",
                key=f"add_family_membership_{selected_family['family_key']}",
                use_container_width=True,
            ):
                try:
                    result = add_product_to_stored_family(
                        selected_family["family_key"], membership_sku
                    )
                    st.session_state["manual_family_membership_result"] = result
                    st.session_state[pending_base_selection_key] = result["sku"]
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
            family_result = st.session_state.pop(family_result_key, None)
            if family_result:
                st.success(
                    f"Hele familie bijgewerkt: {family_result['saved']} "
                    f"standaardregels opgeslagen en {family_result['removed']} "
                    "oude regels voor dezelfde inhoud verwijderd."
                )
                if family_result["removed_skus"]:
                    st.caption(
                        "Verwijderd: " + ", ".join(family_result["removed_skus"])
                    )
            bulk_result = st.session_state.pop("family_bulk_delete_result", None)
            if bulk_result:
                st.success(
                    f"{bulk_result['count']} deelverkoopregels verwijderd: "
                    + ", ".join(bulk_result["skus"])
                )
            standard_quantity = base_quantity
            standard_unit = unit_label
            confirmed_piece_counts = list_piece_package_counts()
            family_values = {
                item["sku"]: item["value"]
                for item in selected_family.get("variants") or []
            }
            family_rows = []
            for family_sku in selected_family["skus"]:
                saved_rows = mappings_by_base.get(family_sku.casefold(), [])
                for mapping in saved_rows:
                    adjustment_type = str(
                        mapping.get("price_adjustment_type") or "none"
                    )
                    family_rows.append({
                        "Basis-SKU": family_sku,
                        "Beschikbare waarde": family_values.get(family_sku) or "—",
                        "Productnaam": product_name_for_sku(family_sku) or "—",
                        "Inhoud verkoopvariant": f"{mapping['base_quantity']:g}",
                        "Verkoopeenheid": mapping.get("unit_label") or "—",
                        "Opslag": {
                            "none": "Geen opslag",
                            "fixed": "Vast bedrag",
                            "percent": "Percentage",
                        }.get(adjustment_type, adjustment_type),
                        "Bedrag / percentage": (
                            f"€ {float(mapping.get('price_adjustment_value') or 0):.2f}"
                            if adjustment_type == "fixed"
                            else f"{float(mapping.get('price_adjustment_value') or 0):g}%"
                            if adjustment_type == "percent"
                            else "—"
                        ),
                        "Tekst bij deze variant": (
                            mapping.get("customer_notice") or "—"
                        ),
                        "Verkoop-SKU": mapping["derived_sku"],
                        "\u00a0\u00a0\u00a0": "   ",
                    })
                if not saved_rows:
                    family_rows.append({
                        "Basis-SKU": family_sku,
                        "Beschikbare waarde": family_values.get(family_sku) or "—",
                        "Productnaam": product_name_for_sku(family_sku) or "—",
                        "Inhoud verkoopvariant": "—",
                        "Verkoopeenheid": "—",
                        "Opslag": "—",
                        "Bedrag / percentage": "—",
                        "Tekst bij deze variant": "—",
                        "Verkoop-SKU": "Nog niet ingesteld",
                        "\u00a0\u00a0\u00a0": "   ",
                    })
            st.markdown("##### Regel binnen deze familie kiezen")
            family_choice_options = list(range(len(family_rows)))

            def family_choice_label(row_index: int) -> str:
                row = family_rows[row_index]
                sale_sku = str(row.get("Verkoop-SKU") or "")
                if sale_sku == "Nog niet ingesteld":
                    return (
                        f"{row['Basis-SKU']} — hoofdproduct — "
                        f"{row['Beschikbare waarde']}"
                    )
                return (
                    f"{row['Basis-SKU']} — deelvariant {sale_sku} — "
                    f"{row['Inhoud verkoopvariant']} {row['Verkoopeenheid']}"
                )

            selected_family_row_index = st.selectbox(
                "Kies het hoofdproduct of de deelvariant die je wilt aanpassen",
                family_choice_options,
                format_func=family_choice_label,
                key=f"family_rule_choice_{selected_family['family_key']}",
            )
            selected_row = family_rows[selected_family_row_index]
            selected_base_sku = str(selected_row.get("Basis-SKU") or "").strip()
            selected_variant_sku = str(selected_row.get("Verkoop-SKU") or "")
            selected_mapping = mapping_by_sku.get(selected_variant_sku)
            row_actions = st.columns([1, 1, 1])
            row_actions[0].button(
                f"＋ Nieuwe variant voor {selected_base_sku}",
                key=(
                    "add_selected_family_row_"
                    f"{selected_family['family_key']}_{selected_family_row_index}"
                ),
                use_container_width=True,
                type="primary",
                on_click=add_mapping_for_base,
                args=(selected_base_sku,),
            )
            if selected_mapping:
                row_actions[1].button(
                    "✎ Geselecteerde variant wijzigen",
                    key=(
                        "edit_selected_family_row_"
                        f"{selected_family['family_key']}_{selected_family_row_index}"
                    ),
                    use_container_width=True,
                    on_click=edit_mapping,
                    args=(selected_mapping,),
                )
                row_actions[2].button(
                    "🗑 Geselecteerde variant verwijderen",
                    key=(
                        "delete_selected_family_row_"
                        f"{selected_family['family_key']}_{selected_family_row_index}"
                    ),
                    use_container_width=True,
                    on_click=remove_selected_mapping,
                    args=(selected_variant_sku,),
                )
            else:
                row_actions[1].caption(
                    "Voor dit hoofdproduct is nog geen deelvariant ingesteld."
                )
            st.caption("Overzicht van de hoofdproducten en opgeslagen deelvarianten")
            st.dataframe(
                family_rows,
                hide_index=True,
                use_container_width=True,
                key=f"family_variant_table_{selected_family['family_key']}",
                column_config={
                    "\u00a0\u00a0\u00a0": st.column_config.TextColumn(
                        "\u00a0\u00a0\u00a0", width="small"
                    ),
                },
            )
            family_sku_keys = {
                family_sku.casefold() for family_sku in selected_family["skus"]
            }
            bulk_targets = [
                mapping for mapping in mappings
                if str(mapping.get("base_sku") or "").casefold() in family_sku_keys
                and float(mapping.get("base_quantity") or 0)
                == float(standard_quantity)
                and str(mapping.get("unit_label") or "").casefold()
                == standard_unit.casefold()
            ]
            if bulk_targets:
                quantity_label = (
                    f"{standard_quantity:g} {standard_unit}"
                )
                panel_key = (
                    "bulk_delete_panel_"
                    f"{selected_family['family_key']}_"
                    f"{standard_quantity:g}_{standard_unit}"
                )
                with bulk_action_placeholder.container(key=panel_key):
                    with st.expander(
                        f"Bulk verwijderen: alle {quantity_label}-varianten"
                    ):
                        target_skus = [
                            str(mapping["derived_sku"]) for mapping in bulk_targets
                        ]
                        st.warning(
                            "Deze deelverkoopregels worden verwijderd: "
                            + ", ".join(target_skus)
                        )
                        confirmed_bulk_delete = st.checkbox(
                            "Ik bevestig dat uitsluitend deze deelverkoopregels "
                            "mogen worden verwijderd.",
                            key=(
                                "confirm_family_bulk_delete_"
                                f"{selected_family['family_key']}_"
                                f"{standard_quantity:g}_{standard_unit}"
                            ),
                        )
                        if st.button(
                            f"Alle {quantity_label}-varianten verwijderen",
                            key=(
                                "delete_family_variants_"
                                f"{selected_family['family_key']}_"
                                f"{standard_quantity:g}_{standard_unit}"
                            ),
                            disabled=not confirmed_bulk_delete,
                            type="primary",
                            use_container_width=True,
                        ):
                            for target_sku in target_skus:
                                delete_mapping(target_sku)
                            remaining = [
                                mapping for mapping in mappings
                                if str(mapping.get("derived_sku")) not in target_skus
                                and str(mapping.get("base_sku") or "").casefold()
                                in family_sku_keys
                            ]
                            st.session_state["family_bulk_delete_result"] = {
                                "count": len(target_skus), "skus": target_skus,
                            }
                            st.session_state[pending_selection_key] = (
                                str(remaining[0]["derived_sku"])
                                if remaining else ""
                            )
                            st.rerun()
            edited_family_rows = [
                {
                    "Basis-SKU": family_sku,
                    "Nieuwe standaard-SKU": build_derived_sku(
                        family_sku, standard_quantity, standard_unit
                    ),
                    "Stuks per verpakking": confirmed_piece_counts.get(family_sku),
                }
                for family_sku in selected_family["skus"]
            ]
            if standard_unit == "stuk":
                edited_family_rows = st.data_editor(
                    edited_family_rows,
                    hide_index=True,
                    use_container_width=True,
                    disabled=["Basis-SKU", "Nieuwe standaard-SKU"],
                    column_config={
                        "Stuks per verpakking": st.column_config.NumberColumn(
                            "Stuks per verpakking", min_value=1, step=1,
                            help="Werkelijk aantal stuks in één volledige basisverpakking.",
                        ),
                    },
                    key=f"piece_count_editor_{selected_family['family_key']}",
                )
            with standard_action_placeholder.container():
                save_family_standard = st.button(
                    "Standaardregels voor hele familie opslaan",
                    key="save_family_standard_rules",
                    type="primary",
                    use_container_width=True,
                )
            if save_family_standard:
                try:
                    standard_skus_by_base = {
                        row["Basis-SKU"].casefold(): row["Nieuwe standaard-SKU"]
                        for row in edited_family_rows
                    }
                    if standard_unit == "stuk":
                        missing_counts = [
                            row["Basis-SKU"] for row in edited_family_rows
                            if not row.get("Stuks per verpakking")
                        ]
                        if missing_counts:
                            raise ValueError(
                                "Vul eerst stuks per verpakking in voor: "
                                + ", ".join(missing_counts)
                            )
                    for row in edited_family_rows:
                        family_sku = row["Basis-SKU"]
                        existing_for_base = next(
                            (
                                saved for saved in mappings
                                if str(saved["base_sku"]).casefold()
                                == family_sku.casefold()
                            ),
                            None,
                        )
                        if standard_unit == "stuk":
                            package_quantity = int(row["Stuks per verpakking"])
                            save_piece_package_count(family_sku, package_quantity)
                        else:
                            package_quantity = (
                                existing_for_base["base_package_quantity"]
                                if existing_for_base
                                else suggested_mapping_values(family_sku)[
                                    "base_package_quantity"
                                ]
                            )
                        save_mapping(
                            row["Nieuwe standaard-SKU"],
                            family_sku,
                            standard_quantity,
                            base_package_quantity=package_quantity,
                            unit_label=standard_unit,
                            customer_notice=customer_notice,
                            price_adjustment_type={
                                "Geen opslag": "none",
                                "Vast bedrag": "fixed",
                                "Percentage": "percent",
                            }[price_adjustment_label],
                            price_adjustment_value=price_adjustment_value,
                            replace_existing=True,
                        )
                    removed_skus = []
                    for saved_mapping in mappings:
                        mapped_base = str(saved_mapping["base_sku"]).casefold()
                        standard_sku = standard_skus_by_base.get(mapped_base)
                        same_standard_contents = (
                            float(saved_mapping["base_quantity"])
                            == float(standard_quantity)
                            and str(saved_mapping.get("unit_label") or "").casefold()
                            == standard_unit.casefold()
                        )
                        if (
                            standard_sku
                            and same_standard_contents
                            and str(saved_mapping["derived_sku"]).casefold()
                            != standard_sku.casefold()
                        ):
                            delete_mapping(saved_mapping["derived_sku"])
                            removed_skus.append(str(saved_mapping["derived_sku"]))
                    st.session_state[family_result_key] = {
                        "saved": len(edited_family_rows),
                        "removed": len(removed_skus),
                        "removed_skus": removed_skus,
                    }
                    st.session_state[pending_selection_key] = (
                        standard_skus_by_base.get(base_sku.casefold())
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

            if any(str(item.get("unit_label") or "").casefold() == "stuk" for item in mappings):
                st.divider()
                st.markdown("#### Eerdere stukverkopen verrekenen")
                st.warning(
                    "Shopify geeft standaard maximaal 60 dagen orderhistorie. "
                    "Controleer de voorvertoning voordat je de voorraad bijwerkt."
                )
                history_columns = st.columns([2, 1])
                history_start = history_columns[0].text_input(
                    "Begindatum",
                    placeholder="JJJJ-MM-DD",
                    key="piece_history_start_date",
                )
                if history_columns[1].button(
                    "Historie voorvertonen",
                    key="preview_piece_history",
                    use_container_width=True,
                ):
                    try:
                        st.session_state["piece_history_preview"] = backfill_piece_sales(
                            history_start, apply=False
                        )
                    except Exception as exc:
                        st.error(str(exc))
                history_preview = st.session_state.get("piece_history_preview")
                if history_preview:
                    st.info(
                        f"{history_preview['orders']} nieuwe orders · "
                        f"{len(history_preview['lines'])} orderregels · "
                        f"{history_preview['pieces']} netto verkochte stuks"
                    )
                    st.dataframe(
                        [
                            {
                                "Basis-SKU": sku,
                                "Netto verkochte stuks": pieces,
                            }
                            for sku, pieces in history_preview["totals"].items()
                        ],
                        hide_index=True,
                        use_container_width=True,
                    )
                    confirmed_history = st.checkbox(
                        "Ik heb de aantallen gecontroleerd en wil deze eenmalig verrekenen.",
                        key="confirm_piece_history",
                    )
                    if st.button(
                        "Bevestigen en voorraad verrekenen",
                        key="apply_piece_history",
                        type="primary",
                        disabled=not confirmed_history,
                        use_container_width=True,
                    ):
                        try:
                            result = backfill_piece_sales(history_start, apply=True)
                            st.session_state.pop("piece_history_preview", None)
                            st.success(
                                f"{len(result['lines'])} historische orderregels "
                                "verwerkt en de gedeelde voorraad bijgewerkt."
                            )
                        except Exception as exc:
                            st.error(str(exc))


def show_certilas_surcharge_bundles() -> None:
    st.markdown("## Certilas toeslagbundels")
    st.caption(
        "Automatische koppelingen uit de kolom Alloy surcharges. De hoeveelheid "
        "van het ZZ-component is gelijk aan het verpakkingsgewicht van het hoofdartikel."
    )
    summary = certilas_surcharge_bundle_stats()
    metrics = st.columns(3)
    metrics[0].metric("ZZ-bundels", summary["total"])
    metrics[1].metric("Prijs berekend", summary["ready"])
    metrics[2].metric("Aandacht nodig", summary["attention"])
    if not summary["total"]:
        st.info("Er zijn nog geen Certilas ZZ-toeslagbundels opgebouwd.")
        return
    search_col, status_col, limit_col = st.columns([3, 1.2, 1])
    query = search_col.text_input(
        "Zoek Certilas-bundel", placeholder="Hoofd-SKU, ZZ-SKU of omschrijving",
        key="certilas_surcharge_bundle_query",
    )
    status_label = status_col.selectbox(
        "Status", ["Alles", "Prijs berekend", "Aandacht nodig"],
        key="certilas_surcharge_bundle_status",
    )
    limit = limit_col.selectbox(
        "Maximaal", [50, 100, 250, 500, 1000], index=2,
        key="certilas_surcharge_bundle_limit",
    )
    rows = list_certilas_surcharge_bundles(
        query=query,
        status={"Alles":"all", "Prijs berekend":"ready", "Aandacht nodig":"attention"}[status_label],
        limit=int(limit),
    )
    status_names = {
        "ready": "Gereed", "missing_weight": "Gewicht ontbreekt",
        "missing_base_price": "Basisprijs ontbreekt",
        "missing_component": "ZZ-prijsregel ontbreekt",
    }
    display = [{
        "Hoofd-SKU": row["main_sku"], "Hoofdproduct": row.get("main_description") or "—",
        "ZZ-SKU": row["component_sku"], "ZZ-product": row.get("component_description") or "—",
        "Aantal (kg)": row.get("quantity"), "Bruto basisprijs": row.get("base_gross_price"),
        "Basisverkoopprijs": row.get("base_sales_price"),
        "ZZ-prijs/kg": row.get("component_unit_price"), "Toeslag": row.get("surcharge_total"),
        "Eindprijs": row.get("effective_sales_price"),
        "Status": status_names.get(row["status"], row["status"]),
    } for row in rows]
    st.dataframe(
        display, hide_index=True, width="stretch",
        column_config={
            "Bruto basisprijs": st.column_config.NumberColumn(format="€ %.2f"),
            "Basisverkoopprijs": st.column_config.NumberColumn(format="€ %.2f"),
            "ZZ-prijs/kg": st.column_config.NumberColumn(format="€ %.2f"),
            "Toeslag": st.column_config.NumberColumn(format="€ %.2f"),
            "Eindprijs": st.column_config.NumberColumn(format="€ %.2f"),
            "Aantal (kg)": st.column_config.NumberColumn(format="%.2f"),
        },
    )


def show_normal_bundle_editor() -> None:
    with st.expander("Normale bundel toevoegen of wijzigen", expanded=False):
        options = bundle_editor_options()
        labels = {"": "+ Nieuwe bundel"}
        labels.update({
            item["bundle_variant_id"]: f"{item.get('variant_sku') or 'geen SKU'} — {item['product_title']}"
            for item in options
        })
        selected_id = st.selectbox(
            "Kies een bestaande bundel of maak een nieuwe", list(labels),
            format_func=lambda value: labels[value], key="normal_bundle_editor_selection",
        )
        bundle = get_bundle_for_edit(selected_id) or {}
        suffix = selected_id or "new"
        title_col, sku_col = st.columns([2.2, 1])
        title = title_col.text_input(
            "Bundeltitel", value=bundle.get("product_title") or "",
            key=f"normal_bundle_title_{suffix}",
        )
        main_sku = sku_col.text_input(
            "Hoofd-SKU", value=bundle.get("variant_sku") or "",
            key=f"normal_bundle_sku_{suffix}",
        )
        variant_col, price_col, status_col = st.columns([2, 1, 1])
        variant_title = variant_col.text_input(
            "Variantnaam", value=bundle.get("variant_title") or "",
            key=f"normal_bundle_variant_{suffix}",
        )
        variant_price = price_col.number_input(
            "Bundelprijs", min_value=0.0, value=float(bundle.get("variant_price") or 0),
            step=0.01, key=f"normal_bundle_price_{suffix}",
        )
        statuses = ["DRAFT", "ACTIVE", "ARCHIVED"]
        current_status = str(bundle.get("product_status") or "DRAFT").upper()
        status = status_col.selectbox(
            "Productstatus", statuses,
            index=statuses.index(current_status) if current_status in statuses else 0,
            key=f"normal_bundle_status_{suffix}",
        )
        rows = [{
            "Aantal": item.get("quantity", 1), "SKU": item.get("variant_sku") or "",
            "Product": item.get("product_title") or "", "Variant": item.get("variant_title") or "",
            "Prijs": item.get("variant_price"),
        } for item in bundle.get("components", [])]
        if not rows:
            rows = [{"Aantal":1,"SKU":"","Product":"","Variant":"","Prijs":None}]
        edited = st.data_editor(
            rows, num_rows="dynamic", hide_index=True, width="stretch",
            key=f"normal_bundle_components_{suffix}",
            column_config={
                "Aantal": st.column_config.NumberColumn(min_value=1,step=1,format="%d"),
                "SKU": st.column_config.TextColumn(required=True),
                "Product": st.column_config.TextColumn(width="large"),
                "Variant": st.column_config.TextColumn(width="medium"),
                "Prijs": st.column_config.NumberColumn(min_value=0,step=0.01,format="€ %.2f"),
            },
        )
        st.caption(
            "Voeg regels toe met het plusje. Opslaan wijzigt alleen de PIM-preview "
            "en publiceert nog niets naar Shopify."
        )
        save_col, delete_col = st.columns([1, 1])
        if save_col.button(
            "Nieuwe bundel opslaan" if not selected_id else "Wijzigingen opslaan",
            type="primary", key=f"save_normal_bundle_{suffix}",
        ):
            try:
                save_bundle_draft(
                    bundle_variant_id=selected_id, product_title=title,
                    variant_sku=main_sku, variant_title=variant_title,
                    variant_price=variant_price, product_status=status, components=edited,
                )
                st.success("Bundel opgeslagen in de PIM-preview.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        if selected_id:
            confirm_delete = delete_col.checkbox(
                "Verwijderen bevestigen",
                key=f"confirm_delete_normal_bundle_{suffix}",
                help="Alleen de bundelrelatie wordt verwijderd; producten blijven bestaan.",
            )
            if delete_col.button(
                "Bundel verwijderen",
                disabled=not confirm_delete,
                key=f"delete_normal_bundle_{suffix}",
            ):
                try:
                    if delete_bundle(selected_id):
                        st.success("Bundel verwijderd. De onderliggende producten zijn behouden.")
                        st.rerun()
                    else:
                        st.warning("De bundel bestaat niet meer.")
                except Exception as exc:
                    st.error(str(exc))


def _show_normal_bundle_tab(project_dir: Path) -> None:
    server_bundle_path = (
        project_dir.parent
        / "output"
        / "weldingshop-nl.myshopify.com-bundle-export-202607271350.csv"
    )
    upload_column, server_column = st.columns(2)
    with upload_column:
        bundle_upload = st.file_uploader(
            "Bundel-export uploaden",
            type=["csv"],
            key="bundle_csv_upload",
            help="CSV-export met hoofdvarianten, componentvarianten en aantallen.",
        )
        if st.button(
            "Geüpload bestand in PIM laden",
            disabled=bundle_upload is None,
            type="primary",
            key="import_bundle_upload",
        ):
            try:
                result = import_bundle_csv(bundle_upload.getvalue(), bundle_upload.name)
                st.success(
                    f"{result['valid']} geldige bundelvarianten geladen; "
                    f"{result['invalid']} regels vragen aandacht."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Bundelbestand kon niet worden geïmporteerd: {exc}")
    with server_column:
        st.text_input(
            "Gevonden serverbestand",
            value=str(server_bundle_path) if server_bundle_path.exists() else "",
            disabled=True,
            key="bundle_server_path",
        )
        if st.button(
            "Serverbestand in PIM laden",
            disabled=not server_bundle_path.exists(),
            key="import_bundle_server",
        ):
            try:
                result = import_bundle_csv_path(server_bundle_path)
                st.success(
                    f"{result['valid']} geldige bundelvarianten geladen; "
                    f"{result['invalid']} regels vragen aandacht."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Serverbestand kon niet worden geïmporteerd: {exc}")

    show_normal_bundle_editor()

    summary = bundle_stats()
    metrics = st.columns(5)
    metrics[0].metric("Bundelvarianten", summary["total"])
    metrics[1].metric("Geldig", summary["valid"])
    metrics[2].metric("Aandacht nodig", summary["invalid"])
    metrics[3].metric("Geselecteerd", summary["selected"])
    metrics[4].metric("Componentregels", summary["components"])

    if not summary["total"]:
        st.info(
            "Laad eerst het bundel-exportbestand. Daarna verschijnen hier de "
            "selectieknoppen en productpreviews."
        )
        return

    select_column, clear_column, image_column = st.columns([1, 1, 2])
    if select_column.button(
        "Alles selecteren",
        type="primary",
        width="stretch",
        key="bundle_select_all",
    ):
        set_all_bundle_selection(True)
        st.rerun()
    if clear_column.button(
        "Niets selecteren",
        width="stretch",
        key="bundle_select_none",
    ):
        set_all_bundle_selection(False)
        st.rerun()
    if image_column.button(
        "Productgegevens en afbeeldingen vernieuwen",
        width="stretch",
        key="bundle_refresh_images",
    ):
        try:
            with st.spinner("Shopify-productafbeeldingen ophalen…"):
                result = refresh_bundle_images()
            st.success(
                f"{result['found']} afbeeldingen gevonden voor "
                f"{result['requested']} producten."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Afbeeldingen konden niet worden vernieuwd: {exc}")

    filter_column, selected_column, page_column = st.columns([3, 1, 1])
    search_query = filter_column.text_input(
        "Zoek bundels",
        placeholder="Titel, variant of SKU",
        key="bundle_preview_query",
    )
    selected_only = selected_column.checkbox(
        "Alleen geselecteerd",
        key="bundle_selected_only",
    )
    page_size = page_column.selectbox(
        "Per pagina",
        [10, 25, 50, 100, 250],
        index=1,
        key="bundle_page_size",
    )
    page_number = st.number_input(
        "Pagina",
        min_value=1,
        value=1,
        step=1,
        key="bundle_page_number",
    )
    previews = list_bundle_previews(
        query=search_query,
        selected_only=selected_only,
        limit=int(page_size),
        offset=(int(page_number) - 1) * int(page_size),
    )

    for bundle in previews:
        with st.container(border=True):
            image_col, detail_col, selection_col = st.columns([0.55, 5, 1.15])
            with image_col:
                if bundle.get("image_url"):
                    st.image(bundle["image_url"], width=72)
                else:
                    st.markdown(
                        "<div style='width:72px;height:72px;border:1px solid #ddd;"
                        "border-radius:6px;display:flex;align-items:center;"
                        "justify-content:center;font-size:10px;color:#777'>Geen foto</div>",
                        unsafe_allow_html=True,
                    )
            with detail_col:
                st.markdown(f"**{html.escape(bundle['product_title'])}**")
                variant_label = bundle.get("variant_title") or "Standaardvariant"
                st.caption(
                    f"{variant_label} · SKU: {bundle.get('variant_sku') or 'geen SKU'} · "
                    f"Prijs: {euro(bundle.get('variant_price'))} · "
                    f"Status: {bundle.get('product_status') or 'onbekend'} · "
                    f"{len(bundle['components'])} component(en)"
                )
                if not bundle["valid"]:
                    st.error(bundle.get("validation_message") or "Ongeldige bundel")
            with selection_col:
                desired = st.checkbox(
                    "Uploaden",
                    value=bool(bundle["selected"]),
                    disabled=not bool(bundle["valid"]),
                    key=f"bundle_selected_{bundle['bundle_variant_id']}",
                )
                if desired != bool(bundle["selected"]):
                    set_bundle_selection(bundle["bundle_variant_id"], desired)
                    st.rerun()
                st.caption(
                    "Klaar"
                    if bundle["valid"]
                    else "Geblokkeerd"
                )

            with st.expander(f"Bundelinhoud · {len(bundle['components'])} component(en)"):
                for component in bundle["components"]:
                    component_image, component_name, component_sku, component_price = st.columns(
                        [0.35, 4.5, 1.5, 1.1], vertical_alignment="center"
                    )
                    with component_image:
                        if component.get("image_url"):
                            st.image(component["image_url"], width=44)
                    variant = component.get("variant_title") or ""
                    variant_suffix = (
                        f" · {variant}" if variant and variant != "Default Title" else ""
                    )
                    component_name.markdown(
                        f"**{component['quantity']}× {html.escape(component['product_title'])}**"
                        f"{html.escape(variant_suffix)}"
                    )
                    component_sku.caption(
                        f"SKU: {component.get('variant_sku') or 'geen SKU'}"
                    )
                    component_price.caption(euro(component.get("variant_price")))


def show_bundle_builder(project_dir: Path) -> None:
    st.title("Bundelbouwer")
    st.caption(
        "Bundels en afzonderlijke prijscomponenten samenstellen en controleren."
    )
    st.info(
        "Previewmodus: wijzigingen worden in de PIM bewaard, maar er wordt nog "
        "niets automatisch naar Shopify gepubliceerd."
    )
    st.markdown(
        """
        <style>
        [class*="st-key-bundle_tab_"] {
            border-bottom: 1px solid #d6dce5;
        }
        [class*="st-key-bundle_tab_"] button {
            border: 0 !important;
            border-radius: 8px 8px 0 0 !important;
            min-height: 42px !important;
            font-weight: 650 !important;
            box-shadow: none !important;
            width: 100% !important;
        }
        [class*="st-key-bundle_tab_inactive_"] button {
            background: transparent !important;
            color: #4a5568 !important;
        }
        [class*="st-key-bundle_tab_inactive_"] button:hover {
            background: #f1f5f9 !important;
            color: #1565c0 !important;
        }
        [class*="st-key-bundle_tab_active_"] button {
            background: #c62828 !important;
            color: white !important;
            border-bottom: 3px solid #c62828 !important;
            font-weight: 800 !important;
        }
        [class*="st-key-bundle_tab_active_"] button * {
            color: white !important;
            font-weight: 800 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    section_key = "bundle_builder_selected_section"
    if section_key not in st.session_state:
        st.session_state[section_key] = "Bundels"

    def select_section(section: str) -> None:
        st.session_state[section_key] = section

    sections = ["Bundels", "Toeslagen", "Deelverkoop uit gedeelde voorraad"]
    selected_section = st.session_state[section_key]
    navigation = st.columns(3, gap="small")
    for index, (column, section) in enumerate(zip(navigation, sections)):
        state = "active" if section == selected_section else "inactive"
        column.button(
            section,
            key=f"bundle_tab_{state}_{index}",
            on_click=select_section,
            args=(section,),
            use_container_width=True,
        )
    if selected_section == "Bundels":
        _show_normal_bundle_tab(project_dir)
    elif selected_section == "Toeslagen":
        show_certilas_surcharge_bundles()
    else:
        show_derived_inventory_rules()
