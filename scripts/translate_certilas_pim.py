from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.ai.providers.openai_provider import OpenAIProvider
from app.suppliers.hub import supplier_database_path, utc_now


FOREIGN_MARKERS = re.compile(
    r"\b(the|and|with|for|welding|steel|wire|suitable|applications|properties|"
    r"approval|excellent|higher|resistant|hardfacing|deposit|alloy)\b",
    re.IGNORECASE,
)


def text_only(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def backup_database(source: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = source.with_name(f"{source.stem}-before-pim-translation-{stamp}.sqlite")
    with sqlite3.connect(source) as current, sqlite3.connect(destination) as backup:
        current.backup(backup)
    return destination


def batches(items: list[tuple[int, str]], max_chars: int = 45000):
    batch: list[tuple[int, str]] = []
    length = 0
    for item in items:
        if batch and length + len(item[1]) > max_chars:
            yield batch
            batch, length = [], 0
        batch.append(item)
        length += len(item[1])
    if batch:
        yield batch


def translate_batch(client, model: str, batch: list[tuple[int, str]]) -> dict[int, str]:
    payload = [{"id": item_id, "html": html} for item_id, html in batch]
    prompt = f"""Vertaal de onderstaande bestaande PIM-productomschrijvingen naar
natuurlijk, technisch correct Nederlands. Gebruik geen webzoekfunctie en voeg geen
nieuwe productfeiten toe. Behoud de HTML-structuur. Laat merknamen, artikelnummers,
chemische symbolen, materiaalsoorten, lasposities en officiële norm- en
classificatiecodes zoals EN ISO, AWS en DIN exact ongewijzigd. Vertaal uitsluitend
beschrijvende tekst. Als een tekst al volledig Nederlands is, geef hem ongewijzigd terug.
Vertaal ook iedere Engelse toepassingslijst en iedere volledige technische Engelse zin.
Behandel zinnen zoals 'Nickel iron wire', 'Suitable for', 'Fire gratings' en
'Blast furnace bells' niet als productnamen maar vertaal ze naar begrijpelijk Nederlands.
Antwoord uitsluitend als een geldige JSON-array met objecten id en html, één voor ieder
invoerobject en in dezelfde volgorde.

INVOER:
{json.dumps(payload, ensure_ascii=False)}"""
    response = client.responses.create(model=model, input=prompt)
    output = response.output_text.strip()
    if output.startswith("```"):
        output = output.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    translated = json.loads(output)
    result = {int(item["id"]): str(item["html"]).strip() for item in translated}
    expected = {item_id for item_id, _ in batch}
    if set(result) != expected or any(not result[item_id] for item_id in expected):
        raise ValueError("AI gaf geen complete vertaalbatch terug.")
    return result


def run(*, apply: bool, selected_skus: list[str] | None = None) -> dict:
    path = supplier_database_path("certilas")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT sku,html_description,raw_data_json FROM products
               WHERE TRIM(COALESCE(html_description,''))<>''"""
        ).fetchall()
    selected = {sku.strip().upper() for sku in (selected_skus or []) if sku.strip()}
    selected_html = {
        row["html_description"] for row in rows if row["sku"].upper() in selected
    }
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        html = row["html_description"]
        if (selected_html and html in selected_html) or (
            not selected_html and FOREIGN_MARKERS.search(text_only(html))
        ):
            grouped.setdefault(html, []).append(row)
    summary = {
        "candidate_skus": sum(len(items) for items in grouped.values()),
        "unique_descriptions": len(grouped),
        "updated_skus": 0,
        "backup": "",
    }
    if not apply or not grouped:
        return summary
    backup = backup_database(path)
    summary["backup"] = str(backup)
    provider = OpenAIProvider()
    client = provider.client.with_options(timeout=180.0, max_retries=2)
    model = "gpt-5.4-mini"
    indexed = list(enumerate(grouped, start=1))
    translations: dict[str, str] = {}
    for batch_number, batch in enumerate(batches(indexed), start=1):
        result = translate_batch(client, model, batch)
        for item_id, original in batch:
            translations[original] = result[item_id]
        print(json.dumps({
            "batch": batch_number,
            "translated_unique": len(translations),
            "total_unique": len(indexed),
        }), flush=True)
    with sqlite3.connect(path) as connection:
        for original, translated in translations.items():
            for row in grouped[original]:
                raw = json.loads(row["raw_data_json"] or "{}")
                raw["pim_translation"] = {
                    "language": "nl",
                    "translated_at": utc_now(),
                    "source": "existing_pim_html",
                    "web_research_used": False,
                }
                connection.execute(
                    """UPDATE products SET html_description=?,raw_data_json=?,updated_at=?
                       WHERE sku=?""",
                    (translated, json.dumps(raw, ensure_ascii=False), utc_now(), row["sku"]),
                )
                summary["updated_skus"] += 1
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sku", action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(run(apply=args.apply, selected_skus=args.sku), ensure_ascii=False), flush=True)
