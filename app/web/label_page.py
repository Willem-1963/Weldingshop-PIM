from __future__ import annotations

import html
import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from app.suppliers.hub import get_supplier_product, list_products, list_suppliers
from app.shopify.client import ShopifyClient
from app.web.label_store import (
    delete_template,
    get_active_template,
    get_last_settings,
    get_template,
    get_workstation,
    list_templates,
    list_workstations,
    save_template,
    save_active_template,
    save_last_settings,
    save_workstation,
)


LABEL_FORMATS = {
    "4 × 6 inch — liggend": ("6in", "4in"),
    "DYMO 11354 — 57 × 32 mm": ("57mm", "32mm"),
}

FIELD_OPTIONS = {
    "Productnaam": "title",
    "SKU": "sku",
    "Barcode / EAN": "ean",
    "Verkoopprijs": "price",
    "Merk": "brand",
    "Leverancier": "supplier",
    "Categorie": "category",
    "Producttype": "product_type",
    "Gewicht": "weight",
    "Locatiecode (custom.locatie)": "custom_location",
}

TEXT_SIZES = {
    "Klein — 9 pt": 9,
    "Normaal — 12 pt": 12,
    "Groot — 18 pt": 18,
    "Extra groot — 28 pt": 28,
    "Kop — 40 pt": 40,
}

DISPLAY_OPTIONS = ["1 regel", "2 regels", "3 regels", "Barcode"]

# Code 39: narrow/wide sequences for 9 bars and spaces. It is intentionally
# generated locally, so printing does not depend on a font, CDN or internet.
CODE39 = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw",
    "3": "wnwwnnnnn", "4": "nnnwwnnnw", "5": "wnnwwnnnn",
    "6": "nnwwwnnnn", "7": "nnnwnnwnw", "8": "wnnwnnwnn",
    "9": "nnwwnnwnn", "A": "wnnnnwnnw", "B": "nnwnnwnnw",
    "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn",
    "F": "nnwnwwnnn", "G": "nnnnnwwnw", "H": "wnnnnwwnn",
    "I": "nnwnnwwnn", "J": "nnnnwwwnn", "K": "wnnnnnnww",
    "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww",
    "O": "wnnnwnnwn", "P": "nnwnwnnwn", "Q": "nnnnnnwww",
    "R": "wnnnnnwwn", "S": "nnwnnnwwn", "T": "nnnnwnwwn",
    "U": "wwnnnnnnw", "V": "nwwnnnnnw", "W": "wwwnnnnnn",
    "X": "nwnnwnnnw", "Y": "wwnnwnnnn", "Z": "nwwnwnnnn",
    "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn",
    "$": "nwnwnwnnn", "/": "nwnwnnnwn", "+": "nwnnnwnwn",
    "%": "nnnwnwnwn", "*": "nwnnwnwnn",
}


@dataclass(frozen=True)
class FieldSetting:
    field: str
    size: int
    display: str
    position: int = 0


def _product_title(product: dict[str, Any]) -> str:
    return str(
        product.get("ai_title")
        or product.get("source_title")
        or product.get("source_description")
        or product.get("sku")
        or ""
    ).strip()


def _format_price(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"€ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(value)


def product_values(product: dict[str, Any]) -> dict[str, str]:
    weight = product.get("weight_grams")
    weight_text = "" if weight in (None, "") else f"{weight:g} g" if isinstance(weight, (int, float)) else f"{weight} g"
    return {
        "title": _product_title(product),
        "sku": str(product.get("sku") or ""),
        "ean": str(product.get("ean") or ""),
        "price": _format_price(product.get("sale_price") or product.get("price")),
        "brand": str(product.get("brand") or product.get("vendor") or ""),
        "supplier": str(product.get("supplier") or ""),
        "category": str(product.get("category_full") or product.get("category") or ""),
        "product_type": str(product.get("product_type") or ""),
        "weight": weight_text,
        "custom_location": str(product.get("custom_location") or ""),
    }


def _weldingshop_shopify_location(client: ShopifyClient) -> dict[str, Any]:
    locations = (client.shop_and_locations().get("locations") or {}).get("nodes") or []
    exact = [item for item in locations if str(item.get("name") or "").strip().casefold() == "weldingshop"]
    if len(exact) != 1:
        raise RuntimeError(
            f"Shopify-locatie ‘Weldingshop’ niet eenduidig gevonden ({len(exact)} resultaten)."
        )
    return exact[0]


@st.cache_data(ttl=60, show_spinner=False)
def shopify_label_values_for_sku(sku: str) -> dict[str, Any]:
    """Lees locatiecode en actuele beschikbare voorraad bij Weldingshop."""
    escaped = sku.replace("\\", "\\\\").replace('"', '\\"')
    client = ShopifyClient.from_settings()
    shop_location = _weldingshop_shopify_location(client)
    data = client.graphql(
        """
        query LabelValuesBySku($query:String!,$locationId:ID!){
          productVariants(first:10,query:$query){nodes{
            id sku barcode
            inventoryItem{
              id tracked
              inventoryLevel(locationId:$locationId){
                quantities(names:["available"]){name quantity}
              }
            }
            product{metafield(namespace:"custom",key:"locatie"){value}}
          }}
        }
        """,
        {"query": f'sku:"{escaped}"', "locationId": shop_location["id"]},
    )
    exact = [
        node for node in (data.get("productVariants") or {}).get("nodes") or []
        if str(node.get("sku") or "").strip().upper() == sku.strip().upper()
    ]
    if len(exact) != 1:
        raise ValueError(f"SKU {sku} is niet exact één keer in Shopify gevonden.")
    variant = exact[0]
    metafield = (exact[0].get("product") or {}).get("metafield") or {}
    inventory_item = variant.get("inventoryItem") or {}
    level = inventory_item.get("inventoryLevel") or {}
    quantity = next((
        int(item.get("quantity") or 0)
        for item in level.get("quantities") or []
        if item.get("name") == "available"
    ), 0)
    return {
        "custom_location": str(metafield.get("value") or "").strip(),
        "ean": str(variant.get("barcode") or "").strip(),
        "variant_id": str(variant.get("id") or ""),
        "inventory_quantity": quantity,
        "inventory_item_id": str(inventory_item.get("id") or ""),
        "inventory_active": bool(level),
        "shopify_location_id": str(shop_location["id"]),
    }


def shopify_location_for_sku(sku: str) -> str:
    return str(shopify_label_values_for_sku(sku).get("custom_location") or "")


def _ean13_check_digit(first_twelve: str) -> str:
    if len(first_twelve) != 12 or not first_twelve.isdigit():
        raise ValueError("Voor een EAN-13 zijn eerst precies 12 cijfers nodig.")
    weighted_sum = sum(
        int(character) * (1 if index % 2 == 0 else 3)
        for index, character in enumerate(first_twelve)
    )
    return str((-weighted_sum) % 10)


def _valid_ean(value: str) -> bool:
    return (
        len(value) == 13
        and value.isdigit()
        and value[-1] == _ean13_check_digit(value[:12])
    )


def generate_unique_ean() -> str:
    """Genereer een ongebruikte interne EAN-13 in het bereik 29."""
    client = ShopifyClient.from_settings()
    for _attempt in range(50):
        first_twelve = f"29{secrets.randbelow(10**10):010d}"
        ean = first_twelve + _ean13_check_digit(first_twelve)
        escaped = ean.replace('"', '\\"')
        data = client.graphql(
            """
            query ExistingLabelBarcode($query:String!){
              productVariants(first:1,query:$query){nodes{id}}
            }
            """,
            {"query": f'barcode:"{escaped}"'},
        )
        if not (data.get("productVariants") or {}).get("nodes"):
            return ean
    raise RuntimeError("Kon na 50 pogingen geen unieke EAN-code genereren.")


def _generate_ean_for_widget(widget_key: str) -> None:
    """Form-callback: zet de nieuwe EAN vóór de volgende Streamlit-render."""
    st.session_state[widget_key] = generate_unique_ean()


def save_shopify_barcode_for_sku(sku: str, ean: str) -> str:
    """Bewaar een EAN op precies één Shopify-variant."""
    clean_sku = sku.strip()
    clean_ean = ean.strip().replace(" ", "")
    if not clean_sku:
        raise ValueError("SKU is verplicht.")
    if clean_ean and not _valid_ean(clean_ean):
        raise ValueError("Barcode-EAN moet een geldige EAN-13 met controlecijfer zijn.")
    escaped = clean_sku.replace("\\", "\\\\").replace('"', '\\"')
    client = ShopifyClient.from_settings()
    data = client.graphql(
        """
        query LabelBarcodeOwner($query:String!){
          productVariants(first:10,query:$query){nodes{id sku barcode product{id}}}
        }
        """,
        {"query": f'sku:"{escaped}"'},
    )
    exact = [
        node for node in (data.get("productVariants") or {}).get("nodes") or []
        if str(node.get("sku") or "").strip().upper() == clean_sku.upper()
    ]
    if len(exact) != 1:
        raise ValueError(f"SKU {clean_sku} is niet exact één keer in Shopify gevonden.")
    variant = exact[0]
    current = str(variant.get("barcode") or "").strip()
    if current == clean_ean:
        return current
    product_id = str((variant.get("product") or {}).get("id") or "")
    variant_id = str(variant.get("id") or "")
    if not product_id or not variant_id:
        raise RuntimeError(f"Shopify-ID ontbreekt voor SKU {clean_sku}.")
    result = client.graphql(
        """
        mutation SaveLabelBarcode($productId:ID!,$variants:[ProductVariantsBulkInput!]!){
          productVariantsBulkUpdate(productId:$productId,variants:$variants){
            productVariants{id sku barcode}
            userErrors{field message code}
          }
        }
        """,
        {"productId": product_id, "variants": [{"id": variant_id, "barcode": clean_ean}]},
    )
    payload = result.get("productVariantsBulkUpdate") or {}
    if payload.get("userErrors"):
        raise RuntimeError(
            "; ".join(str(error.get("message") or error) for error in payload["userErrors"])
        )
    saved = next((
        str(item.get("barcode") or "").strip()
        for item in payload.get("productVariants") or []
        if str(item.get("id") or "") == variant_id
    ), "")
    if saved != clean_ean:
        raise RuntimeError("Shopify bevestigde de nieuwe Barcode-EAN niet.")
    shopify_label_values_for_sku.clear()
    return saved


def save_shopify_location_for_sku(sku: str, location: str) -> str:
    """Update only custom.locatie on the exact Shopify product for ``sku``."""
    clean_sku = sku.strip()
    clean_location = location.strip()
    if not clean_sku:
        raise ValueError("SKU is verplicht.")
    escaped = clean_sku.replace("\\", "\\\\").replace('"', '\\"')
    client = ShopifyClient.from_settings()
    data = client.graphql(
        """
        query LabelLocationOwner($query:String!){
          productVariants(first:10,query:$query){nodes{sku product{id}}}
        }
        """,
        {"query": f'sku:"{escaped}"'},
    )
    exact = [
        node for node in (data.get("productVariants") or {}).get("nodes") or []
        if str(node.get("sku") or "").strip().upper() == clean_sku.upper()
    ]
    if not exact:
        raise ValueError(f"SKU {clean_sku} is niet in Shopify gevonden.")
    product_ids = {
        str((node.get("product") or {}).get("id") or "") for node in exact
    } - {""}
    if len(product_ids) != 1:
        raise RuntimeError(
            f"SKU {clean_sku} verwijst niet naar precies één Shopify-product."
        )
    owner_id = next(iter(product_ids))
    if not clean_location:
        result = client.graphql(
            """
            mutation DeleteLabelLocation($metafields:[MetafieldIdentifierInput!]!){
              metafieldsDelete(metafields:$metafields){
                deletedMetafields{ownerId namespace key}
                userErrors{field message}
              }
            }
            """,
            {"metafields": [{
                "ownerId": owner_id, "namespace": "custom", "key": "locatie",
            }]},
        )
        payload = result.get("metafieldsDelete") or {}
        errors = payload.get("userErrors") or []
        if errors:
            raise RuntimeError(
                "; ".join(str(error.get("message") or error) for error in errors)
            )
        shopify_label_values_for_sku.clear()
        return ""
    result = client.graphql(
        """
        mutation SaveLabelLocation($metafields:[MetafieldsSetInput!]!){
          metafieldsSet(metafields:$metafields){
            metafields{namespace key value}
            userErrors{field message code}
          }
        }
        """,
        {
            "metafields": [{
                "ownerId": owner_id,
                "namespace": "custom",
                "key": "locatie",
                "type": "single_line_text_field",
                "value": clean_location,
            }]
        },
    )
    payload = result.get("metafieldsSet") or {}
    errors = payload.get("userErrors") or []
    if errors:
        raise RuntimeError("; ".join(str(error.get("message") or error) for error in errors))
    saved = next(
        (
            str(item.get("value") or "").strip()
            for item in payload.get("metafields") or []
            if item.get("namespace") == "custom" and item.get("key") == "locatie"
        ),
        "",
    )
    if saved != clean_location:
        raise RuntimeError("Shopify bevestigde de nieuwe locatiecode niet.")
    shopify_label_values_for_sku.clear()
    return saved


def save_shopify_label_values_for_sku(
    sku: str, location: str, quantity: int, ean: str | None = None,
) -> dict[str, Any]:
    """Bewaar locatiecode, EAN en absolute voorraad bij Weldingshop."""
    clean_quantity = int(quantity)
    if clean_quantity < 0:
        raise ValueError("Voorraad kan niet negatief zijn.")
    clean_ean = None if ean is None else ean.strip().replace(" ", "")
    if clean_ean and not _valid_ean(clean_ean):
        # Valideer vóór locatie of voorraad wordt gewijzigd: een invoerfout mag
        # nooit een half opgeslagen formulier achterlaten.
        raise ValueError("Barcode-EAN moet een geldige EAN-13 met controlecijfer zijn.")
    current = shopify_label_values_for_sku(sku)
    saved_location = save_shopify_location_for_sku(sku, location)
    saved_ean = current.get("ean", "")
    if clean_ean is not None and clean_ean != str(saved_ean or "").strip():
        saved_ean = save_shopify_barcode_for_sku(sku, clean_ean)
    client = ShopifyClient.from_settings()
    inventory_item_id = current["inventory_item_id"]
    shopify_location_id = current["shopify_location_id"]
    if not inventory_item_id:
        raise RuntimeError(f"SKU {sku} heeft geen Shopify inventory item.")
    if not current["inventory_active"]:
        activation = client.graphql(
            """
            mutation ActivateLabelInventory(
              $inventoryItemId:ID!,
              $inventoryItemUpdates:[InventoryBulkToggleActivationInput!]!
            ){
              inventoryBulkToggleActivation(
                inventoryItemId:$inventoryItemId,
                inventoryItemUpdates:$inventoryItemUpdates
              ){userErrors{field message code}}
            }
            """,
            {"inventoryItemId": inventory_item_id, "inventoryItemUpdates": [{
                "locationId": shopify_location_id, "activate": True,
            }]},
        )["inventoryBulkToggleActivation"]
        if activation.get("userErrors"):
            raise RuntimeError(json.dumps(activation["userErrors"], ensure_ascii=False))
    payload = client.graphql(
        """
        mutation SaveLabelInventory(
          $input:InventorySetQuantitiesInput!,$idempotencyKey:String!
        ){
          inventorySetQuantities(input:$input) @idempotent(key:$idempotencyKey){
            userErrors{field message code}
          }
        }
        """,
        {"idempotencyKey": str(uuid.uuid4()), "input": {
            "name": "available", "reason": "correction",
            "referenceDocumentUri": f"pim://weldingshop/labels/{sku}",
            "quantities": [{
                "inventoryItemId": inventory_item_id,
                "locationId": shopify_location_id,
                "quantity": clean_quantity,
                "changeFromQuantity": current["inventory_quantity"],
            }],
        }},
    )["inventorySetQuantities"]
    if payload.get("userErrors"):
        raise RuntimeError(json.dumps(payload["userErrors"], ensure_ascii=False))
    shopify_label_values_for_sku.clear()
    return {
        "custom_location": saved_location,
        "inventory_quantity": clean_quantity,
        "ean": saved_ean,
    }


def code39_svg(value: str) -> str:
    clean = "".join(character for character in value.upper() if character in CODE39 and character != "*")
    if not clean:
        return ""
    encoded = f"*{clean}*"
    narrow, wide, gap, height = 2, 5, 2, 54
    x = 8
    bars: list[str] = []
    for character in encoded:
        for index, width_code in enumerate(CODE39[character]):
            width = wide if width_code == "w" else narrow
            if index % 2 == 0:
                bars.append(f'<rect x="{x}" y="2" width="{width}" height="{height}"/>')
            x += width
        x += gap
    svg_width = x + 6
    return (
        f'<svg class="barcode" viewBox="0 0 {svg_width} 72" role="img" '
        f'aria-label="Barcode {html.escape(clean)}" preserveAspectRatio="none">'
        f'<g fill="#000">{"".join(bars)}</g>'
        f'<text x="{svg_width / 2}" y="69" text-anchor="middle" '
        f'font-family="Arial,sans-serif" font-size="11">{html.escape(clean)}</text></svg>'
    )


def build_label_document(
    product: dict[str, Any],
    settings: list[FieldSetting],
    label_format: str,
    quantity: int,
) -> str:
    width, height = LABEL_FORMATS[label_format]
    values = product_values(product)
    blocks: list[str] = []
    for setting in sorted(settings, key=lambda item: item.position):
        value = values.get(setting.field, "")
        if not value:
            continue
        if setting.display == "Barcode":
            rendered = code39_svg(value)
            if rendered:
                blocks.append(f'<div class="field barcode-field">{rendered}</div>')
            continue
        lines = int(setting.display.split()[0])
        display_value = (
            f"Locatie: {value}" if setting.field == "custom_location" else value
        )
        blocks.append(
            f'<div class="field text-field lines-{lines}" style="font-size:{setting.size}pt">'
            f'{html.escape(display_value)}</div>'
        )
    format_class = "label-large" if label_format.startswith("4 × 6") else "label-dymo"
    label = f'<section class="label {format_class}">{"".join(blocks)}</section>'
    labels = label * max(1, min(int(quantity), 500))
    return f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><title>Labels {html.escape(values['sku'])}</title>
<style>
@page {{ size: {width} {height}; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin:0; padding:0; font-family:Arial,sans-serif; color:#111; background:#ececec; }}
.toolbar {{ position:sticky; top:0; z-index:2; display:flex; align-items:center; gap:12px;
  padding:10px 14px; background:#202124; color:white; font:14px Arial,sans-serif; }}
.toolbar button {{ border:0; border-radius:6px; padding:9px 16px; background:#f47b20;
  color:white; font-weight:700; cursor:pointer; }}
.pages {{ padding:16px; }}
.label {{ width:{width}; height:{height}; margin:0 auto 16px; overflow:hidden;
  background:white; page-break-after:always; break-after:page; }}
.label-large {{ padding:28px; display:flex; flex-direction:column;
  justify-content:center; gap:12px;
  page-break-after:always; break-after:page; }}
.label-dymo {{ padding:5px 6px; display:flex; flex-direction:column;
  justify-content:center; gap:2px;
  page-break-after:always; break-after:page; }}
.label:last-child {{ page-break-after:auto; break-after:auto; }}
.field {{ width:100%; line-height:1.08; overflow:hidden; }}
.text-field {{ flex:0 0 auto; font-weight:700; overflow-wrap:anywhere; }}
.lines-1 {{ white-space:nowrap; text-overflow:ellipsis; }}
.lines-2,.lines-3 {{ display:-webkit-box; -webkit-box-orient:vertical; }}
.lines-2 {{ -webkit-line-clamp:2; }} .lines-3 {{ -webkit-line-clamp:3; }}
.barcode-field {{ flex:0 0 auto; display:flex; align-items:center; justify-content:center; }}
.label-large .barcode-field {{ height:95px; }}
.label-dymo .barcode-field {{ height:34px; }}
.barcode {{ display:block; width:100%; height:100%; }}
@media print {{
  html,body {{ background:white; }} .toolbar {{ display:none !important; }} .pages {{ padding:0; }}
  .label {{ margin:0; }}
}}
</style></head><body>
<div class="toolbar"><button onclick="window.print()">Printer selecteren en afdrukken</button>
<span>{quantity} label(s) · {html.escape(label_format)}</span></div>
<main class="pages">{labels}</main></body></html>"""


def _search_all_suppliers(query: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for supplier in list_suppliers():
        slug = str(supplier.get("slug") or "")
        if not slug:
            continue
        for product in list_products(slug, limit=20, query=query):
            product["supplier_slug"] = slug
            product["supplier"] = supplier.get("name") or slug
            matches.append(product)
    known_skus = {
        str(product.get("sku") or "").strip().upper() for product in matches
    }
    try:
        client = ShopifyClient.from_settings()
        escaped = query.replace("\\", "\\\\").replace('"', '\\"')
        data = client.graphql(
            """query LabelProductSearch($query:String!){
            products(first:30,query:$query){nodes{
              title vendor productType
              variants(first:100){nodes{sku barcode price}}
            }}}
            """,
            {"query": escaped},
        )
        for product in (data.get("products") or {}).get("nodes") or []:
            for variant in (product.get("variants") or {}).get("nodes") or []:
                sku = str(variant.get("sku") or "").strip()
                if not sku or sku.upper() in known_skus:
                    continue
                matches.append({
                    "sku": sku,
                    "ean": str(variant.get("barcode") or ""),
                    "source_title": str(product.get("title") or sku),
                    "sale_price": variant.get("price"),
                    "brand": str(product.get("vendor") or ""),
                    "vendor": str(product.get("vendor") or ""),
                    "product_type": str(product.get("productType") or ""),
                    "supplier_slug": "",
                    "supplier": "Shopify (incidenteel)",
                })
                known_skus.add(sku.upper())
    except Exception:
        # De leveranciers-PIM blijft beschikbaar wanneer Shopify tijdelijk faalt.
        pass
    return matches[:60]


def _product_choice(product: dict[str, Any]) -> str:
    ean = f" · {product['ean']}" if product.get("ean") else ""
    return f"{product.get('sku', '—')} · {_product_title(product)}{ean} · {product.get('supplier', '')}"


def _apply_saved_settings(saved: dict[str, Any]) -> None:
    if saved.get("label_format") in LABEL_FORMATS:
        st.session_state["label_format"] = saved["label_format"]
    fields = saved.get("fields") or {}
    for field in FIELD_OPTIONS.values():
        config = fields.get(field) or {}
        st.session_state[f"label_field_{field}"] = bool(config.get("enabled"))
        size = config.get("size_label")
        display = config.get("display")
        position = config.get("position")
        if size in TEXT_SIZES:
            st.session_state[f"label_size_{field}"] = size
        if display in DISPLAY_OPTIONS:
            st.session_state[f"label_display_{field}"] = display
        if position in range(1, len(FIELD_OPTIONS) + 1):
            st.session_state[f"label_position_{field}"] = position


def _clear_setting_widgets() -> None:
    """Verwijder oude widgetwaarden voordat een compleet ontwerp wordt geladen."""
    st.session_state.pop("label_format", None)
    for field in FIELD_OPTIONS.values():
        for prefix in ("label_field_", "label_size_", "label_display_", "label_position_"):
            st.session_state.pop(f"{prefix}{field}", None)


def _current_settings(label_format: str) -> dict[str, Any]:
    return {
        "label_format": label_format,
        "fields": {
            field: {
                "enabled": bool(st.session_state.get(f"label_field_{field}")),
                "size_label": st.session_state.get(
                    f"label_size_{field}", "Normaal — 12 pt"
                ),
                "display": st.session_state.get(
                    f"label_display_{field}", "1 regel"
                ),
                "position": int(
                    st.session_state.get(f"label_position_{field}", position)
                ),
            }
            for position, field in enumerate(FIELD_OPTIONS.values(), start=1)
        },
    }


def _save_current_session_settings() -> None:
    """Persist a widget change immediately, also before a product is selected."""
    label_format = st.session_state.get("label_format")
    if label_format not in LABEL_FORMATS:
        return
    save_last_settings(_current_settings(label_format))


def _preserve_layout_when_print_quantity_changes() -> None:
    """Bewaar de actuele (ook nog niet opgeslagen) opmaak over de rerun heen."""
    label_format = st.session_state.get("label_format")
    if (label_format not in LABEL_FORMATS
            or "label_field_title" not in st.session_state):
        return
    pending: dict[str, Any] = {"settings": _current_settings(label_format)}
    loaded_template = str(st.session_state.get("label_loaded_template") or "").strip()
    if loaded_template:
        pending["template_name"] = loaded_template
    st.session_state["label_pending_settings"] = pending


def show_label_page(force_reload: bool = False) -> None:
    pending = st.session_state.pop("label_pending_settings", None)
    if pending:
        # Een opgeslagen ontwerp moet alle huidige widgetwaarden vervangen.
        # Zonder wissen kan Streamlit waarden uit het vorige ontwerp behouden.
        settings_to_load = pending.get("settings", pending)
        _clear_setting_widgets()
        _apply_saved_settings(settings_to_load)
        if pending.get("template_name"):
            st.session_state["label_template_name"] = pending["template_name"]
            st.session_state["label_loaded_template"] = pending["template_name"]
        st.session_state["label_last_settings_loaded"] = True
    elif (force_reload
          or not st.session_state.get("label_last_settings_loaded")
          or "label_format" not in st.session_state):
        active_template = get_active_template()
        active_settings = get_template(active_template) if active_template else None
        settings_to_load = active_settings or get_last_settings()
        if settings_to_load:
            _clear_setting_widgets()
            _apply_saved_settings(settings_to_load)
        if active_settings:
            st.session_state["label_saved_template"] = active_template
            st.session_state["label_template_name"] = active_template
            st.session_state["label_loaded_template"] = active_template
        st.session_state["label_last_settings_loaded"] = True

    st.title("Labels maken")
    st.caption("Zoek een product, stel het label samen en kies daarna de printer in het systeemvenster.")

    query_col, print_quantity_col, format_col = st.columns([2.2, 0.8, 1.2])
    with query_col:
        query = st.text_input(
            "Product zoeken",
            placeholder="SKU, barcode/EAN of een deel van de productnaam",
            key="label_search_query",
        ).strip()
    with print_quantity_col:
        quantity = st.number_input(
            "Aantal labels afdrukken",
            min_value=1,
            max_value=500,
            value=1,
            step=1,
            key="label_quantity",
            on_change=_preserve_layout_when_print_quantity_changes,
        )
    with format_col:
        label_format = st.selectbox(
            "Labelformaat", list(LABEL_FORMATS), key="label_format",
            on_change=_save_current_session_settings,
        )

    if not query:
        st.info("Vul een SKU, barcode of (deel van) de productnaam in om te beginnen.")
        return

    matches = _search_all_suppliers(query)
    if not matches:
        st.warning("Geen producten gevonden.")
        return

    selected_index = st.selectbox(
        f"Gevonden producten ({len(matches)})",
        range(len(matches)),
        format_func=lambda index: _product_choice(matches[index]),
        key="label_product_choice",
    )
    selected = matches[selected_index]
    full_product = (
        get_supplier_product(selected["supplier_slug"], selected["sku"])
        if selected.get("supplier_slug") else None
    ) or selected
    full_product["supplier"] = selected["supplier"]
    try:
        shopify_label_values = shopify_label_values_for_sku(
            str(full_product.get("sku") or "")
        )
        full_product["custom_location"] = shopify_label_values["custom_location"]
        full_product["ean"] = shopify_label_values["ean"]
        shopify_inventory_quantity = int(shopify_label_values["inventory_quantity"])
        shopify_values_error = ""
    except Exception as exc:
        # Productlabels blijven bruikbaar wanneer Shopify tijdelijk niet
        # bereikbaar is; de Shopify-velden kunnen dan niet worden opgeslagen.
        full_product["custom_location"] = ""
        shopify_inventory_quantity = 0
        shopify_values_error = str(exc)

    # Widgets in een formulier veroorzaken bij iedere klik op +/- geen volledige
    # paginarerun. Zo blijven productkeuze, ontwerp en scrollpositie stabiel.
    location_key = f"label_location_value_{full_product.get('sku', '')}"
    ean_key = f"label_ean_value_{full_product.get('sku', '')}"
    if location_key not in st.session_state:
        st.session_state[location_key] = str(full_product.get("custom_location") or "")
    if ean_key not in st.session_state:
        st.session_state[ean_key] = str(full_product.get("ean") or "")
    with st.form(f"label_product_settings_{full_product.get('sku', '')}"):
        location_col, ean_col, generate_col, quantity_col, save_col = st.columns(
            [1.5, 1.5, 0.8, 0.8, 0.8], vertical_alignment="bottom"
        )
        with location_col:
            location_input = st.text_input(
                "Locatie",
                key=location_key,
                placeholder="Vul de locatie in",
            )
        with ean_col:
            ean_input = st.text_input(
                "Barcode-EAN",
                key=ean_key,
                placeholder="Vul of genereer een EAN-13",
            )
        with generate_col:
            st.form_submit_button(
                "Genereer EAN",
                width="stretch",
                on_click=_generate_ean_for_widget,
                args=(ean_key,),
            )
        with quantity_col:
            inventory_quantity = st.number_input(
                "Aantal bij Weldingshop", min_value=0, max_value=100000,
                value=shopify_inventory_quantity, step=1,
                key=f"label_inventory_quantity_{full_product.get('sku', '')}",
            )
        with save_col:
            save_product_settings = st.form_submit_button(
                "Locatie opslaan" if shopify_values_error else "Opslaan",
                # Formulierwidgets veroorzaken pas een rerun bij submit. Een
                # disabled-status op basis van location_input blijft daardoor
                # bij een aanvankelijk leeg veld permanent hangen.
                width="stretch",
            )
        if save_product_settings:
            try:
                sku = str(full_product.get("sku") or "")
                if shopify_values_error:
                    saved_location = save_shopify_location_for_sku(sku, location_input)
                    saved_ean = save_shopify_barcode_for_sku(sku, ean_input)
                    full_product["custom_location"] = saved_location
                    full_product["ean"] = saved_ean
                    st.success(
                        "Locatie en Barcode-EAN zijn opgeslagen en staan op het label."
                    )
                    st.warning(
                        "De voorraad kon niet worden opgeslagen, omdat de Shopify-"
                        f"voorraadgegevens niet geladen konden worden: {shopify_values_error}"
                    )
                else:
                    saved = save_shopify_label_values_for_sku(
                        sku, location_input, int(inventory_quantity), ean_input,
                    )
                    full_product["custom_location"] = saved["custom_location"]
                    full_product["ean"] = saved["ean"]
                    st.success(
                        f"Locatie, Barcode-EAN {saved['ean'] or '(leeg)'} en voorraad "
                        f"{saved['inventory_quantity']} zijn opgeslagen."
                    )
                # Een zojuist opgeslagen locatie moet direct zichtbaar zijn;
                # de gebruiker hoeft het labelveld niet nogmaals apart aan te zetten.
                st.session_state["label_field_custom_location"] = bool(
                    full_product["custom_location"]
                )
                saved_location = str(full_product["custom_location"])
                saved_ean = str(full_product.get("ean") or "")
                st.session_state["label_location_saved_message"] = (
                    f"Locatie {saved_location or '(leeg)'} en Barcode-EAN "
                    f"{saved_ean or '(leeg)'} zijn opgeslagen; de labelpreview is vernieuwd."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Gegevens konden niet worden opgeslagen: {exc}")
    if shopify_values_error:
        st.warning(f"Shopify-locatie en voorraad konden niet worden geladen: {shopify_values_error}")
    saved_location_message = str(
        st.session_state.pop("label_location_saved_message", "") or ""
    )
    if saved_location_message:
        st.success(saved_location_message)

    preview_slot = st.empty()
    field_panel = st.expander("Veldinstellingen", expanded=False)
    field_panel.caption(
        "Vink velden aan en kies per veld tekstgrootte, weergave en positie. "
        "Positie 1 komt bovenaan op het label."
    )
    defaults = {
        "Productnaam": (True, "Groot — 18 pt", "2 regels"),
        "SKU": (True, "Normaal — 12 pt", "1 regel"),
        "Barcode / EAN": (True, "Normaal — 12 pt", "Barcode"),
        "Locatiecode (custom.locatie)": (True, "Normaal — 12 pt", "1 regel"),
    }
    settings: list[FieldSetting] = []
    for natural_position, (label, field) in enumerate(FIELD_OPTIONS.items(), start=1):
        default_enabled, default_size, default_display = defaults.get(
            label, (False, "Normaal — 12 pt", "1 regel")
        )
        (
            enabled_col,
            size_col,
            display_col,
            position_col,
        ) = field_panel.columns([1.3, 1, 1, 0.55])
        with enabled_col:
            enabled = st.checkbox(
                label, value=default_enabled, key=f"label_field_{field}",
                on_change=_save_current_session_settings,
            )
        with size_col:
            size_label = st.selectbox(
                f"Tekstgrootte {label}", list(TEXT_SIZES),
                index=list(TEXT_SIZES).index(default_size),
                key=f"label_size_{field}", label_visibility="collapsed",
                disabled=not enabled,
                on_change=_save_current_session_settings,
            )
        with display_col:
            display = st.selectbox(
                f"Weergave {label}", DISPLAY_OPTIONS,
                index=DISPLAY_OPTIONS.index(default_display),
                key=f"label_display_{field}", label_visibility="collapsed",
                disabled=not enabled,
                on_change=_save_current_session_settings,
            )
        with position_col:
            position = st.selectbox(
                f"Positie {label}",
                range(1, len(FIELD_OPTIONS) + 1),
                index=natural_position - 1,
                key=f"label_position_{field}",
                label_visibility="collapsed",
                disabled=not enabled,
                on_change=_save_current_session_settings,
            )
        if enabled:
            settings.append(
                FieldSetting(field, TEXT_SIZES[size_label], display, position)
            )

    if not settings:
        field_panel.warning("Selecteer minimaal één gegevensveld.")
        return

    saved_settings = _current_settings(label_format)
    save_last_settings(saved_settings)
    templates = list_templates()
    workstations = list_workstations()
    with st.expander("Labelopslag en werkplekselectie", expanded=False):
        template_col, load_col, overwrite_col, delete_col = st.columns(
            [2, 0.8, 1.1, 0.8], vertical_alignment="bottom"
        )
        with template_col:
            chosen_template = st.selectbox(
                "Opgeslagen labelontwerp",
                ["— Kies een ontwerp —", *templates],
                key="label_saved_template",
            )
        with load_col:
            if st.button(
                "Ontwerp laden",
                disabled=chosen_template.startswith("—"),
                width="stretch",
            ):
                loaded = get_template(chosen_template)
                if loaded:
                    save_active_template(chosen_template)
                    st.session_state["label_pending_settings"] = {
                        "settings": loaded,
                        "template_name": chosen_template,
                    }
                    st.rerun()
        with overwrite_col:
            if st.button(
                "Overschrijven",
                disabled=chosen_template.startswith("—"),
                width="stretch",
            ):
                save_template(chosen_template, saved_settings)
                save_last_settings(saved_settings)
                save_active_template(chosen_template)
                st.session_state["label_template_name"] = chosen_template
                st.success(f"Labelontwerp ‘{chosen_template}’ is overschreven.")
        with delete_col:
            if st.button(
                "Verwijderen",
                disabled=chosen_template.startswith("—"),
                width="stretch",
            ):
                delete_template(chosen_template)
                if get_active_template().casefold() == chosen_template.casefold():
                    save_active_template("")
                st.session_state.pop("label_saved_template", None)
                st.rerun()

        st.markdown("##### Huidig ontwerp opslaan")
        save_name_col, save_button_col = st.columns([2, 1], vertical_alignment="bottom")
        with save_name_col:
            template_name = st.text_input(
                "Naam labelontwerp",
                placeholder="Bijvoorbeeld Magazijnlabel 4×6",
                key="label_template_name",
            )
        with save_button_col:
            if st.button(
                "Ontwerp opslaan",
                type="primary",
                width="stretch",
                disabled=not template_name.strip(),
            ):
                save_template(template_name, saved_settings)
                save_last_settings(saved_settings)
                save_active_template(template_name)
                st.session_state["label_loaded_template"] = template_name.strip()
                st.success(f"Labelontwerp ‘{template_name.strip()}’ is opgeslagen.")

        st.markdown("##### Werkplek en printer")
        workstation_col, new_col, workplace_load_col = st.columns(
            [1.2, 1.4, 0.8], vertical_alignment="bottom"
        )
        with workstation_col:
            workstation_choice = st.selectbox(
                "Deze computer/werkplek",
                ["— Nieuwe werkplek —", *workstations],
                key="label_workstation_choice",
            )
        with new_col:
            new_workstation = st.text_input(
                "Naam nieuwe werkplek",
                placeholder="Bijvoorbeeld Magazijn-pc",
                disabled=not workstation_choice.startswith("—"),
            )
        active_workstation = (
            new_workstation.strip()
            if workstation_choice.startswith("—")
            else workstation_choice
        )
        with workplace_load_col:
            if st.button(
                "Werkplek laden",
                disabled=workstation_choice.startswith("—"),
                width="stretch",
            ):
                profile = get_workstation(workstation_choice)
                if profile:
                    st.session_state["label_pending_settings"] = profile["settings"]
                    st.session_state["label_printer_name"] = profile["printer_name"]
                    st.rerun()
        printer_name = st.text_input(
            "Printer voor deze werkplek",
            key="label_printer_name",
            placeholder="Bijvoorbeeld DYMO LabelWriter 450 of Zebra ZD421",
        )
        st.caption(
            "De printernaam wordt onthouden. De definitieve printerkeuze blijft "
            "in het systeemafdrukvenster plaatsvinden."
        )
        if st.button(
            "Instellingen voor deze werkplek bewaren",
            width="stretch",
            disabled=not active_workstation,
        ):
            save_workstation(active_workstation, printer_name, saved_settings)
            st.success(
                f"Werkplek ‘{active_workstation}’ en printer ‘{printer_name or 'niet ingevuld'}’ zijn opgeslagen."
            )

    positions = [setting.position for setting in settings]
    if len(positions) != len(set(positions)):
        field_panel.warning(
            "Twee gekozen velden hebben dezelfde positie. Geef ieder veld een "
            "uniek positienummer voor een voorspelbare volgorde."
        )

    if any(setting.field == "custom_location" for setting in settings) and not full_product.get("custom_location"):
        field_panel.info(
            "Voor dit product is in Shopify geen waarde gevonden in custom.locatie."
        )

    document = build_label_document(full_product, settings, label_format, int(quantity))
    with preview_slot.container():
        st.markdown("#### Afdrukvoorbeeld")
        st.caption(
            "Klik in het voorbeeld op ‘Printer selecteren en afdrukken’. Kies "
            "daar de juiste printer en schaal 100% / werkelijke grootte."
        )
        preview_height = 560 if label_format.startswith("4 × 6") else 330
        components.html(document, height=preview_height, scrolling=True)
        st.download_button(
            "Labelbestand downloaden",
            data=document.encode("utf-8"),
            file_name=f"labels-{full_product.get('sku', 'product')}.html",
            mime="text/html",
            help="Handig als de browser het afdrukvenster vanuit het voorbeeld blokkeert.",
        )
