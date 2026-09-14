from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import streamlit as st


UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "data" / "shared_files"


def supplier_directory(slug: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", slug):
        raise ValueError("Ongeldige leverancier.")
    return UPLOAD_ROOT / slug


def save_shared_file(slug: str, name: str, content: bytes, note: str) -> dict:
    if not content:
        raise ValueError("Het bestand is leeg.")
    if len(content) > 50 * 1024 * 1024:
        raise ValueError("Het bestand mag maximaal 50 MB zijn.")
    filename = re.sub(r"[^\w. ()-]", "_", name.replace("\\", "/").split("/")[-1])[:180]
    if filename in {"", ".", ".."}:
        filename = "bestand"
    folder = supplier_directory(slug) / uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    payload_folder = folder / "file"
    payload_folder.mkdir()
    path = payload_folder / filename
    path.write_bytes(content)
    metadata = {
        "name": filename,
        "supplier": slug,
        "size": len(content),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "note": note.strip(),
        "path": str(path),
    }
    # Publish metadata last so incomplete uploads never appear in the list.
    (folder / "upload.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def list_shared_files(slug: str) -> list[dict]:
    records = []
    for path in supplier_directory(slug).glob("*/upload.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if Path(record["path"]).is_file():
                records.append(record)
        except (OSError, ValueError, KeyError):
            continue
    return sorted(records, key=lambda item: item["uploaded_at"], reverse=True)


def render_shared_files(slug: str) -> None:
    with st.expander("Bestand delen voor bespreking", expanded=False):
        st.caption(
            "Upload hier een bestand voor deze leverancier en voeg eventueel je vraag toe. "
            "Het wordt bewaard om samen te bekijken; dit start geen productimport. "
            "Laat na het opslaan in de chat weten welk bestand je hebt gedeeld."
        )
        with st.form(f"shared_file_form_{slug}", clear_on_submit=True):
            upload = st.file_uploader(
                "Kies een bestand of sleep het hierheen (maximaal 50 MB)",
                key=f"shared_file_upload_{slug}",
            )
            note = st.text_area("Vraag of toelichting", key=f"shared_file_note_{slug}")
            submitted = st.form_submit_button("Bestand delen")
        if submitted:
            if upload is None:
                st.warning("Kies eerst een bestand.")
            else:
                try:
                    record = save_shared_file(slug, upload.name, upload.getvalue(), note)
                    st.success(f"Opgeslagen: {record['name']}. Je kunt dit bestand nu in de chat noemen.")
                except (OSError, ValueError) as exc:
                    st.error(f"Bestand kon niet worden opgeslagen: {exc}")
        records = list_shared_files(slug)
        if records:
            st.markdown("**Gedeelde bestanden**")
            st.dataframe([
                {
                    "Bestand": item["name"],
                    "Geüpload (UTC)": item["uploaded_at"][:19].replace("T", " "),
                    "Grootte (KB)": round(item["size"] / 1024, 1),
                    "Vraag of toelichting": item["note"],
                }
                for item in records
            ], hide_index=True, width="stretch")
