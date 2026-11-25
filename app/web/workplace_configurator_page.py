from __future__ import annotations

from datetime import datetime
import json

import pandas as pd
import streamlit as st

from app.workplace_configurator import (
    create_model_improvement_requests,
    current_version, latest_sync, model_export_frame, model_export_xlsx,
    model_improvement_bundle, model_improvement_bundle_bytes,
    model_improvement_requests, model_json_bytes, models_json_bytes,
    refresh_live_snapshot, summary_counts, version_history, version_models,
    validate_model_improvement_bundle,
)
from app.workplace_configurator_update import (
    run_model_improvement, run_update, update_history,
)


TAB_HELP = {
    "Overzicht": "Toont de live productieversie en alle categorieën met aantallen.",
    "Modellen": (
        "Toont ieder live model met artikelnaam, SKU, Shopify variant-ID, 3D-profiel "
        "en maatvoering. JSON is per model of voor de zichtbare selectie te downloaden."
    ),
    "Updaten": (
        "Werkt de Shopify-configurator veilig bij via een preview, validatie, browsertest "
        "en regressietest. Alleen een volledig geslaagde versie wordt gepubliceerd."
    ),
    "Historie": (
        "Bewaart productieversies en iedere handmatige of automatische update, inclusief "
        "status, Shopify-resultaat, regressietest en eventuele foutmelding."
    ),
}


def _info_header(title: str) -> None:
    title_col, info_col = st.columns([12, 1], vertical_alignment="center")
    title_col.subheader(title)
    info_col.button("ⓘ", key=f"configurator_info_{title}", help=TAB_HELP[title], type="tertiary")


def _format_time(value: str) -> str:
    if not value:
        return "Onbekend"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return value


def _finding_frame(rows: list[dict], kind: str) -> pd.DataFrame:
    labels = {
        "new": ("Artikel", "SKU('s)", "Beoordeling", "Waarom gemeld", "Product"),
        "remove": ("Live model", "SKU", "Status", "Waarom controleren", "Product"),
        "valkenpower": ("Controle", "", "Uitkomst", "Wat betekent dit?", "Bron"),
    }[kind]
    return pd.DataFrame([
        {labels[0]: row["name"], labels[1]: row["sku"], labels[2]: row["status"],
         labels[3]: row["reason"], labels[4]: row["url"]}
        for row in rows
    ])


def _render_findings(title: str, explanation: str, rows: list[dict], kind: str) -> None:
    with st.container(border=True):
        st.markdown(f"### {title}")
        st.caption(explanation)
        st.metric("Aantal", len(rows))
        if rows:
            frame = _finding_frame(rows, kind)
            link_name = frame.columns[-1]
            st.dataframe(
                frame, hide_index=True, width="stretch",
                column_config={link_name: st.column_config.LinkColumn(link_name, display_text="Open")},
            )
        else:
            st.success("Geen aandachtspunten gevonden.")


def _render_previous_update(sync: dict | None) -> None:
    if not sync:
        st.info("Nog geen update uitgevoerd. Kies hieronder Updaten.")
        return
    status_col, time_col, source_col, tests_col = st.columns(4)
    ok = sync["status"] == "SUCCEEDED"
    status_col.metric("Vorige update", "Succes" if ok else "Gefaald")
    time_col.metric("Voltooid", _format_time(sync.get("finished_at") or sync["started_at"]))
    source_col.metric(
        "Startwijze", "Automatisch" if sync.get("trigger_type") == "AUTOMATISCH" else "Handmatig"
    )
    regression = str(sync.get("regression_status") or "Onbekend")
    tests_col.metric("Regressietest", "Succes" if regression == "SUCCES" else regression.replace("_", " ").title())
    if not ok and sync.get("error"):
        st.error(f"Vorige update is gefaald: {sync['error']}")


def _render_model_improvement_request(models: list[dict], version: dict) -> None:
    st.divider()
    st.markdown("### Modelverbetering aanvragen")
    st.caption(
        "Selecteer een los 3D-model, meerdere modellen of een volledige productgroep "
        "wanneer de 3D-weergave onvoldoende op de productfoto lijkt. Het verzoek wordt "
        "duurzaam vastgelegd en door de eerstvolgende update volledig automatisch verwerkt."
    )
    st.info(
        "Na het toevoegen druk je op **Updaten**. De PIM analyseert de productfoto, bouwt "
        "het 3D-model, uploadt alles naar een ongepubliceerde Shopify-preview, test de "
        "geselecteerde modellen en de volledige configurator en publiceert pas bij succes. "
        "Er is geen handmatige download of upload nodig."
    )
    flash = st.session_state.pop("configurator_model_request_flash", None)
    if flash:
        st.success(flash)

    scope_label = st.radio(
        "Wat moet worden verbeterd?",
        ["Eén of meer modellen", "Hele productgroep"],
        horizontal=True,
        help=(
            "Kies losse modellen op artikelnaam/SKU, of selecteer één complete "
            "configuratorcategorie als productgroep."
        ),
        key="configurator_model_request_scope",
    )
    reasons = [
        "3D-model lijkt niet op de productfoto",
        "Kleur of materiaal klopt niet",
        "Afmetingen of verhoudingen kloppen niet",
        "Onderdelen of details ontbreken",
        "Anders",
    ]
    reason = st.selectbox(
        "Reden",
        reasons,
        help="Deze omschrijving verschijnt in de openstaande verbeterwachtrij.",
        key="configurator_model_request_reason",
    )
    notes = st.text_area(
        "Toelichting (optioneel)",
        placeholder="Bijvoorbeeld: de hulparm ontbreekt en de bedieningskast staat aan de verkeerde zijde.",
        help="Beschrijf zo concreet mogelijk wat op de productfoto anders is.",
        key="configurator_model_request_notes",
    )

    targets: list[dict] = []
    if scope_label == "Eén of meer modellen":
        options = {
            f"{row['name']} · {row['sku'] or 'geen SKU'} · {row['variant_id']}": row
            for row in models
        }
        selected_labels = st.multiselect(
            "Model(len) selecteren",
            list(options),
            placeholder="Zoek op artikelnaam, SKU of variant-ID",
            help="Je kunt één of meerdere modellen in hetzelfde verzoek selecteren.",
            key="configurator_model_request_models",
        )
        selected_rows = [options[label] for label in selected_labels]
        targets = [
            {
                "target_key": str(row["variant_id"]),
                "target_label": row["name"],
                "variant_id": str(row["variant_id"]),
                "sku": row["sku"],
                "category": row["category"],
            }
            for row in selected_rows
        ]
        if selected_rows:
            st.dataframe(
                pd.DataFrame([
                    {
                        "Artikel": row["name"],
                        "SKU": row["sku"],
                        "Productgroep": row["category"],
                        "Productfoto": row["image_url"],
                    }
                    for row in selected_rows
                ]),
                hide_index=True,
                width="stretch",
                column_config={
                    "Productfoto": st.column_config.ImageColumn("Productfoto")
                },
            )
        scope = "MODEL"
    else:
        category_counts = (
            pd.Series([row["category"] for row in models if row["category"]])
            .value_counts()
            .sort_index()
            .to_dict()
        )
        group_options = [
            f"{category} · {count} modellen"
            for category, count in category_counts.items()
        ]
        selected_group = st.selectbox(
            "Productgroep selecteren",
            group_options,
            help="Alle modellen binnen deze configuratorcategorie worden als één verbeteropdracht vastgelegd.",
            key="configurator_model_request_group",
        )
        if selected_group:
            category = selected_group.rsplit(" · ", 1)[0]
            count = int(category_counts[category])
            targets = [{
                "target_key": category,
                "target_label": f"Productgroep {category} ({count} modellen)",
                "category": category,
            }]
            st.caption(f"Dit verzoek omvat **{count} modellen** in productgroep **{category}**.")
        scope = "GROUP"

    if st.button(
        "Verbeterverzoek toevoegen",
        type="secondary",
        disabled=not targets,
        help=(
            "Slaat de geselecteerde modellen of productgroep op in de PIM-wachtrij. "
            "De knop Updaten verwerkt daarna de volledige opdracht automatisch."
        ),
        key="configurator_add_model_request",
    ):
        try:
            created = create_model_improvement_requests(
                scope=scope,
                targets=targets,
                reason=reason,
                notes=notes,
                version=version,
            )
            if created:
                st.session_state["configurator_model_request_flash"] = (
                    f"{len(created)} verbeterverzoek(en) toegevoegd aan de openstaande wachtrij."
                )
            else:
                st.session_state["configurator_model_request_flash"] = (
                    "De gekozen modellen of productgroep staan al in de openstaande wachtrij."
                )
            st.rerun()
        except Exception as exc:
            st.error(f"Het verbeterverzoek kon niet worden opgeslagen: {exc}")

    open_requests = model_improvement_requests(status="OPEN")
    st.markdown("#### Openstaande verbeterverzoeken")
    st.caption(
        "Deze verzoeken blijven staan tot een gecontroleerde modelversie is opgeleverd."
    )
    if not open_requests:
        st.success("Er staan geen modelverbeteringen open.")
        return
    st.dataframe(
        pd.DataFrame([
            {
                "Aangevraagd": _format_time(row["created_at"]),
                "Omvang": "Productgroep" if row["scope"] == "GROUP" else "Model",
                "Artikel of productgroep": row["target_label"],
                "SKU": row["sku"],
                "Reden": row["reason"],
                "Toelichting": row["notes"],
                "Productieversie": f"thema {row['theme_id']} · {row['engine_version']} / {row['model_version']}",
                "Status": str(row.get("execution_status") or "NIET_GESTART").replace("_", " ").title(),
                "Laatste fout": row.get("last_error") or "",
            }
            for row in open_requests
        ]),
        hide_index=True,
        width="stretch",
        height=min(520, 86 + len(open_requests) * 36),
    )


def _render_model_improvement_execution() -> None:
    st.markdown("### Modelverbetering uitvoeren")
    st.caption(
        "Kies een open verzoek, download het actuele modelpakket, pas de 3D-eigenschappen "
        "aan en upload het bestand opnieuw. De PIM publiceert uitsluitend na een geslaagde "
        "Shopify-preview en volledige regressietest."
    )
    open_requests = model_improvement_requests(status="OPEN")
    if not open_requests:
        st.success("Er staat geen verbeterverzoek klaar om uit te voeren.")
        return
    options = {
        (
            f"#{row['id']} · {row['target_label']} · "
            f"{_format_time(row['created_at'])}"
        ): row
        for row in open_requests
    }
    selected_label = st.selectbox(
        "Open verbeterverzoek kiezen",
        list(options),
        help="Kies één los model of een eerder aangevraagde volledige productgroep.",
        key="configurator_execute_request",
    )
    request = options[selected_label]
    bundle = model_improvement_bundle(int(request["id"]))
    target_rows = bundle["models"]
    status_col, models_col, attempt_col = st.columns(3)
    status_col.metric("Verzoekstatus", "Open")
    models_col.metric("Modellen in opdracht", len(target_rows))
    execution_status = str(request.get("execution_status") or "NIET_GESTART")
    attempt_col.metric(
        "Vorige uitvoering",
        execution_status.replace("_", " ").title(),
    )
    if request.get("last_error"):
        st.error(f"Vorige uitvoering is gefaald: {request['last_error']}")
    st.dataframe(
        pd.DataFrame([
            {
                "Artikel": row.get("title", ""),
                "SKU": row.get("code", ""),
                "Variant-ID": row.get("variantId", ""),
                "Modeltype": row.get("modelKind", ""),
                "Profiel": row.get("appearance", ""),
                "Productfoto": row.get("image", ""),
            }
            for row in target_rows
        ]),
        hide_index=True,
        width="stretch",
        column_config={
            "Productfoto": st.column_config.ImageColumn("Productfoto"),
        },
    )
    file_name = f"modelverbetering-{request['id']}.json"
    st.download_button(
        "Download huidig modelpakket (JSON)",
        model_improvement_bundle_bytes(int(request["id"])),
        file_name=file_name,
        mime="application/json",
        help=(
            "Downloadt exact de geselecteerde live modellen. Pas alleen 3D-velden aan; "
            "Shopify-identiteit, SKU, titel, productlink en productfoto blijven beschermd."
        ),
        key=f"configurator_download_request_{request['id']}",
    )
    uploaded = st.file_uploader(
        "Aangepast modelpakket uploaden",
        type=["json"],
        help=(
            "Upload hetzelfde JSON-bestand nadat de geometrie, kleuren, verhoudingen of "
            "details zijn aangepast. Alle modellen uit een groepsopdracht moeten aanwezig blijven."
        ),
        key=f"configurator_upload_request_{request['id']}",
    )
    validation = None
    payload = None
    if uploaded is not None:
        try:
            payload = json.loads(uploaded.getvalue().decode("utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("De hoofdstructuur van JSON moet een object zijn.")
            validation = validate_model_improvement_bundle(
                int(request["id"]), payload
            )
            st.success(
                "Validatie geslaagd: "
                f"{len(validation['changedVariantIds'])} gewijzigd(e) model(len), "
                f"{validation['modelCount']} model(len) gecontroleerd."
            )
        except Exception as exc:
            st.error(f"Modelpakket is niet uitvoerbaar: {exc}")

    confirmed = st.checkbox(
        "Ik bevestig dat dit modelpakket via een Shopify-preview mag worden getest en bij succes gepubliceerd.",
        disabled=validation is None,
        key=f"configurator_confirm_request_{request['id']}",
    )
    if st.button(
        "Modelopdracht uitvoeren",
        type="primary",
        disabled=validation is None or not confirmed,
        help=(
            "Maakt een ongepubliceerde Shopify-themakopie, voert validatie, browsercontrole "
            "en regressietest uit en publiceert uitsluitend als alles slaagt."
        ),
        key=f"configurator_execute_request_{request['id']}",
    ):
        try:
            with st.spinner(
                "Preview bouwen, modellen controleren en volledige regressietest uitvoeren…"
            ):
                result = run_model_improvement(int(request["id"]), payload)
            st.success(
                f"Modelverbetering gepubliceerd in thema {result['published_theme_id']}. "
                f"{len(result['changed_variant_ids'])} model(len) bijgewerkt."
            )
            st.rerun()
        except Exception as exc:
            st.error(
                "Modelverbetering gefaald; de bestaande live configurator is behouden: "
                f"{exc}"
            )


def show_workplace_configurator_page() -> None:
    st.title("Werkplaatsconfigurator")
    st.caption(
        "Leg live 3D-modellen en productieversies vast en werk Shopify veilig bij, "
        "met een regressietest vóór iedere publicatie."
    )
    try:
        refresh_live_snapshot()
        version = current_version()
        if not version:
            raise RuntimeError("De opgeslagen productieversie kon niet worden teruggelezen.")
    except Exception as exc:
        version = current_version()
        if version:
            st.warning(
                "De live website kon nu niet worden ververst. Het laatst opgeslagen overzicht "
                f"wordt getoond. Technische melding: {exc}"
            )
        else:
            st.error(f"De productieconfigurator kon niet worden gelezen: {exc}")
            return

    models = version_models(int(version["id"]))
    overview_tab, models_tab, update_tab, history_tab = st.tabs(
        ["Overzicht", "Modellen", "Updaten", "Historie"]
    )

    with overview_tab:
        _info_header("Overzicht")
        counts = summary_counts(models)
        first = st.columns(4)
        first[0].metric("Live modellen", counts["models"], help="Aantal unieke Shopify-varianten met een live 3D-model.")
        first[1].metric("Productseries", counts["series"], help="EXPERT, STARX, STARX Deep en overige equipmentseries.")
        first[2].metric("Categorieën", counts["categories"], help="Aantal verschillende cataloguscategorieën in productie.")
        first[3].metric("Nu beschikbaar", counts["available"], help="Modellen waarvan de variant momenteel bestelbaar is.")
        st.markdown("### Actieve productieversie")
        detail = st.columns(4)
        detail[0].metric("Shopify-thema", str(version["theme_id"]))
        detail[1].metric("Storefront-build", f"/t/{version['storefront_theme']}/")
        detail[2].metric("3D-engine", version["engine_version"])
        detail[3].metric("Modelbibliotheek", version["model_version"])
        st.caption(
            f"Productielivegang/laatste themawijziging: **{_format_time(version['published_at'])}** · "
            f"catalogus {version['catalog_version']} · laatst gecontroleerd {_format_time(version['last_seen_at'])}."
        )
        st.link_button("Open productieconfigurator", version["page_url"], help="Opent de klantversie op weldingshop.nl.")
        st.markdown("### Alle categorieën in productie")
        st.caption("Iedere categorie in de live configurator, met het aantal gekoppelde modellen.")
        st.dataframe(
            pd.DataFrame(counts["all_categories"], columns=["Categorie", "Aantal modellen"]),
            hide_index=True, width="stretch", height=520,
        )

    with models_tab:
        _info_header("Modellen")
        st.caption("Eén regel is één live Shopify-variant/3D-modelkoppeling.")
        series = sorted({row["series"] for row in models if row["series"]})
        categories = sorted({row["category"] for row in models if row["category"]})
        filters = st.columns([2, 2, 3])
        selected_series = filters[0].multiselect("Serie", series, placeholder="Alle series")
        selected_categories = filters[1].multiselect("Categorie", categories, placeholder="Alle categorieën")
        term = filters[2].text_input("Zoeken", placeholder="Artikelnaam, SKU of variant-ID")
        filtered = []
        for row in models:
            if selected_series and row["series"] not in selected_series:
                continue
            if selected_categories and row["category"] not in selected_categories:
                continue
            if term and term.casefold() not in f"{row['name']} {row['sku']} {row['variant_id']}".casefold():
                continue
            filtered.append(row)
        frame = model_export_frame(filtered, version)
        st.write(f"**{len(filtered)} van {len(models)} modellen getoond**")
        st.dataframe(
            frame, hide_index=True, width="stretch", height=560,
            column_config={
                "Productlink": st.column_config.LinkColumn("Product", display_text="Open"),
                "Afbeelding": st.column_config.ImageColumn("Afbeelding"),
            },
        )
        file_base = f"werkplaatsconfigurator-{version['theme_id']}-{version['engine_version']}-{version['model_version']}"
        download_columns = st.columns(3)
        download_columns[0].download_button(
            "Download Excel-vastlegging", model_export_xlsx(frame), file_name=file_base + ".xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="Downloadt exact de modellen die na filtering zichtbaar zijn.", width="stretch",
        )
        download_columns[1].download_button(
            "Download CSV-vastlegging", frame.to_csv(index=False).encode("utf-8-sig"),
            file_name=file_base + ".csv", mime="text/csv",
            help="Universeel alternatief voor Excel en andere PIM-systemen.", width="stretch",
        )
        download_columns[2].download_button(
            "Download JSON (getoond)", models_json_bytes(filtered), file_name=file_base + ".json",
            mime="application/json", help="Downloadt de originele JSON van alle zichtbare modellen.",
            width="stretch",
        )
        st.markdown("### JSON van één model")
        st.caption("Kies één regel en download exact het live catalogusrecord.")
        if filtered:
            options = {
                f"{row['name']} · {row['sku'] or 'geen SKU'} · {row['variant_id']}": row
                for row in filtered
            }
            selected_label = st.selectbox("Model kiezen", list(options))
            selected_model = options[selected_label]
            st.download_button(
                "Download JSON van dit model", model_json_bytes(selected_model),
                file_name=(selected_model["sku"] or selected_model["variant_id"]) + ".json",
                mime="application/json", help="Downloadt één volledig configuratorrecord.",
            )

    with update_tab:
        _info_header("Updaten")
        st.info(
            "**Wat doet Updaten?** De PIM vergelijkt de live configurator met Shopify en Valkenpower, "
            "actualiseert veilige productgegevens en verwerkt alle open modelverbeteringen. Voor die "
            "modellen analyseert de PIM automatisch de Shopify-productfoto en bouwt hij nieuwe 3D-geometrie. "
            "Bij cataloguswijzigingen wordt eerst een ongepubliceerde Shopify-themakopie gemaakt. "
            "De modellen, renderer en catalogus worden in dezelfde run geüpload. Pas na validatie, "
            "browsercontrole en volledige regressietest wordt die kopie live gezet. "
            "Bij een fout blijft het bestaande live-thema actief."
        )
        st.caption(
            "Automatisch: iedere eerste maandag van de maand om 12:00 uur (Europe/Amsterdam). "
            "Deze knop start dezelfde procedure direct en wordt als Handmatig vastgelegd."
        )
        _render_model_improvement_request(models, version)
        st.divider()
        st.markdown("### Volledige update uitvoeren")
        st.caption(
            "Deze ene knop verwerkt productgegevens én alle open modelverbeteringen van begin tot eind."
        )
        _render_previous_update(latest_sync())
        if st.button(
            "Updaten", type="primary",
            help=(
                "Analyseert productfoto's, bouwt geselecteerde 3D-modellen, maakt een Shopify-preview, "
                "uploadt alle bestanden, voert regressietests uit en publiceert alleen bij succes."
            ),
        ):
            try:
                with st.spinner("Shopify en Valkenpower analyseren, preview bouwen en regressietest uitvoeren…"):
                    result = run_update(trigger_type="HANDMATIG")
                st.success(
                    f"Update geslaagd. {result['shopify_changed_count']} Shopify-wijziging(en) en "
                    f"{result.get('automatic_model_improvements', 0)} modelverbetering(en) verwerkt; "
                    "alle tests zijn geslaagd en de versie staat live."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Update gefaald. De bestaande live configurator is behouden: {exc}")

        sync = latest_sync()
        if sync and sync["status"] == "SUCCEEDED":
            st.caption(
                f"Laatste update: {_format_time(sync['finished_at'])} · "
                f"{'Automatisch' if sync.get('trigger_type') == 'AUTOMATISCH' else 'Handmatig'} · "
                f"Shopify: {sync.get('shopify_status', 'onbekend')} · "
                f"regressietest: {sync.get('regression_status', 'onbekend')}"
            )
            grouped = {
                kind: [dict(row) for row in sync["findings"] if row["kind"] == kind]
                for kind in ("new", "remove", "valkenpower")
            }
            _render_findings(
                "1. Mogelijke nieuwe artikelen",
                "Nieuwe producten worden alleen toegevoegd met complete modeldata; anders staat Actie vereist.",
                grouped["new"], "new",
            )
            _render_findings(
                "2. Uit de configurator",
                "Niet-bestaande, inactieve of niet-online varianten; verwijdering verloopt via een geteste preview.",
                grouped["remove"], "remove",
            )
            _render_findings(
                "3. Valkenpower-verbeteringen",
                "Vergelijking van EXPERT, STARX en STARX Deep met de vorige PIM-meting.",
                grouped["valkenpower"], "valkenpower",
            )

    with history_tab:
        _info_header("Historie")
        st.markdown("### Updatehistorie")
        update_frame = pd.DataFrame([
            {
                "Startwijze": "Automatisch" if row.get("trigger_type") == "AUTOMATISCH" else "Handmatig",
                "Gestart": _format_time(row.get("started_at", "")),
                "Voltooid": _format_time(row.get("finished_at", "")),
                "Status": "Succes" if row.get("status") == "SUCCEEDED" else ("Gefaald" if row.get("status") == "FAILED" else "Bezig"),
                "Nieuwe kandidaten": row.get("new_count", 0),
                "Verwijderd": row.get("removal_count", 0),
                "Shopify-wijzigingen": row.get("shopify_changed_count", 0),
                "Shopify": row.get("shopify_status", ""),
                "Regressietest": row.get("regression_status", ""),
                "Gepubliceerd thema": row.get("published_theme_id") or "—",
                "Foutmelding": row.get("error") or "",
            }
            for row in update_history()
        ])
        st.dataframe(update_frame, hide_index=True, width="stretch")
        st.download_button(
            "Download updatehistorie", update_frame.to_csv(index=False).encode("utf-8-sig"),
            file_name="werkplaatsconfigurator-updatehistorie.csv", mime="text/csv",
            help="Downloadt handmatige en automatische updates met Shopify- en regressiestatus.",
        )
        st.markdown("### Productieversies")
        rows = version_history()
        history = pd.DataFrame([
            {
                "Productielivegang": _format_time(row["published_at"]),
                "Eerst gezien door PIM": _format_time(row["first_seen_at"]),
                "Shopify-thema": row["theme_id"],
                "Storefront-build": "/t/" + row["storefront_theme"] + "/",
                "3D-engine": row["engine_version"],
                "Modelbibliotheek": row["model_version"],
                "Catalogus": row["catalog_version"],
                "Modellen": row["model_count"],
                "Status": "Actief" if row["id"] == version["id"] else "Vorige productieversie",
            }
            for row in rows
        ])
        st.dataframe(history, hide_index=True, width="stretch")
        st.download_button(
            "Download versiehistorie", history.to_csv(index=False).encode("utf-8-sig"),
            file_name="werkplaatsconfigurator-versiehistorie.csv", mime="text/csv",
            help="Downloadt alle vastgelegde productieversies en livegangdatums.",
        )
