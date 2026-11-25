from pathlib import Path
from typing import Optional

import streamlit.components.v1 as components


_FRONTEND = Path(__file__).with_name("frontend")
_component = components.declare_component(
    "live_barcode_scanner_v2",
    path=str(_FRONTEND),
)


def live_barcode_scanner(*, key: Optional[str] = None) -> Optional[str]:
    """Open de achtercamera en geef één automatisch herkende barcode terug."""
    value = _component(default=None, key=key)
    return str(value).strip() if value else None
