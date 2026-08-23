import html
import re
from typing import Any

from .base import SupplierQualityPolicy


# Kentie-producten mogen met één goedgekeurde afbeelding worden gepubliceerd.
# Onvolledige producten worden afzonderlijk overgeslagen, zodat zij een
# leveranciersbrede synchronisatie niet blokkeren.
class KentieQualityPolicy(SupplierQualityPolicy):
    _INTERNAL_MATCH_TEXT = re.compile(
        r"(?:geen exacte match|exacte match (?:niet|kan niet|kon niet)|"
        r"niet letterlijk (?:aangetroffen|bevestigd|aantoonbaar)|"
        r"beschikbare offici[eë]le kentie-bron|"
        r"artikelnummer[^.]{0,100}niet letterlijk)",
        re.I,
    )

    def description_is_complete(self, description: str) -> bool:
        # Shopify decodeert HTML-entiteiten tijdens productSet. Meet daarom
        # vóór en na upload op dezelfde genormaliseerde tekstlengte.
        value = html.unescape(str(description or "").strip())
        return (
            len(value) >= self.description_minimum
            and "<" in value
            and not self._INTERNAL_MATCH_TEXT.search(value)
        )

    def validation_errors(
        self, product: dict[str, Any], description: str, tags: list[str],
    ) -> list[str]:
        title = str(
            product.get("ai_title") or product.get("source_title") or ""
        ).strip()
        if re.search(
            r"\b(onbevestigd|niet bevestigd|geen bevestigde match|unknown)\b",
            title, re.I,
        ):
            return ["placeholdertitel is niet toegestaan"]
        if self._INTERNAL_MATCH_TEXT.search(html.unescape(str(description or ""))):
            return ["interne of onbevestigde bronmatchtekst is niet toegestaan"]
        return []


POLICY = KentieQualityPolicy(
    # Kentie: één echte productfoto en twee inhoudelijk unieke tags volstaan.
    # Een onvolledig artikel blijft individueel Concept en blokkeert de rest
    # van de leverancierssynchronisatie niet.
    description_minimum=80, tag_minimum=2, image_minimum=1,
    isolate_incomplete_content=True,
)
