from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

import app.core.config  # loads config/settings.env without exposing credentials


HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
PART_TYPES = {"box", "cylinder", "beam"}


def _number(value: Any, name: str, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is geen getal.") from exc
    # Accept harmless floating-point rounding at a boundary. A genuinely
    # invalid coordinate is sent back to the vision model for one repair
    # attempt by generate_photo_model().
    epsilon = 1e-6
    if result < low and result >= low - epsilon:
        result = low
    elif result > high and result <= high + epsilon:
        result = high
    if not low <= result <= high:
        raise ValueError(f"{name} ({result:g}) valt buiten {low}–{high}.")
    return round(result, 6)


def _confidence(value: Any) -> float:
    """Accept confidence as a 0–1 fraction or a 0–100 percentage."""
    explicit_percent = False
    if isinstance(value, str):
        raw = value.strip().replace(",", ".")
        explicit_percent = raw.endswith("%")
        if explicit_percent:
            raw = raw[:-1].strip()
        value = raw
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence is geen getal.") from exc
    if explicit_percent or result > 1:
        result /= 100
    return _number(result, "confidence", 0, 1)


def validate_photo_model(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("AI-modelantwoord is geen JSON-object.")
    parts = value.get("parts")
    if not isinstance(parts, list) or not 8 <= len(parts) <= 120:
        raise ValueError("Een fotomodel moet 8 tot en met 120 geometrische delen bevatten.")
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(parts, 1):
        if not isinstance(source, dict) or source.get("type") not in PART_TYPES:
            raise ValueError(f"Geometriedel {index} heeft geen toegestaan type.")
        kind = str(source["type"])
        color = str(source.get("color") or "")
        if not HEX_COLOR.fullmatch(color):
            raise ValueError(f"Geometriedel {index} heeft geen geldige hexkleur.")
        part: dict[str, Any] = {"type": kind, "color": color.upper()}
        if kind == "box":
            for key, low, high in (
                ("x", -0.75, 0.75), ("y", 0.0, 1.25), ("z", -0.75, 0.75),
                ("w", 0.005, 1.5), ("h", 0.005, 1.5), ("d", 0.005, 1.5),
                ("rx", -3.142, 3.142), ("ry", -3.142, 3.142), ("rz", -3.142, 3.142),
            ):
                part[key] = _number(source.get(key, 0), f"deel {index}.{key}", low, high)
        elif kind == "cylinder":
            for key, low, high in (
                ("x", -0.75, 0.75), ("y", 0.0, 1.25), ("z", -0.75, 0.75),
                ("r", 0.003, 0.75), ("h", 0.005, 1.5),
                ("rx", -3.142, 3.142), ("ry", -3.142, 3.142), ("rz", -3.142, 3.142),
            ):
                part[key] = _number(source.get(key, 0), f"deel {index}.{key}", low, high)
        else:
            for key, low, high in (
                ("ax", -0.75, 0.75), ("ay", 0.0, 1.25), ("az", -0.75, 0.75),
                ("bx", -0.75, 0.75), ("by", 0.0, 1.25), ("bz", -0.75, 0.75),
                ("r", 0.002, 0.25),
            ):
                part[key] = _number(source.get(key, 0), f"deel {index}.{key}", low, high)
        normalized.append(part)
    confidence = _confidence(value.get("confidence", 0))
    if confidence < 0.7:
        raise ValueError(
            f"De AI-fotomatch is onvoldoende betrouwbaar ({confidence:.0%}); actie vereist."
        )
    return {
        "schemaVersion": 1,
        "profile": str(value.get("profile") or "photo-model").strip()[:100],
        "confidence": confidence,
        "notes": str(value.get("notes") or "").strip()[:500],
        "parts": normalized,
    }


def generate_photo_model(record: dict[str, Any], *, reason: str, notes: str = "") -> dict[str, Any]:
    image_url = str(record.get("image") or "").strip()
    if not image_url.startswith("https://"):
        raise ValueError("Het Shopify-product heeft geen bruikbare HTTPS-productfoto.")
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ontbreekt in config/settings.env.")
    prompt = f"""
Je bouwt één herkenbaar maar efficiënt 3D-fotomodel voor een werkplaatsconfigurator.
Analyseer uitsluitend het afgebeelde product; negeer witte achtergrond, tekst, logo's en maatlijnen.
Gebruik 12–80 eenvoudige delen. Coördinaten zijn genormaliseerd: x/z -0.5..0.5, y 0..1.
Gebruik NOOIT centimeters, millimeters, pixels, percentages of gehele productmaten als coördinaten.
Elke positie en afmeting moet een JSON-getal zijn binnen de hieronder beschreven grenzen.
De onderzijde moet de vloer raken. Voorzijde van het product kijkt naar +z.
Gebruik box voor kasten/platen, cylinder voor wielen/assen/tanks en beam voor schuine stangen/slangen.
Leg opvallende kleurvlakken, poten, wielen, masten, armen, kappen, bedieningskasten en handgrepen vast.
Geef uitsluitend JSON terug met profile, confidence, notes en parts.
Iedere part heeft type en color (#RRGGBB).
box: x,y,z,w,h,d,rx,ry,rz. cylinder: x,y,z,r,h,rx,ry,rz.
beam: ax,ay,az,bx,by,bz,r. Rotaties zijn radialen.

Artikel: {record.get('title')}
SKU: {record.get('code')}
Categorie: {record.get('category')}
Bestaand type: {record.get('modelKind')}
Buitenmaat: {record.get('width')} × {record.get('depth')} × {record.get('height')} cm
Reden verzoek: {reason}
Toelichting: {notes or 'geen'}
""".strip()
    client = OpenAI(api_key=api_key)
    repair_instruction = ""
    last_error: Exception | None = None
    for attempt in range(2):
        response = client.responses.create(
            model=model,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt + repair_instruction},
                    {"type": "input_image", "image_url": image_url, "detail": "high"},
                ],
            }],
            text={"format": {"type": "json_object"}},
        )
        try:
            result = json.loads(response.output_text)
            return validate_photo_model(result)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            last_error = exc
            if attempt:
                break
            # Repeat the complete vision pass with the exact validator error.
            # This repairs occasional centimetre or percentage coordinates
            # without silently clipping a structurally wrong model.
            previous = str(response.output_text or "")[:12_000]
            repair_instruction = f"""

CORRECTIE VEREIST. De vorige JSON is afgekeurd: {exc}
Maak de volledige JSON opnieuw. Gebruik uitsluitend genormaliseerde getallen:
x/z/ax/az/bx/bz -0.75..0.75; y/ay/by 0..1.25; confidence 0..1.
Gebruik geen centimeters, millimeters, percentages of verwijzingen naar andere velden.
Vorige afgekeurde JSON:
{previous}
"""
    raise ValueError(
        "De AI kon na een automatische correctiepoging geen geldig fotomodel maken: "
        + str(last_error or "onbekende validatiefout")
    )
