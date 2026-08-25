from __future__ import annotations

import base64
import json
import mimetypes
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from .research import (
    discover_official_page, enrich_from_evidence, inspect_official_page,
    probe_product_page,
)
from .service import ProductMakerService, normalized_host
from .shopify import (
    product_metafield_definitions, publish, shopify_locations, suggest_categories,
)
from app.suppliers.hub import (
    get_supplier_product,
    list_suppliers as list_synced_suppliers,
    save_product_maker_values,
    search_supplier_products,
)
from app.suppliers.discounts import preview_sales_prices


def _draft_values(draft: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    values = {
        key: draft.get(key) for key in (
            "supplier_id", "sku", "ean", "manufacturer_number", "vendor", "title",
            "description_html", "short_description", "seo_title", "seo_description",
            "purchase_price", "sale_price", "compare_at_price", "initial_quantity",
            "purchase_unit", "sales_unit", "unit_factor", "product_type", "category_id",
            "category_label", "source_url", "notes",
            "price_from_purchase_invoice",
        )
    }
    values["tags"] = draft.get("tags") or []
    values["metafields"] = draft.get("metafields") or []
    values.update(overrides or {})
    return values


def _clear_product_editor_widgets() -> None:
    """Force widgets to reload freshly built values from the saved product."""
    for key in list(st.session_state):
        if str(key).startswith("pm_ws_"):
            del st.session_state[key]


def _pim_product_for_identifier(
    supplier: dict[str, Any], kind: str, identifier: str,
) -> dict[str, Any]:
    """Return one exact supplier-PIM match without consulting a website."""
    slug = str(supplier.get("sync_slug") or "").strip()
    clean_identifier = identifier.strip()
    if not slug or not clean_identifier:
        raise ValueError("Leverancier en identificatie zijn verplicht")
    matches: dict[str, dict[str, Any]] = {}
    for match in search_supplier_products(slug, clean_identifier, 50):
        product = get_supplier_product(slug, str(match.get("sku") or ""))
        if not product:
            continue
        if kind == "SKU":
            exact = (
                str(product.get("sku") or "").strip().casefold()
                == clean_identifier.casefold()
            )
        else:
            exact = (
                "".join(character for character in str(product.get("ean") or "") if character.isdigit())
                == "".join(character for character in clean_identifier if character.isdigit())
            )
        if exact:
            matches[str(product.get("sku") or "").casefold()] = product
    if not matches:
        raise ValueError(
            f"Geen exacte {kind}-match in de leveranciers-PIM van {supplier['name']}"
        )
    if len(matches) != 1:
        raise ValueError(
            f"De {kind} verwijst naar meerdere artikelen in de leveranciers-PIM"
        )
    return next(iter(matches.values()))


def _create_pim_identification(
    service: ProductMakerService, supplier: dict[str, Any], product: dict[str, Any],
) -> int:
    """Create and build a standalone draft solely from one identified PIM row."""
    product_sku = str(product["sku"])
    existing = next(
        (
            item for item in service.list_drafts(product_sku)
            if str(item.get("sku") or "").strip().casefold()
            == product_sku.strip().casefold()
        ),
        None,
    )
    draft_id = service.save_draft(
        int(existing["id"]) if existing else None,
        supplier_id=int(supplier["id"]), sku=product_sku,
        ean=str(product.get("ean") or ""), manufacturer_number="",
        vendor=str(product.get("brand") or product.get("vendor") or supplier["name"]),
        title=str(product.get("ai_title") or product.get("source_title") or ""),
        description_html="", short_description="", seo_title="",
        seo_description="", purchase_price="0", sale_price="0",
        compare_at_price="", initial_quantity=0, purchase_unit="stuk",
        sales_unit="stuk", unit_factor="1", product_type="",
        category_id="", category_label="", tags=[], metafields=[],
        source_url="", notes="Geïdentificeerd vanuit de leveranciers-PIM.",
    )
    service.clear_source_material(draft_id, preserve_manual_uploads=True)
    service.mark_incidental(draft_id, False)
    service.save_automation_settings(draft_id, source_research=False)
    _build_product_directly(service, draft_id)
    return draft_id


def _automatic_start(
    service: ProductMakerService, suppliers: list[dict[str, Any]],
) -> None:
    with st.expander("Volautomatisch starten met SKU of EAN", expanded=False):
        st.caption(
            "Vul een geldige officiële productpagina in. SKU, EAN, merk en titel "
            "worden gericht uit de pagina gelezen en daarna aan de "
            "PIM-identificatie doorgegeven."
        )
        with st.form("pm_automatic_start"):
            kind = st.radio(
                "Zoeken op", ["SKU", "EAN"], horizontal=True,
                help="De gekozen identificatie wordt uit de productpagina gelezen.",
            )
            identifier = st.text_input(
                f"{kind} *",
                help=f"Vul de volledige {kind} in zoals deze op de productpagina staat.",
            )
            product_url = st.text_input(
                "Productpagina van de leverancier *",
                placeholder="https://leverancier.example/product/...",
            )
            start = st.form_submit_button(
                "Product automatisch onderzoeken en opbouwen",
                type="primary", width="stretch",
            )
        if not start:
            return
        try:
            if not identifier.strip():
                raise ValueError(f"Vul een {kind} in")
            identity = probe_product_page(
                product_url.strip(), identifier.strip(), kind,
            )
            selected_identifier = identity["sku"] if kind == "SKU" else identity["ean"]
            if not selected_identifier:
                raise ValueError(
                    f"De officiële productpagina bevat geen herkenbare {kind}"
                )
            normalized_entered = (
                identifier.strip().casefold() if kind == "SKU"
                else "".join(character for character in identifier if character.isdigit())
            )
            normalized_page = (
                selected_identifier.casefold() if kind == "SKU"
                else "".join(
                    character for character in selected_identifier if character.isdigit()
                )
            )
            if not normalized_entered or normalized_entered != normalized_page:
                raise ValueError(
                    f"De ingevulde {kind} komt niet exact overeen met de productpagina"
                )
            domain = identity["domain"]
            supplier = next(
                (
                    item for item in suppliers
                    if domain in set(item.get("approved_domains") or [])
                ),
                None,
            )
            # De website-route mag de PIM uitsluitend gebruiken om het officiële
            # leveranciersdomein te herkennen, nooit als bron voor productvelden.
            incidental = supplier is None
            if incidental:
                temporary_supplier = next(
                    (
                        item for item in service.list_suppliers()
                        if not item.get("sync_slug")
                        if domain in set(item.get("approved_domains") or [])
                    ),
                    None,
                )
                supplier_id = (
                    int(temporary_supplier["id"]) if temporary_supplier else
                    service.save_supplier(
                        f"{identity['vendor']} (incidenteel: {domain})",
                        domain, brand=identity["vendor"]
                    )
                )
            else:
                supplier_id = int(supplier["id"])
            existing = next(
                (
                    item for item in service.list_drafts(str(identity["sku"]))
                    if str(item.get("sku") or "").strip().casefold()
                    == str(identity["sku"]).strip().casefold()
                ),
                None,
            )
            draft_id = service.save_draft(
                int(existing["id"]) if existing else None,
                supplier_id=supplier_id,
                sku=str(identity["sku"]),
                ean=str(identity.get("ean") or ""),
                manufacturer_number=identity["manufacturer_number"],
                vendor=str(identity["vendor"]), title=str(identity["title"]),
                description_html=identity["description_html"],
                short_description="", seo_title="",
                seo_description="", purchase_price="0", sale_price="0",
                compare_at_price="", initial_quantity=0, purchase_unit="stuk",
                sales_unit="stuk", unit_factor="1", product_type="",
                category_id="", category_label="", tags=[], metafields=[],
                source_url=identity["url"],
                notes=(
                    "Incidenteel Shopify-product; verwijderen na actieve publicatie."
                    if incidental else "Volautomatisch opgebouwd vanuit de officiële productpagina."
                ),
            )
            service.clear_source_material(draft_id)
            service.mark_incidental(draft_id, incidental)
            service.save_automation_settings(draft_id, source_research=True)
            inspect_official_page(service, draft_id, identity["url"])
            _build_product_directly(service, draft_id)
            st.session_state["pm_select_after_save"] = draft_id
            _clear_product_editor_widgets()
            st.success(
                "Automatische opbouw voltooid. Controleer alleen nog bewijs, prijs, "
                "geselecteerde foto en Shopify-velden."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Automatische opbouw gestopt: {exc}")


def _draft_editor(service: ProductMakerService, draft_id: int, suppliers: list[dict[str, Any]]) -> None:
    draft = service.get_draft(draft_id) if draft_id else {}
    supplier_map = {int(item["id"]): item for item in suppliers}
    supplier_ids = [0, *supplier_map]
    default_supplier = int(draft.get("supplier_id") or 0)
    with st.form("pm_draft_form"):
        supplier_id = st.selectbox(
            "Vooraf goedgekeurde leverancier",
            supplier_ids,
            index=supplier_ids.index(default_supplier) if default_supplier in supplier_ids else 0,
            format_func=lambda value: "Nog niet gekozen" if value == 0 else supplier_map[value]["name"],
        )
        identity = st.columns(4)
        sku = identity[0].text_input("SKU *", value=draft.get("sku", ""))
        ean = identity[1].text_input("EAN/GTIN", value=draft.get("ean", ""))
        manufacturer_number = identity[2].text_input(
            "Fabrikantnummer", value=draft.get("manufacturer_number", "")
        )
        default_vendor = (
            supplier_map.get(supplier_id, {}).get("brand")
            or supplier_map.get(supplier_id, {}).get("name") or draft.get("vendor", "")
        )
        vendor = identity[3].text_input("Merk / vendor *", value=default_vendor)
        title = st.text_input("Producttitel", value=draft.get("title", ""))
        short_description = st.text_area(
            "Korte productintroductie", value=draft.get("short_description", ""), height=80,
        )
        description_html = st.text_area(
            "Volledige productomschrijving (HTML)",
            value=draft.get("description_html", ""), height=220,
        )
        seo = st.columns(2)
        seo_title = seo[0].text_input("SEO-titel", value=draft.get("seo_title", ""))
        seo_description = seo[1].text_area(
            "SEO-beschrijving", value=draft.get("seo_description", ""), height=80,
        )
        commercial = st.columns(4)
        purchase_price = commercial[0].text_input(
            "Inkoopprijs", value=draft.get("purchase_price", "0.00")
        )
        sale_price = commercial[1].text_input(
            "Verkoopprijs", value=draft.get("sale_price", "0.00")
        )
        compare_at_price = commercial[2].text_input(
            "Vergelijkingsprijs", value=draft.get("compare_at_price", "")
        )
        initial_quantity = commercial[3].number_input(
            "Beginvoorraad", min_value=0, value=int(draft.get("initial_quantity") or 0),
        )
        units = st.columns(4)
        purchase_unit = units[0].text_input(
            "Inkoopeenheid", value=draft.get("purchase_unit", "stuk")
        )
        sales_unit = units[1].text_input(
            "Verkoopeenheid", value=draft.get("sales_unit", "stuk")
        )
        unit_factor = units[2].text_input(
            "Omrekenfactor", value=draft.get("unit_factor", "1")
        )
        product_type = units[3].text_input(
            "Producttype", value=draft.get("product_type", "")
        )
        tags = st.text_area(
            "Tags, één per regel", value="\n".join(draft.get("tags", [])), height=90,
        )
        source_url = st.text_input(
            "Geselecteerde officiële productpagina", value=draft.get("source_url", ""),
            help="Deze URL wordt pas bewezen nadat de volledige SKU, EAN of fabrikantcode is gevonden.",
        )
        notes = st.text_area("Interne werkaantekeningen", value=draft.get("notes", ""))
        submitted = st.form_submit_button(
            "Zelfstandig PIM-concept opslaan", type="primary", width="stretch"
        )
    if submitted:
        try:
            saved_id = service.save_draft(
                draft_id or None, supplier_id=supplier_id or None, sku=sku, ean=ean,
                manufacturer_number=manufacturer_number, vendor=vendor, title=title,
                short_description=short_description, description_html=description_html,
                seo_title=seo_title, seo_description=seo_description,
                purchase_price=purchase_price, sale_price=sale_price,
                compare_at_price=compare_at_price, initial_quantity=initial_quantity,
                purchase_unit=purchase_unit, sales_unit=sales_unit, unit_factor=unit_factor,
                product_type=product_type, category_id=draft.get("category_id", ""),
                category_label=draft.get("category_label", ""), tags=tags,
                metafields=draft.get("metafields", []), source_url=source_url, notes=notes,
            )
            # Een bestaande widgetkey mag binnen dezelfde Streamlit-render niet
            # worden gewijzigd. Selecteer het opgeslagen concept vóór de widget
            # in de volgende rendercyclus.
            st.session_state["pm_select_after_save"] = saved_id
            st.success(f"Zelfstandig PIM-concept #{saved_id} opgeslagen.")
            st.rerun()
        except Exception as exc:
            st.error(f"Opslaan mislukt: {exc}")


def _research_panel(service: ProductMakerService, draft_id: int) -> None:
    draft = service.get_draft(draft_id)
    st.markdown("### 2. Brononderzoek en bewijs")
    if not draft.get("supplier_id"):
        st.warning("Kies eerst een leverancier met goedgekeurde officiële domeinen.")
        return
    st.caption("Toegestane domeinen: " + ", ".join(draft.get("approved_domains") or []))
    if st.button("Exact product automatisch zoeken", width="stretch"):
        run_id = service.start_research(draft_id, "official_search", draft["sku"])
        try:
            result = discover_official_page(draft)
            service.finish_research(run_id, "complete", result)
            st.session_state["pm_search_candidates"] = result["candidates"]
        except Exception as exc:
            service.finish_research(run_id, "failed", {}, str(exc))
            st.error(f"Zoeken mislukt: {exc}")
    candidates = st.session_state.get("pm_search_candidates", [])
    selected_candidate_url = ""
    if candidates:
        st.markdown("#### Gevonden officiële productpagina’s")
        candidate_map = {
            str(item["url"]): (
                f"{item.get('title') or 'Productpagina'} · "
                f"gevonden via {item.get('matched_identifier') or 'identificatie'}"
            )
            for item in candidates
        }
        selected_candidate_url = st.radio(
            "Kies de bron die je wilt controleren",
            list(candidate_map), format_func=lambda value: candidate_map[value],
            key=f"pm_candidate_{draft_id}",
        )
        for index, item in enumerate(candidates, start=1):
            with st.container(border=True):
                st.markdown(f"**{index}. {item.get('title') or 'Officiële productpagina'}**")
                st.link_button("Productpagina openen", str(item["url"]))
                details = st.columns(2)
                details[0].write(
                    f"**Gevonden identificatie:** {item.get('matched_identifier') or '-'}"
                )
                details[1].write(f"**Domein:** {str(item['url']).split('/')[2]}")
                st.caption(str(item.get("reason") or "Exacte kandidaat op officieel domein."))
    manual_source_url = st.text_input(
        "Of plak zelf een officiële product-URL",
        value=draft.get("source_url") or "",
        key=f"pm_inspect_url_{draft_id}",
    )
    source_url = manual_source_url.strip() or selected_candidate_url
    if source_url:
        st.info(f"Te controleren bron: {source_url}")
    if st.button("Bron onafhankelijk controleren en feiten ophalen", width="stretch"):
        run_id = service.start_research(draft_id, "page_inspection", source_url)
        try:
            result = inspect_official_page(service, draft_id, source_url)
            service.finish_research(run_id, "complete", {key: value for key, value in result.items() if key != "page_text"})
            service.save_draft(draft_id, **_draft_values(draft, {"source_url": result["url"]}))
            st.success(
                f"Exact bevestigd via {result['matched_by']}: {result['matched_value']}. "
                f"{len(result['images'])} foto’s en {len(result['documents'])} documenten gevonden."
            )
            st.rerun()
        except Exception as exc:
            service.finish_research(run_id, "failed", {}, str(exc))
            st.error(f"Bron afgewezen: {exc}")
    evidence = service.list_evidence(draft_id)
    if evidence:
        st.markdown("#### Veldbewijs")
        open_evidence = [
            item for item in evidence
            if not item["approved"] and item["state"] != "conflict"
        ]
        if st.button(
            f"Al het bewijs goedkeuren ({len(open_evidence)})",
            type="primary", width="stretch", disabled=not open_evidence,
        ):
            approved_count = service.approve_all_evidence(draft_id)
            st.success(f"{approved_count} bewijsregel(s) goedgekeurd.")
            st.rerun()
        if any(item["state"] == "conflict" for item in evidence):
            st.warning(
                "Tegenstrijdig bewijs wordt nooit via de bulkknop goedgekeurd en "
                "moet afzonderlijk worden opgelost."
            )
        for item in evidence:
            cols = st.columns([1.2, 2, .7, .7])
            cols[0].write(f"**{item['field_name']}** · {item['state']}")
            cols[1].write(str(item["value"]))
            cols[1].caption(item["source_excerpt"] or item["source_title"])
            cols[2].write(f"{round(float(item['confidence']) * 100)}%")
            label = "Goedkeuring intrekken" if item["approved"] else "Bewijs goedkeuren"
            if cols[3].button(label, key=f"pm_evidence_{item['id']}"):
                service.approve_evidence(item["id"], not bool(item["approved"]))
                st.rerun()
    if st.button("AI-tekstvoorstel maken uit uitsluitend deze bewijsregels", width="stretch"):
        try:
            proposal = enrich_from_evidence(service, draft_id)
            st.session_state["pm_ai_proposal"] = proposal
        except Exception as exc:
            st.error(f"Tekstverrijking mislukt: {exc}")
    proposal = st.session_state.get("pm_ai_proposal")
    if proposal:
        st.markdown("#### AI-voorstel — nog niet toegepast")
        st.json(proposal)
        if st.button("Voorstel toepassen op het PIM-concept", type="primary"):
            overrides = {
                key: proposal[key] for key in (
                    "title", "short_description", "description_html", "seo_title",
                    "seo_description", "product_type", "tags",
                ) if proposal.get(key)
            }
            service.save_draft(draft_id, **_draft_values(draft, overrides))
            st.session_state.pop("pm_ai_proposal", None)
            st.success("AI-voorstel toegepast; controleer de tekst handmatig.")
            st.rerun()


def _assets_panel(service: ProductMakerService, draft_id: int) -> None:
    st.markdown("### 3. Foto’s en documenten")
    st.markdown("#### Foto handmatig toevoegen")
    uploaded_image = st.file_uploader(
        "Kies een foto vanaf je computer",
        type=["jpg", "jpeg", "png", "webp"],
        key=f"pm_manual_image_file_{draft_id}",
    )
    upload_title = st.text_input(
        "Alternatieve tekst voor upload (optioneel)",
        key=f"pm_manual_upload_title_{draft_id}",
    ).strip()
    upload_verified = st.checkbox(
        "Ik bevestig dat de geüploade foto exact dit artikel toont",
        key=f"pm_manual_upload_verified_{draft_id}",
    )
    if st.button(
        "Geüploade foto toevoegen", key=f"pm_manual_upload_add_{draft_id}",
        disabled=uploaded_image is None or not upload_verified,
    ):
        try:
            service.save_uploaded_image(
                draft_id, uploaded_image.name, uploaded_image.getvalue(),
                title=upload_title,
            )
            st.success("Foto geüpload en geselecteerd voor Shopify.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    st.caption("Of voeg een foto toe via een openbare link:")
    manual_url = st.text_input(
        "Openbare foto-URL",
        key=f"pm_manual_image_url_{draft_id}",
        placeholder="https://leverancier.nl/productfoto.jpg",
        help="Shopify moet de afbeelding via een openbare HTTPS-link kunnen ophalen.",
    ).strip()
    manual_title = st.text_input(
        "Alternatieve tekst (optioneel)", key=f"pm_manual_image_title_{draft_id}",
    ).strip()
    exact_product = st.checkbox(
        "Ik bevestig dat dit een officiële foto van exact dit artikel is",
        key=f"pm_manual_image_verified_{draft_id}",
    )
    if st.button(
        "Foto toevoegen", key=f"pm_manual_image_add_{draft_id}",
        disabled=not manual_url or not exact_product,
    ):
        if not manual_url.startswith("https://"):
            st.error("Gebruik een openbare HTTPS-link naar de foto.")
        else:
            service.add_asset(
                draft_id, "image", manual_url,
                title=manual_title or "Handmatig toegevoegde productfoto",
                source_url=manual_url, official=True,
                identifier_verified=True, selected=True,
            )
            st.success("Foto toegevoegd en geselecteerd voor Shopify.")
            st.rerun()

    assets = service.list_assets(draft_id)
    if not assets:
        st.info("Nog geen officiële foto’s of documenten gevonden.")
        return
    image_assets = [item for item in assets if item["kind"] == "image"]
    if image_assets:
        columns = st.columns(4)
        for index, item in enumerate(image_assets):
            with columns[index % 4]:
                st.image(item["url"], caption=item["title"] or "Officiële productfoto")
                st.caption("Exact artikel bevestigd" if item["identifier_verified"] else "Niet bevestigd")
                if st.button(
                    "Deselecteren" if item["selected"] else "Foto gebruiken",
                    key=f"pm_asset_{item['id']}", disabled=not item["identifier_verified"],
                ):
                    service.select_asset(item["id"], not bool(item["selected"]))
                    st.rerun()
    documents = [item for item in assets if item["kind"] != "image"]
    if documents:
        st.dataframe(
            [{"Type": item["kind"], "Titel": item["title"], "URL": item["url"]}
             for item in documents], width="stretch", hide_index=True,
        )


def _shopify_schema_panel(service: ProductMakerService, draft_id: int) -> None:
    draft = service.get_draft(draft_id)
    st.markdown("### 4. Shopify-categorie en metafields")
    query = st.text_input("Zoek in Shopify-taxonomie", value=draft["product_type"] or draft["title"])
    if st.button("Categorieën zoeken"):
        try:
            st.session_state["pm_categories"] = suggest_categories(query)
        except Exception as exc:
            st.error(f"Categorieën ophalen mislukt: {exc}")
    categories = st.session_state.get("pm_categories", [])
    if categories:
        category_map = {str(item["id"]): str(item.get("fullName") or item.get("name")) for item in categories}
        category_id = st.selectbox(
            "Shopify-productcategorie", list(category_map), format_func=lambda value: category_map[value]
        )
        if st.button("Categorie opslaan"):
            service.save_draft(
                draft_id, **_draft_values(draft, {
                    "category_id": category_id, "category_label": category_map[category_id],
                })
            )
            st.rerun()
    if draft["category_label"]:
        st.success(f"Gekozen categorie: {draft['category_label']}")
    try:
        definitions = product_metafield_definitions()
    except Exception as exc:
        definitions = []
        st.error(f"Metafielddefinities ophalen mislukt: {exc}")
    if definitions:
        definition_map = {
            f"{item.get('namespace')}.{item.get('key')}": item for item in definitions
            if item.get("namespace") and item.get("key")
        }
        selected_key = st.selectbox("Shopify-metafield toevoegen", ["", *definition_map])
        value = st.text_input("Waarde voor geselecteerd metafield", key="pm_metafield_value")
        if st.button("Metafield aan concept toevoegen", disabled=not selected_key or not value):
            configured = [
                item for item in draft["metafields"]
                if f"{item.get('namespace')}.{item.get('key')}" != selected_key
            ]
            namespace, key = selected_key.split(".", 1)
            configured.append({"namespace": namespace, "key": key, "value": value})
            service.save_draft(draft_id, **_draft_values(draft, {"metafields": configured}))
            service.add_evidence(
                draft_id, f"metafield.{namespace}.{key}", value,
                state="proposed", source_title="Handmatig metafieldvoorstel",
                source_excerpt="Koppel dit veld aan een officiële bron of keur het bewust goed.",
                matched_by="manual", confidence=.5, approved=False,
            )
            st.rerun()
    if draft["metafields"]:
        st.dataframe(draft["metafields"], width="stretch", hide_index=True)


def _quality_and_publish(service: ProductMakerService, draft_id: int) -> None:
    draft = service.get_draft(draft_id)
    report = service.quality_report(draft_id)
    st.markdown("### 5. Kwaliteitspoort en Shopify")
    st.progress(report["score"] / 100, text=f"Productkwaliteit {report['score']}%")
    st.dataframe(
        [{"Controle": label, "Status": "Gereed" if passed else "Ontbreekt (advies)"}
         for label, passed in report["checks"].items()], width="stretch", hide_index=True,
    )
    if not report["ready"]:
        st.info("Deze punten zijn waarschuwingen. Opslaan, voorvertonen en publiceren blijven mogelijk.")
    if draft["shopify_admin_url"]:
        st.link_button("Open product in Shopify-beheer", draft["shopify_admin_url"], width="stretch")
    try:
        locations = shopify_locations()
    except Exception as exc:
        locations = []
        st.error(f"Shopify-locaties ophalen mislukt: {exc}")
    location_map = {str(item["id"]): str(item.get("name") or item["id"])
                    for item in locations if item.get("id")}
    location_id = st.selectbox(
        "Voorraadlocatie", list(location_map), format_func=lambda value: location_map[value],
    ) if location_map else ""
    publication_status = st.radio(
        "Publicatiestatus",
        ["Actief", "Concept"],
        horizontal=True,
        help=(
            "Actief publiceert naar alle beschikbare verkoopkanalen. "
            "Concept blijft uitsluitend zichtbaar in Shopify-beheer."
        ),
        key=f"pm_publication_status_{draft_id}",
    )
    if st.button(
        "Publiceer in Shopify op alle kanalen",
        type="primary",
        width="stretch",
        disabled=not location_id or draft["status"] == "active",
        help="Bij de keuze Concept wordt het product niet aan verkoopkanalen gekoppeld.",
        key=f"pm_publish_all_{draft_id}",
    ):
        try:
            active = publication_status == "Actief"
            result = publish(service, draft_id, location_id, active=active)
            if active:
                st.success(
                    "Product actief gemaakt en gepubliceerd op "
                    f"{result['published_channels']} Shopify-verkoopkanalen."
                )
            else:
                st.success("Product als ongepubliceerd Shopify-concept opgeslagen.")
            st.link_button("Open in Shopify", result["admin_url"])
            st.rerun()
        except Exception as exc:
            st.error(f"Publiceren naar Shopify mislukt: {exc}")


def _editable_product_fields(
    service: ProductMakerService, draft: dict[str, Any],
    suppliers: list[dict[str, Any]], draft_id: int,
) -> dict[str, Any]:
    """Render the compact left-hand editor and return its current widget values."""
    supplier_map = {int(item["id"]): item for item in suppliers}
    supplier_ids = [0, *supplier_map]
    default_supplier = int(draft.get("supplier_id") or 0)
    heading, action = st.columns([1, 1.35], vertical_alignment="center")
    heading.markdown("#### Identificatie")
    create_from_pim = action.button(
        "Product uit de PIM aanmaken", type="primary", width="stretch",
        key=f"pm_create_from_pim_{draft_id}",
        help=(
            "Gebruikt de gekozen leverancier en de ingevulde SKU of EAN. "
            "Er wordt geen productpagina onderzocht."
        ),
    )
    supplier_id = st.selectbox(
        "Vooraf goedgekeurde leverancier", supplier_ids,
        index=supplier_ids.index(default_supplier) if default_supplier in supplier_ids else 0,
        format_func=lambda value: "Nog niet gekozen" if value == 0 else supplier_map[value]["name"],
        key=f"pm_ws_supplier_{draft_id}",
    )
    sku = st.text_input("SKU *", value=draft.get("sku", ""), key=f"pm_ws_sku_{draft_id}")
    ident = st.columns(2)
    ean = ident[0].text_input("EAN/GTIN", value=draft.get("ean", ""), key=f"pm_ws_ean_{draft_id}")
    manufacturer_number = ident[1].text_input(
        "Fabrikantnummer", value=draft.get("manufacturer_number", ""), key=f"pm_ws_mpn_{draft_id}"
    )
    if create_from_pim:
        try:
            if not supplier_id:
                raise ValueError("Kies eerst een leverancier")
            kind = "SKU" if sku.strip() else "EAN"
            identifier = sku.strip() or ean.strip()
            if not identifier:
                raise ValueError("Vul eerst een SKU of EAN in")
            supplier = supplier_map[int(supplier_id)]
            product = _pim_product_for_identifier(supplier, kind, identifier)
            with st.spinner("Product uitsluitend vanuit de PIM opbouwen…"):
                created_id = _create_pim_identification(service, supplier, product)
            st.session_state["pm_select_after_save"] = created_id
            _clear_product_editor_widgets()
            st.success(f"PIM-product {product['sku']} is aangemaakt.")
            st.rerun()
        except Exception as exc:
            st.error(f"Product uit de PIM aanmaken mislukt: {exc}")
    default_vendor = (supplier_map.get(supplier_id, {}).get("brand")
                      or supplier_map.get(supplier_id, {}).get("name") or draft.get("vendor", ""))
    if supplier_id:
        vendor = st.text_input(
            "Merk / vendor (uit leverancier)", value=default_vendor,
            key=f"pm_ws_vendor_{draft_id}_{supplier_id}", disabled=True,
            help="Automatisch overgenomen uit de goedgekeurde leveranciersregistratie.",
        )
    else:
        vendor = st.text_input(
            "Merk / vendor *", value=draft.get("vendor", ""),
            key=f"pm_ws_vendor_{draft_id}_manual",
            help="Verplicht omdat nog geen goedgekeurde leverancier is gekozen.",
        )

    with st.expander("🟦 1. Productbasis", expanded=True):
        title = st.text_input("Producttitel", value=draft.get("title", ""), key=f"pm_ws_title_{draft_id}")
        short_description = st.text_area(
            "Korte productintroductie", value=draft.get("short_description", ""), height=90,
            key=f"pm_ws_short_{draft_id}",
        )
        description_html = st.text_area(
            "Volledige productomschrijving (HTML)", value=draft.get("description_html", ""), height=210,
            key=f"pm_ws_description_{draft_id}",
        )
        seo_title = st.text_input("SEO-titel", value=draft.get("seo_title", ""), key=f"pm_ws_seo_title_{draft_id}")
        seo_description = st.text_area(
            "SEO-beschrijving", value=draft.get("seo_description", ""), height=80,
            key=f"pm_ws_seo_description_{draft_id}",
        )
        prices = st.columns(2)
        purchase_price = prices[0].text_input("Inkoopprijs", value=draft.get("purchase_price", "0.00"), key=f"pm_ws_buy_{draft_id}")
        sale_price = prices[1].text_input("Verkoopprijs", value=draft.get("sale_price", "0.00"), key=f"pm_ws_sell_{draft_id}")
        price_from_purchase_invoice = st.toggle(
            "Prijs komt uit een inkoopfactuur",
            value=bool(draft.get("price_from_purchase_invoice", False)),
            key=f"pm_ws_invoice_price_{draft_id}",
            help=(
                "Aan: publiceren met €0 is toegestaan en de prijs wordt later vanuit "
                "een inkoopfactuur aangevuld. Uit: geldige inkoop- en verkoopprijs zijn verplicht."
            ),
        )
        compare_at_price = st.text_input("Vergelijkingsprijs", value=draft.get("compare_at_price", ""), key=f"pm_ws_compare_{draft_id}")
        initial_quantity = st.number_input("Beginvoorraad", min_value=0, value=int(draft.get("initial_quantity") or 0), key=f"pm_ws_stock_{draft_id}")
        units = st.columns(2)
        purchase_unit = units[0].text_input("Inkoopeenheid", value=draft.get("purchase_unit", "stuk"), key=f"pm_ws_purchase_unit_{draft_id}")
        sales_unit = units[1].text_input("Verkoopeenheid", value=draft.get("sales_unit", "stuk"), key=f"pm_ws_sales_unit_{draft_id}")
        unit_factor = st.text_input("Omrekenfactor", value=draft.get("unit_factor", "1"), key=f"pm_ws_factor_{draft_id}")
        product_type = st.text_input("Producttype", value=draft.get("product_type", ""), key=f"pm_ws_type_{draft_id}")
        tags = st.text_area("Tags, één per regel", value="\n".join(draft.get("tags", [])), height=80, key=f"pm_ws_tags_{draft_id}")
        if supplier_id:
            source_url = draft.get("source_url", "")
            st.caption("Productbron: goedgekeurde Leverancierssynchronisatie")
        else:
            source_url = st.text_input(
                "Officiële productpagina *", value=draft.get("source_url", ""),
                key=f"pm_ws_source_{draft_id}",
                help="Verplicht voor een leverancier die niet vooraf is goedgekeurd.",
            )
        notes = st.text_area("Interne werkaantekeningen", value=draft.get("notes", ""), key=f"pm_ws_notes_{draft_id}")
    return {
        "supplier_id": supplier_id or None, "sku": sku, "ean": ean,
        "manufacturer_number": manufacturer_number, "vendor": vendor, "title": title,
        "short_description": short_description, "description_html": description_html,
        "seo_title": seo_title, "seo_description": seo_description,
        "purchase_price": purchase_price, "sale_price": sale_price,
        "price_from_purchase_invoice": price_from_purchase_invoice,
        "compare_at_price": compare_at_price, "initial_quantity": initial_quantity,
        "purchase_unit": purchase_unit, "sales_unit": sales_unit, "unit_factor": unit_factor,
        "product_type": product_type, "category_id": draft.get("category_id", ""),
        "category_label": draft.get("category_label", ""), "tags": tags,
        "metafields": draft.get("metafields", []), "source_url": source_url, "notes": notes,
    }


def _preview_image_source(value: str) -> str:
    """Return a browser-safe source for remote and locally uploaded images."""
    path = Path(str(value or ""))
    if not path.is_absolute():
        return str(value or "")
    if not path.is_file():
        return ""
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _theme_preview(draft: dict[str, Any]) -> None:
    images = [item for item in draft.get("assets", []) if item["kind"] == "image" and item["selected"]]
    image_url = _preview_image_source(images[0]["url"]) if images else ""
    price = escape(str(draft.get("sale_price") or "0.00")).replace(".", ",")
    title = escape(draft.get("title") or "Producttitel verschijnt hier")
    vendor = escape(draft.get("vendor") or "Merk")
    intro = escape(draft.get("short_description") or "De korte productintroductie verschijnt hier.")
    description = draft.get("description_html") or "<p>De volledige productomschrijving verschijnt hier.</p>"
    media = (f'<img src="{escape(image_url, quote=True)}" alt="{title}">' if image_url else
             '<div class="placeholder"><span>PRODUCTFOTO</span></div>')
    html = f"""
    <style>
      *{{box-sizing:border-box}} body{{margin:0;background:#f5f5f3;color:#1d1d1b;font-family:Arial,sans-serif}}
      .bar{{height:38px;background:#ef7d00;color:white;text-align:center;padding:11px;font-size:12px;font-weight:700}}
      header{{background:white;padding:20px 30px;border-bottom:1px solid #ddd;font-size:23px;font-weight:900;letter-spacing:.5px}}
      header b{{color:#ef7d00}} .crumb{{padding:18px 5%;font-size:12px;color:#70706e}}
      main{{display:grid;grid-template-columns:56% 44%;gap:32px;background:white;padding:34px 5%;min-height:610px}}
      .media img,.placeholder{{width:100%;height:430px;object-fit:contain;background:#f3f3f1;border-radius:3px}}
      .placeholder{{display:flex;align-items:center;justify-content:center;color:#999;font-weight:bold}}
      .vendor{{text-transform:uppercase;color:#777;font-size:12px;letter-spacing:1.5px}}
      h1{{font-size:30px;line-height:1.15;margin:12px 0}} .intro{{color:#555;line-height:1.55}}
      .price{{font-size:28px;font-weight:800;margin:24px 0}} .tax{{font-size:11px;color:#777}}
      button{{width:100%;border:0;background:#ef7d00;color:white;font-weight:800;padding:17px;border-radius:2px}}
      .stock{{color:#33833b;font-size:13px;margin:15px 0}} .description{{margin-top:30px;border-top:1px solid #ddd;padding-top:20px;line-height:1.55}}
      @media(max-width:650px){{main{{grid-template-columns:1fr}}}}
    </style>
    <div class="bar">VAKMANSCHAP, SERVICE EN SNELLE LEVERING</div>
    <header>WELDING<b>SHOP</b></header><div class="crumb">Home / {vendor} / {title}</div>
    <main><div class="media">{media}</div><section><div class="vendor">{vendor}</div><h1>{title}</h1>
    <p class="intro">{intro}</p><div class="price">€ {price} <span class="tax">excl. btw</span></div>
    <div class="stock">● Op voorraad</div><button>IN WINKELWAGEN</button>
    <div class="description">{description}</div></section></main>"""
    components.html(html, height=790, scrolling=True)


def _missing_price(values: dict[str, Any]) -> bool:
    try:
        return float(str(values.get("sale_price") or "0").replace(",", ".")) <= 0
    except ValueError:
        return True


@st.dialog("Publiceren naar Shopify", width="large")
def _direct_publish_dialog(
    service: ProductMakerService, selected_id: int, values: dict[str, Any],
) -> None:
    price_from_purchase_invoice = st.toggle(
        "Prijs komt uit een inkoopfactuur",
        value=bool(values.get("price_from_purchase_invoice", False)),
        key=f"pm_direct_invoice_price_{selected_id}",
        help=(
            "Aan: Shopify-publicatie met €0 is toegestaan; de prijs wordt later "
            "vanuit een inkoopfactuur aangevuld. Uit: prijzen zijn verplicht."
        ),
    )
    if price_from_purchase_invoice:
        st.info("Prijsstatus: wacht op een inkoopfactuur. Publicatie met €0 is toegestaan.")
    elif _missing_price(values):
        st.error(
            "Publicatie is geblokkeerd: vul een inkoop- en verkoopprijs in, "
            "of zet ‘Prijs komt uit een inkoopfactuur’ aan."
        )
    status = st.radio(
        "Productstatus", ["Actief", "Concept"], horizontal=True,
        key=f"pm_direct_status_{selected_id}",
    )
    publish_all_channels = st.radio(
        "Publiceren op alle kanalen", ["Aan", "Uit"], horizontal=True,
        key=f"pm_direct_channels_{selected_id}",
    ) == "Aan"
    continue_selling = st.radio(
        "Doorgaan met verkopen zonder voorraad", ["Aan", "Uit"], horizontal=True,
        key=f"pm_direct_continue_selling_{selected_id}",
    ) == "Aan"

    st.markdown("#### Waarschuwingen")
    warnings = []
    if _missing_price(values) and not price_from_purchase_invoice:
        warnings.append("Prijs niet ingevuld")
    if int(values.get("initial_quantity") or 0) <= 0 and not continue_selling:
        warnings.append("Voorraad niet ingevuld")
    elif int(values.get("initial_quantity") or 0) <= 0:
        st.caption("Voorraad niet ingevuld: N.v.t. — doorgaan zonder voorraad staat aan.")
    if status == "Concept" and publish_all_channels:
        st.caption("Publiceren op alle kanalen: N.v.t. voor een conceptproduct.")
    for warning in warnings:
        st.warning(warning)
    if not warnings:
        st.success("Geen blokkerende prijs- of voorraadwaarschuwingen.")

    publish_col, cancel_col = st.columns(2)
    if publish_col.button(
        "Publiceren naar Shopify", type="primary", width="stretch",
        key=f"pm_direct_confirm_{selected_id}",
        disabled=_missing_price(values) and not price_from_purchase_invoice,
    ):
        try:
            active = status == "Actief"
            with st.spinner("Product opbouwen en publiceren naar Shopify…"):
                publish_values = {
                    **values,
                    "price_from_purchase_invoice": price_from_purchase_invoice,
                }
                build_id = service.save_draft(selected_id or None, **publish_values)
                _build_product_directly(service, build_id)
                built_draft = service.get_draft(build_id)
                location_id = _automatic_publish_location(built_draft)
                result = publish(
                    service, build_id, location_id, active=active,
                    publish_all_channels=publish_all_channels,
                    continue_selling=continue_selling,
                )
            _clear_product_editor_widgets()
            if active:
                channel_text = (
                    f" en op {result['published_channels']} verkoopkanalen gepubliceerd"
                    if publish_all_channels else " zonder verkoopkanalen te koppelen"
                )
                st.success(f"Product is actief gemaakt{channel_text}.")
            else:
                st.success("Product als Shopify-concept opgeslagen.")
            link_columns = st.columns(2) if result.get("storefront_url") else [st]
            link_columns[0].link_button(
                "Open product in Shopify", result["admin_url"], width="stretch",
            )
            if result.get("storefront_url"):
                link_columns[1].link_button(
                    "Open op weldingshop.nl", result["storefront_url"], width="stretch",
                )
        except Exception as exc:
            st.error(f"Opbouwen of publiceren naar Shopify mislukt: {exc}")
    if cancel_col.button(
        "Cancel / terug", width="stretch", key=f"pm_direct_cancel_{selected_id}",
    ):
        st.rerun()


def _build_product_directly(service: ProductMakerService, draft_id: int) -> None:
    """Build one saved draft using the enabled standalone automation standards."""
    draft = service.get_draft(draft_id)
    settings = service.automation_settings(draft_id)
    if draft.get("price_from_purchase_invoice"):
        service.refresh_purchase_invoice_price(draft_id)
        draft = service.get_draft(draft_id)
    if (
        settings["source_research"]
        and draft.get("source_url")
        and not draft.get("approved_domains")
        and not draft.get("supplier_id")
    ):
        # A manually entered product can have an explicit official source URL
        # without having passed through the website-identification wizard. Verify
        # the page before trusting its host, then retain that host as the narrowly
        # scoped allowlist for this incidental supplier.
        identity = probe_product_page(
            str(draft["source_url"]), str(draft.get("sku") or ""), "SKU",
        )
        domain = identity["domain"]
        temporary_supplier = next(
            (
                item for item in service.list_suppliers()
                if not item.get("sync_slug")
                if domain in set(item.get("approved_domains") or [])
            ),
            None,
        )
        supplier_id = (
            int(temporary_supplier["id"]) if temporary_supplier else
            service.save_supplier(
                f"{identity['vendor']} (incidenteel: {domain})",
                domain, brand=identity["vendor"],
            )
        )
        service.save_draft(
            draft_id, **_draft_values(draft, {"supplier_id": supplier_id}),
        )
        service.mark_incidental(draft_id)
        draft = service.get_draft(draft_id)
    if draft.get("supplier_sync_slug") and not settings["source_research"]:
        synced_supplier = _synced_supplier_for_draft(draft)
        if not synced_supplier:
            raise ValueError("Deze goedgekeurde leverancier heeft nog geen Leverancierssynchronisatie")
        _hydrate_from_supplier_sync(service, draft_id)
        draft = service.get_draft(draft_id)
    elif not settings["source_research"] and not draft.get("source_url"):
        raise ValueError("Vul voor een niet-goedgekeurde leverancier een officiële productpagina in")
    inspected = None
    errors: list[str] = []
    if settings["source_research"]:
        urls = [draft.get("source_url") or ""]
        if not draft.get("source_url"):
            discovery = discover_official_page(draft)
            st.session_state["pm_search_candidates"] = discovery["candidates"]
            urls.extend(str(item["url"]) for item in discovery["candidates"])
        for url in dict.fromkeys(item for item in urls if item):
            try:
                inspected = inspect_official_page(service, draft_id, url)
                break
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        if inspected is None:
            raise ValueError(
                "Geen exact verifieerbare officiële productpagina gevonden. "
                + (errors[-1] if errors else "")
            )
        service.save_draft(
            draft_id, **_draft_values(draft, {"source_url": inspected["url"]})
        )
        draft = service.get_draft(draft_id)
    if settings["evidence_enrichment"] and draft.get("evidence"):
        proposal = enrich_from_evidence(service, draft_id)
        overrides = {
            key: proposal[key] for key in (
                "title", "short_description", "description_html", "seo_title",
                "seo_description", "product_type", "tags",
            ) if proposal.get(key)
        }
        if overrides:
            service.save_draft(draft_id, **_draft_values(draft, overrides))
            draft = service.get_draft(draft_id)
    if settings["category_suggestion"] and not draft.get("category_id"):
        category_context = " | ".join(
            str(value).strip() for value in (
                draft.get("product_type"), draft.get("title"), draft.get("vendor"),
            ) if str(value or "").strip()
        ) or str(draft["sku"])
        categories = suggest_categories(category_context)
        if categories:
            category = categories[0]
            service.save_draft(
                draft_id, **_draft_values(draft, {
                    "category_id": str(category["id"]),
                    "category_label": str(category.get("fullName") or category.get("name")),
                })
            )
    if settings["asset_collection"]:
        images = [item for item in service.list_assets(draft_id) if item["kind"] == "image"]
        if images and not any(item["selected"] for item in images):
            service.select_asset(images[0]["id"], True)


def _synced_supplier_for_draft(draft: dict[str, Any]) -> dict[str, Any] | None:
    sync_slug = str(draft.get("supplier_sync_slug") or "").strip()
    if sync_slug:
        return next(
            (item for item in list_synced_suppliers() if item.get("slug") == sync_slug),
            None,
        )
    supplier_names = {
        str(value).strip().casefold()
        for value in (draft.get("supplier_name"), draft.get("vendor"))
        if str(value or "").strip()
    }
    approved_domains = set(draft.get("approved_domains") or [])
    return next(
        (
            item for item in list_synced_suppliers()
            if str(item.get("name") or "").casefold() in supplier_names
            or normalized_host(str(item.get("website_url") or "")) in approved_domains
        ),
        None,
    )


def _automatic_publish_location(draft: dict[str, Any]) -> str:
    """Use the supplier's configured Shopify location for one-click publishing."""
    supplier = _synced_supplier_for_draft(draft)
    configured = str((supplier or {}).get("shopify_location_id") or "")
    locations = shopify_locations()
    active_ids = {str(item.get("id") or "") for item in locations}
    if configured in active_ids:
        return configured
    weldingshop = next(
        (
            str(item["id"]) for item in locations
            if str(item.get("name") or "").strip().casefold() == "weldingshop"
        ),
        "",
    )
    if weldingshop:
        return weldingshop
    raise ValueError("Geen geldige automatische Shopify-voorraadlocatie gevonden")


def _restore_missing_supplier_prices(
    service: ProductMakerService, draft_id: int,
) -> bool:
    """Restore zero/empty draft prices from its trusted supplier catalogue."""
    draft = service.get_draft(draft_id)
    synced_supplier = _synced_supplier_for_draft(draft)
    if not synced_supplier:
        return False
    slug = str(synced_supplier["slug"])
    product = get_supplier_product(slug, str(draft.get("sku") or ""))
    if not product:
        return False

    def missing_price(value: Any) -> bool:
        try:
            return float(str(value or "0").replace(",", ".")) <= 0
        except (TypeError, ValueError):
            return True

    updates: dict[str, Any] = {}
    if missing_price(draft.get("purchase_price")):
        purchase_price = product.get("cost_price") or product.get("price")
        if purchase_price is not None and float(purchase_price) > 0:
            updates["purchase_price"] = purchase_price
    if missing_price(draft.get("sale_price")):
        price_preview = next(
            (
                row for row in preview_sales_prices(slug, limit=None)
                if str(row.get("SKU") or "").casefold()
                == str(product.get("sku") or "").casefold()
            ),
            None,
        )
        sale_price = (
            price_preview.get("Berekende verkoopprijs")
            if price_preview else None
        )
        sale_price = sale_price or product.get("sale_price") or product.get("price")
        if sale_price is not None and float(sale_price) > 0:
            updates["sale_price"] = sale_price
    if not updates:
        return False
    service.save_draft(draft_id, **_draft_values(draft, updates))
    return True


def _hydrate_from_supplier_sync(service: ProductMakerService, draft_id: int) -> None:
    """Copy one trusted supplier-catalogue record into the standalone PIM."""
    draft = service.get_draft(draft_id)
    synced_supplier = _synced_supplier_for_draft(draft)
    if not synced_supplier:
        raise ValueError("Deze goedgekeurde leverancier heeft nog geen Leverancierssynchronisatie")
    slug = str(synced_supplier["slug"])
    product = get_supplier_product(slug, draft["sku"])
    if not product:
        for identifier in (draft.get("ean"), draft.get("manufacturer_number"), draft.get("sku")):
            if not identifier:
                continue
            matches = search_supplier_products(slug, str(identifier), 20)
            exact = next(
                (item for item in matches if str(item.get("sku") or "").casefold() == str(identifier).casefold()),
                matches[0] if len(matches) == 1 else None,
            )
            if exact:
                product = get_supplier_product(slug, str(exact["sku"]))
                break
    if not product:
        raise ValueError(
            f"SKU/EAN {draft.get('sku') or draft.get('ean')} staat niet in de "
            f"Leverancierssynchronisatie van {synced_supplier['name']}"
        )
    price_preview = next(
        (
            row for row in preview_sales_prices(slug, limit=None)
            if str(row.get("SKU") or "").casefold() == str(product["sku"]).casefold()
        ),
        None,
    )
    calculated_sale_price = (
        price_preview.get("Berekende verkoopprijs") if price_preview else None
    )
    try:
        tags = json.loads(product.get("ai_tags_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        tags = []
    values = {
        "sku": product.get("sku") or draft["sku"],
        "ean": product.get("ean") or draft.get("ean") or "",
        "vendor": product.get("brand") or product.get("vendor") or synced_supplier["name"],
        "title": product.get("ai_title") or product.get("source_title") or draft.get("title") or "",
        "description_html": product.get("html_description") or draft.get("description_html") or "",
        "short_description": product.get("source_description") or draft.get("short_description") or "",
        "purchase_price": product.get("cost_price") or product.get("price") or draft.get("purchase_price") or 0,
        "sale_price": (
            calculated_sale_price
            if calculated_sale_price is not None
            else product.get("sale_price") or product.get("price")
            or draft.get("sale_price") or 0
        ),
        "initial_quantity": product.get("stock_quantity") or draft.get("initial_quantity") or 0,
        "purchase_unit": product.get("purchase_unit") or draft.get("purchase_unit") or "stuk",
        "sales_unit": product.get("sales_unit") or draft.get("sales_unit") or "stuk",
        "unit_factor": product.get("purchase_units_per_sales_unit") or draft.get("unit_factor") or 1,
        "product_type": product.get("product_group_name") or product.get("category") or draft.get("product_type") or "",
        "tags": tags or draft.get("tags") or [],
    }
    service.save_draft(draft_id, **_draft_values(draft, values))
    trusted_source = f"Leverancierssynchronisatie · {synced_supplier['name']} · {product['sku']}"
    for field_name, value in values.items():
        if field_name == "sale_price" and price_preview and calculated_sale_price is not None:
            continue
        if value not in (None, "", []):
            service.add_evidence(
                draft_id, field_name, value, state="proven", source_title=trusted_source,
                source_excerpt="Geïmporteerd uit de actuele catalogus van de goedgekeurde leverancier.",
                matched_by="supplier_sync", confidence=1, approved=True,
            )
    if price_preview and calculated_sale_price is not None:
        service.add_evidence(
            draft_id, "sale_price", calculated_sale_price, state="derived",
            source_title=(
                f"Verkoopprijsrekenmodule · {synced_supplier['name']} · "
                f"{price_preview.get('Verkoopprijsregel') or price_preview.get('Rekenmethode')}"
            ),
            source_excerpt=(
                f"Netto inkoopprijs {price_preview.get('Netto inkoopprijs')}; "
                f"methode {price_preview.get('Rekenmethode')}; "
                f"berekende verkoopprijs {calculated_sale_price}."
            ),
            matched_by="supplier_pricing_module", confidence=1, approved=True,
        )
    for image in product.get("images") or []:
        if image.get("image_url"):
            service.add_asset(
                draft_id, "image", str(image["image_url"]),
                title=str(image.get("alt_text") or values["title"]),
                source_url=trusted_source, official=True, identifier_verified=True,
            )


def show_product_maker() -> None:
    service = ProductMakerService()
    st.title("Zelfstandige Shopify-productmaker")
    st.caption(
        "Bewijs-gestuurd productonderzoek, tekstverrijking en Shopify-publicatie. "
        "Leest goedgekeurde leverancierscatalogi als bron en slaat het resultaat "
        "uitsluitend op in de eigen zelfstandige PIM."
    )
    service.sync_registered_suppliers(list_synced_suppliers())
    suppliers = service.list_suppliers(synced_only=True)
    left, right = st.columns([1, 2], gap="large")
    with left:
        _automatic_start(service, suppliers)
        pending_selection = st.session_state.pop("pm_select_after_save", None)
        if pending_selection is not None:
            st.session_state["pm_current_draft_id"] = int(pending_selection)
        selected_id = int(st.session_state.get("pm_current_draft_id") or 0)
        if not selected_id:
            recent_drafts = service.list_drafts()
            editable = [
                item for item in recent_drafts
                if item.get("status") in {"draft", "shopify_draft"}
            ]
            if editable:
                selected_id = int(editable[0]["id"])
                st.session_state["pm_current_draft_id"] = selected_id
        try:
            draft = service.get_draft(selected_id) if selected_id else None
        except ValueError:
            selected_id = 0
            st.session_state["pm_current_draft_id"] = 0
            draft = None
        if draft and _restore_missing_supplier_prices(service, selected_id):
            _clear_product_editor_widgets()
            st.session_state["pm_price_restore_notice"] = True
            st.rerun()
        if st.session_state.pop("pm_price_restore_notice", False):
            st.success("Ontbrekende prijzen opnieuw uit de leveranciers-PIM geladen.")
        draft = draft or {
            "purchase_price": "0.00", "sale_price": "0.00", "compare_at_price": "",
            "price_from_purchase_invoice": False,
            "purchase_unit": "stuk", "sales_unit": "stuk", "unit_factor": "1",
            "tags": [], "metafields": [], "assets": [],
        }
        values = _editable_product_fields(service, draft, suppliers, selected_id)
        if selected_id:
            settings = service.automation_settings(selected_id)
            with st.expander("🟩 2. Brononderzoek en bewijs", expanded=False):
                st.caption("Automatische standaarden — per onderdeel handmatig uit te zetten")
                source_research = st.toggle("Officiële bron automatisch onderzoeken", settings["source_research"], key=f"pm_auto_source_{selected_id}")
                evidence_enrichment = st.toggle("Tekst uit goedgekeurd bewijs opbouwen", settings["evidence_enrichment"], key=f"pm_auto_evidence_{selected_id}")
                changed = source_research != settings["source_research"] or evidence_enrichment != settings["evidence_enrichment"]
                if changed:
                    service.save_automation_settings(
                        selected_id, source_research=source_research,
                        evidence_enrichment=evidence_enrichment,
                    )
                _research_panel(service, selected_id)
            with st.expander("🟧 3. Foto’s en documenten", expanded=False):
                asset_collection = st.toggle("Officiële media automatisch verzamelen", settings["asset_collection"], key=f"pm_auto_assets_{selected_id}")
                if asset_collection != settings["asset_collection"]:
                    service.save_automation_settings(selected_id, asset_collection=asset_collection)
                _assets_panel(service, selected_id)
            with st.expander("🟪 4. Shopify-categorie en metafields", expanded=False):
                category_suggestion = st.toggle("Shopify-categorie automatisch voorstellen", settings["category_suggestion"], key=f"pm_auto_category_{selected_id}")
                if category_suggestion != settings["category_suggestion"]:
                    service.save_automation_settings(selected_id, category_suggestion=category_suggestion)
                _shopify_schema_panel(service, selected_id)
            with st.expander("🟥 5. Kwaliteitspoort en Shopify", expanded=False):
                quality_checks = st.toggle("Kwaliteitscontroles standaard uitvoeren", settings["quality_checks"], key=f"pm_auto_quality_{selected_id}")
                if quality_checks != settings["quality_checks"]:
                    service.save_automation_settings(selected_id, quality_checks=quality_checks)
                _quality_and_publish(service, selected_id)
        else:
            st.info("Sla de identificatie en productbasis op om onderzoek en Shopify-onderdelen te openen.")
        if st.button("Opslaan in de zelfstandige PIM", type="primary", width="stretch"):
            try:
                saved_id = service.save_draft(selected_id or None, **values)
                saved_draft = service.get_draft(saved_id)
                if saved_draft.get("supplier_sync_slug"):
                    save_product_maker_values(
                        str(saved_draft["supplier_sync_slug"]),
                        str(saved_draft["sku"]),
                        _draft_values(saved_draft),
                        saved_draft.get("assets") or [],
                    )
                st.session_state["pm_select_after_save"] = saved_id
                if saved_draft.get("supplier_sync_slug"):
                    st.success(
                        f"Product {saved_draft['sku']} is opgeslagen bij "
                        f"{saved_draft['supplier_name']}."
                    )
                else:
                    st.info(
                        "Incidenteel product tijdelijk bewaard voor Shopify; "
                        "niet opgeslagen in een leveranciers-PIM."
                    )
                st.rerun()
            except Exception as exc:
                st.error(f"Opslaan in de PIM mislukt: {exc}")
    with right:
        preview_title, preview_action = st.columns([1.45, 1])
        preview_title.markdown("#### Directe Shopify-themaweergave")
        build_now = preview_action.button(
            "Aangemaakt product publiceren in Shopify",
            type="primary", width="stretch",
            key=f"pm_build_now_{selected_id}",
        )
        if build_now:
            _direct_publish_dialog(service, selected_id, values)
        st.caption("Live voorvertoning: wijzigingen links worden direct zichtbaar, ook vóór opslaan.")
        build_warning = st.session_state.pop("pm_build_warning", None)
        if build_warning:
            st.warning(build_warning)
        preview = dict(draft)
        preview.update(values)
        _theme_preview(preview)
