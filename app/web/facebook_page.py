from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from app.social.facebook import (
    MetaError, PAGE_ID, history, preview, save_connection, save_planning, settings,
)


def show_facebook_page():
    st.title("Facebook-blogposts")
    st.caption("Deel maximaal één gepubliceerd News-artikel per dag op Weldingshop blog post.")
    config = settings()
    overview, connection, planning, logbook = st.tabs(
        ["Overzicht", "Verbinding", "Planning", "Historie"]
    )
    with overview:
        a, b, c = st.columns(3)
        a.metric("Verbinding", "Gecontroleerd" if config["connected"] else "Nog koppelen")
        b.metric("Automatisch delen", "Aan" if config["enabled"] else "Uit")
        c.metric("Dagelijks", config["daily_time"] + " · Nederland")
        st.link_button("Open Facebook-pagina", f"https://www.facebook.com/{PAGE_ID}")
        if config["last_error"]:
            st.error(config["last_error"])
        st.write(
            "De PIM zoekt het nieuwste artikel dat nog ontbreekt op Facebook. "
            "Vóór een plaatsing leest hij alle beschikbare paginaberichten, inclusief "
            "links in bijlagen. Ook eerdere handmatige plaatsingen tellen mee. "
            "Een geplaatst of eerder gevonden artikel wordt niet opnieuw gedeeld."
        )
        if st.button("Controleer en toon volgend bericht", disabled=not config["connected"]):
            try:
                with st.spinner("Artikelen en Facebook-geschiedenis controleren…"):
                    result = preview()
                st.session_state["facebook_blog_preview"] = result
            except Exception as exc:
                st.error(str(exc) if isinstance(exc, (MetaError, ValueError)) else
                         "Controle niet voltooid. Controleer de verbinding en probeer opnieuw.")
        result = st.session_state.get("facebook_blog_preview")
        if result:
            st.caption(
                f"{result['articles']} News-artikelen en {result['posts']} Facebook-berichten "
                f"gecontroleerd; {result['remaining']} artikelen ontbreken nog."
            )
            if result["blocked"]:
                st.warning(
                    "Een eerdere plaatsing is niet bevestigd. Automatisch delen blijft "
                    "gepauzeerd totdat de uitkomst op Facebook is gecontroleerd."
                )
            article = result["article"]
            if article:
                st.subheader("Voorbeeld van het volgende bericht")
                if article.get("image"):
                    st.image(article["image"]["url"], width=600)
                st.text(article["message"])
                st.link_button("Lees het blogartikel", article["url"])
                st.caption(
                    "Dit is alleen een voorbeeld. Bij plaatsing haalt Facebook de afbeelding "
                    "en titel uit de linkvoorvertoning van het artikel."
                )
            else:
                st.info("Er is op dit moment geen ontbrekend artikel om te delen.")
    with connection:
        st.subheader("Veilig koppelen")
        st.write(f"Vaste bestemming: **Weldingshop blog post**, pagina-ID **{PAGE_ID}**.")
        st.info(
            "Gebruik een nieuw paginatoegangstoken. Een gebruikerstoken is niet voldoende. "
            "Voer het token alleen hier in; het wordt versleuteld opgeslagen en nooit in "
            "de historie getoond. De rechten voor lezen en plaatsen moeten zijn toegekend."
        )
        if config["checked_at"]:
            st.caption("Verbinding laatst gecontroleerd: " + config["checked_at"])
        with st.form("facebook_connection", clear_on_submit=True):
            token = st.text_input("Nieuw paginatoegangstoken", type="password", autocomplete="new-password")
            submitted = st.form_submit_button("Opslaan en verbinding controleren")
        if submitted:
            try:
                with st.spinner("Pagina en leesrechten controleren…"):
                    save_connection(token)
                st.session_state.pop("facebook_blog_preview", None)
                st.success("Verbinding gecontroleerd en versleuteld opgeslagen. Zet de planning aan onder Planning.")
            except Exception as exc:
                st.error(str(exc) if isinstance(exc, (MetaError, ValueError)) else
                         "Opslaan niet gelukt. Controleer het paginatoken en probeer opnieuw.")
    with planning:
        st.subheader("Dagelijkse planning")
        st.write(
            "SEO Connector maakt momenteel om de twee dagen een blog aan. "
            "Met oudere artikelen ingeschakeld kan de PIM ook op tussenliggende dagen delen."
        )
        with st.form("facebook_planning"):
            enabled = st.checkbox("Dagelijks automatisch delen", value=bool(config["enabled"]))
            hour, minute = map(int, config["daily_time"].split(":"))
            daily = st.time_input("Tijdstip in Nederland", value=time(hour, minute), step=300)
            backfill = st.checkbox(
                "Op dagen zonder nieuw artikel een ouder, nog niet gedeeld artikel gebruiken",
                value=bool(config["backfill"]),
            )
            st.caption(
                "De server controleert iedere 15 minuten. Maximaal één plaatsing per "
                "Nederlandse kalenderdag. Als alles al gedeeld is, wordt niets geplaatst. "
                "Bij een onzekere plaatsing pauzeert de planning om dubbelplaatsen te voorkomen."
            )
            submitted = st.form_submit_button("Planning opslaan")
        if submitted:
            try:
                save_planning(enabled, daily.strftime("%H:%M"), backfill)
                st.session_state.pop("facebook_blog_preview", None)
                st.success("Planning opgeslagen.")
            except (ValueError, RuntimeError) as exc:
                st.error(str(exc))
    with logbook:
        st.subheader("Historie")
        st.caption(
            "Plaatsingen, eerder gevonden berichten, overgeslagen artikelen, "
            "verbindingscontroles, wijzigingen en fouten. Laatste 300 gebeurtenissen; "
            "alle historie blijft bewaard. Tijden zijn Nederlands."
        )
        st.button("Historie verversen")
        rows = history()
        statuses = sorted({r["status"] for r in rows})
        selected = st.multiselect("Filter op status", statuses)
        term = st.text_input("Zoeken in historie")
        data = []
        for r in rows:
            if selected and r["status"] not in selected:
                continue
            if term and term.casefold() not in ((r["title"] or "") + " " + r["detail"]).casefold():
                continue
            timestamp = datetime.fromisoformat(r["created_at"]).astimezone(ZoneInfo("Europe/Amsterdam"))
            data.append({
                "Datum en tijd": timestamp.strftime("%d-%m-%Y %H:%M"),
                "Status": r["status"], "Artikel": r["title"] or "",
                "Toelichting": r["detail"], "Blogartikel": r["url"] or "",
                "Facebook-bericht": r["permalink"] or "", "Bericht-ID": r["post_id"] or "",
            })
        if data:
            frame = pd.DataFrame(data)
            st.dataframe(
                frame, hide_index=True, width="stretch",
                column_config={
                    "Blogartikel": st.column_config.LinkColumn("Blogartikel", display_text="Open artikel"),
                    "Facebook-bericht": st.column_config.LinkColumn("Facebook-bericht", display_text="Open Facebook"),
                },
            )
            st.download_button("Download getoonde historie", frame.to_csv(index=False).encode("utf-8-sig"),
                               file_name="facebook-bloghistorie.csv", mime="text/csv")
        else:
            st.info("Nog geen historie voor dit filter. Hier verschijnen de eerste controles en plaatsingen.")
