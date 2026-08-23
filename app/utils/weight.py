def to_kg_from_grams(value):
    if value is None or value == "":
        return None

    try:
        grams = float(str(value).replace(",", ".").strip())
    except Exception:
        return None

    if grams <= 0:
        return None

    return grams / 1000


def format_weight(value):
    if value is None or value == "":
        return "-"

    try:
        kg = float(value)
    except Exception:
        return str(value)

    if kg <= 0:
        return "-"

    if kg < 1:
        return f"{kg * 1000:.0f} g".replace(".", ",")

    return f"{kg:.2f} kg".replace(".", ",")