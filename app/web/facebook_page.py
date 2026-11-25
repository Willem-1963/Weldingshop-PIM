from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from app.social.facebook import (
    MetaError, PAGE_ID, begin_oauth_connection, complete_oauth_connection, history, preview,
    publish_preview, save_planning, settings,
)


def _finish_oauth_callback():
    """Process Meta's redirect once, without retaining OAuth values in the address bar."""
    params = st.query_params
    code, state, denied = params.get("code"), params.get("state"), params.get("error")
    if not (code or denied):
        return
    try:
        if denied:
            st.session_state["facebook_oauth_feedback"] = (
                "error", "Facebook-koppelen is geannuleerd. Er is niets gewijzigd."
            )
        elif not state:
            st.session_state["facebook_oauth_feedback"] = (
                "error", "De Facebook-koppeling kon niet worden gecontroleerd. Probeer opnieuw."
            )
        else:
            page_name = complete_oauth_connection(str(state), str(code))
            st.session_state["facebook_oauth_feedback"] = (
                "success", f"Verbonden met {page_name}. Zet de planning aan onder Planning."
            )
    except (MetaError, ValueError) as exc:
        st.session_state["facebook_oauth_feedback"] = ("error", str(exc))
    finally:
        for key in ("code", "state", "error", "error_reason", "error_description"):
            if key in st.query_params:
                del st.query_params[key]
        st.session_state.pop("facebook_oauth_url", None)


def show_facebook_page():
    _finish_oauth_callback()
    st.title("Facebook-blogposts")
    st.caption("Plaats handmatig meerdere News-artikelen; de automatische planning deelt maximaal één bericht per dag.")
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
        result = st.session_state.get("facebook_blog_preview")
        check_column, publish_column = st.columns(2)
        if check_column.button("Controleer en toon volgend bericht", disabled=not config["connected"]):
            try:
                with st.spinner("Artikelen en Facebook-geschiedenis controleren…"):
                    result = preview()
                st.session_state["facebook_blog_preview"] = result
            except Exception as exc:
                st.error(str(exc) if isinstance(exc, (MetaError, ValueError)) else
                         "Controle niet voltooid. Controleer de verbinding en probeer opnieuw.")
        if publish_column.button(
            "Plaats dit bericht",
            disabled=not (config["connected"] and result and result["article"] and not result["blocked"]),
        ):
            try:
                with st.spinner("Facebook controleert opnieuw en plaatst het bericht…"):
                    outcome = publish_preview()
                if outcome["status"] == "geplaatst":
                    st.session_state.pop("facebook_blog_preview", None)
                    st.success("Bericht geplaatst op Facebook.")
                    st.link_button("Open geplaatst Facebook-bericht", outcome["permalink"])
                elif outcome["status"] == "vandaag_verwerkt":
                    st.warning("De automatische planning heeft vandaag al een bericht verwerkt.")
                elif outcome["status"] == "geen_artikel":
                    st.info("Er is geen ontbrekend artikel meer om te delen.")
                else:
                    st.error("Plaatsing is niet bevestigd. Controleer Historie en Facebook voordat je opnieuw probeert.")
            except Exception as exc:
                st.error(str(exc) if isinstance(exc, (MetaError, ValueError)) else
                         "Plaatsing niet voltooid. Controleer verbinding en historie.")
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
                    "Bij plaatsing wordt dit als groot fotobericht geplaatst, met de volledige "
                    "tekst en een klikbare link naar het blogartikel."
                )
            else:
                st.info("Er is op dit moment geen ontbrekend artikel om te delen.")
    with connection:
        st.subheader("Veilig koppelen")
        st.write(f"Vaste bestemming: **Weldingshop blog post**, pagina-ID **{PAGE_ID}**.")
        feedback = st.session_state.pop("facebook_oauth_feedback", None)
        if feedback:
            getattr(st, feedback[0])(feedback[1])
        st.info(
            "Kies hieronder Koppel met Facebook en log in met een beheerder van de pagina. "
            "Je hoeft geen advertentie te maken of te kopen. De PIM vraagt alleen de rechten "
            "om deze vaste pagina te lezen en berichten te plaatsen."
        )
        try:
            oauth_url = st.session_state.get("facebook_oauth_url")
            if not oauth_url:
                oauth_url = begin_oauth_connection()
                st.session_state["facebook_oauth_url"] = oauth_url
            st.link_button("Koppel met Facebook", oauth_url, type="primary")
        except ValueError as exc:
            st.warning(str(exc))
        st.caption(
            "Na toestemming keert Meta terug naar deze PIM. De PIM bewaart alleen het "
            "benodigde paginatoken versleuteld; je wachtwoord en toegangstoken verschijnen niet in de historie."
        )
        if config["checked_at"]:
            st.caption("Verbinding laatst gecontroleerd: " + config["checked_at"])
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
                "Nederlandse kalenderdag. Handmatig kun je meerdere berichten plaatsen. "
                "Als alles al gedeeld is, wordt niets geplaatst. "
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
