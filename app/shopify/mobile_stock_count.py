from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

from app.shopify.client import ShopifyClient


AUDIT_PATH = Path("logs/mobile-stock-count-audit.jsonl")


@dataclass(frozen=True)
class StockCountTarget:
    barcode: str
    product_id: str
    product_handle: str
    product_title: str
    product_status: str
    product_vendor: str
    product_image_url: str
    scanned_variant_id: str
    scanned_sku: str
    scanned_title: str
    units_per_scan: int
    target_variant_id: str
    target_sku: str
    target_inventory_item_id: str
    target_inventory_quantity: int
    shopify_location_id: str
    shopify_location_name: str
    stock_model: str


@dataclass(frozen=True)
class BarcodeLinkCandidate:
    barcode: str
    product_id: str
    product_handle: str
    product_title: str
    product_status: str
    product_vendor: str
    product_image_url: str
    variant_id: str
    variant_sku: str
    variant_title: str
    current_barcode: str
    units_per_scan: int
    target_sku: str
    stock_model: str


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _valid_gtin(value: str) -> bool:
    if not value.isdigit() or len(value) not in {8, 12, 13, 14}:
        return False
    digits = [int(char) for char in value]
    payload = digits[:-1]
    weighted = sum(
        digit * (3 if (len(payload) - index) % 2 else 1)
        for index, digit in enumerate(payload)
    )
    return (10 - weighted % 10) % 10 == digits[-1]


def decode_barcode_image(image_file: Any) -> str:
    """Decode exactly one barcode from a phone-camera image."""
    try:
        import zxingcpp
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise RuntimeError("De barcodelezer is niet geïnstalleerd op de PIM-server.") from exc
    try:
        image = ImageOps.exif_transpose(Image.open(image_file)).convert("RGB")
    except Exception as exc:
        raise ValueError("De camerafoto kon niet worden geopend.") from exc

    # A phone photo often contains a small barcode inside a large label. ZXing
    # can miss that after its normal whole-image downscaling, so scan the full
    # image plus overlapping crops in colour and high-contrast grayscale.
    width, height = image.size
    boxes = [(0, 0, width, height)]
    if width >= 600 and height >= 400:
        overlap_x = max(20, width // 12)
        overlap_y = max(20, height // 12)
        mid_x, mid_y = width // 2, height // 2
        boxes.extend([
            (0, 0, min(width, mid_x + overlap_x), min(height, mid_y + overlap_y)),
            (max(0, mid_x - overlap_x), 0, width, min(height, mid_y + overlap_y)),
            (0, max(0, mid_y - overlap_y), min(width, mid_x + overlap_x), height),
            (max(0, mid_x - overlap_x), max(0, mid_y - overlap_y), width, height),
            (0, 0, width, min(height, (2 * height) // 3)),
            (0, max(0, height // 3), width, height),
        ])

    passes: list[Image.Image] = []
    for box in boxes:
        crop = image.crop(box)
        gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
        sharp = gray.filter(ImageFilter.UnsharpMask(radius=1.4, percent=180, threshold=2))
        passes.extend([crop, gray, sharp])
        crop_width, crop_height = crop.size
        if max(crop_width, crop_height) < 2400:
            scale = min(3, max(2, 1800 // max(1, max(crop_width, crop_height))))
            passes.append(sharp.resize(
                (crop_width * scale, crop_height * scale), Image.Resampling.LANCZOS,
            ))

    values = []
    for candidate_image in passes:
        for binarizer in (zxingcpp.Binarizer.LocalAverage, zxingcpp.Binarizer.GlobalHistogram):
            found = zxingcpp.read_barcodes(
                candidate_image,
                try_rotate=True,
                try_downscale=True,
                try_invert=True,
                binarizer=binarizer,
            )
            for barcode in found:
                value = _clean(getattr(barcode, "text", "")).replace(" ", "")
                if value and value not in values:
                    values.append(value)
            if values:
                break
        if values:
            break
    if not values:
        raise ValueError(
            "Geen barcode herkend. Houd de code dichtbij, recht en scherp in beeld, "
            "of typ de cijfers onder de barcode handmatig in het veld Barcode."
        )
    if len(values) != 1:
        raise ValueError(
            "Meerdere barcodes in beeld gevonden: " + ", ".join(values) + ". "
            "Fotografeer één etiket tegelijk."
        )
    return values[0]


def _available_quantity(inventory_item: dict[str, Any]) -> int:
    level = (inventory_item or {}).get("inventoryLevel") or {}
    return next(
        (
            int(item.get("quantity") or 0)
            for item in level.get("quantities") or []
            if item.get("name") == "available"
        ),
        0,
    )


def _weldingshop_location(client: ShopifyClient) -> dict[str, Any]:
    locations = (client.shop_and_locations().get("locations") or {}).get("nodes") or []
    matches = [
        item for item in locations
        if _clean(item.get("name")).casefold() == "weldingshop"
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Shopify-locatie ‘Weldingshop’ niet eenduidig gevonden ({len(matches)} resultaten)."
        )
    return matches[0]


def _stock_target_from_variant(
    clean_barcode: str, variant: dict[str, Any], location: dict[str, Any],
) -> StockCountTarget:
    product = variant.get("product") or {}
    if _clean(product.get("status")).upper() != "ACTIVE":
        raise ValueError(
            f"Product {_clean(product.get('title')) or clean_barcode} is niet actief. "
            "De telling is geblokkeerd."
        )
    components = (variant.get("productVariantComponents") or {}).get("nodes") or []
    if variant.get("requiresComponents"):
        if len(components) != 1:
            raise ValueError(
                f"SKU {_clean(variant.get('sku'))} heeft {len(components)} componenten. "
                "Alleen één-op-één voorraadkoppelingen mogen mobiel worden geteld."
            )
        relation = components[0]
        target = relation.get("productVariant") or {}
        factor = int(relation.get("quantity") or 0)
        target_sku = _clean(target.get("sku"))
        if factor < 1 or not target_sku.startswith("STOCK-"):
            raise ValueError(
                f"SKU {_clean(variant.get('sku'))} heeft geen geldige STOCK-componentkoppeling."
            )
        stock_model = "component_bundle"
    else:
        target = variant
        factor = 1
        target_sku = _clean(variant.get("sku"))
        stock_model = "direct"
    inventory_item = target.get("inventoryItem") or {}
    inventory_item_id = _clean(inventory_item.get("id"))
    if not inventory_item_id or not inventory_item.get("tracked"):
        raise ValueError(f"Voorraad wordt niet gevolgd voor {target_sku or clean_barcode}.")
    level = inventory_item.get("inventoryLevel") or {}
    if not level:
        raise ValueError(
            f"{target_sku or clean_barcode} is niet op locatie Weldingshop geactiveerd."
        )
    media = (product.get("media") or {}).get("nodes") or []
    image = (variant.get("image") or {}) or ((media[0].get("image") or {}) if media else {})
    return StockCountTarget(
        barcode=clean_barcode,
        product_id=_clean(product.get("id")),
        product_handle=_clean(product.get("handle")),
        product_title=_clean(product.get("title")),
        product_status=_clean(product.get("status")),
        product_vendor=_clean(product.get("vendor")),
        product_image_url=_clean(image.get("url")),
        scanned_variant_id=_clean(variant.get("id")),
        scanned_sku=_clean(variant.get("sku")),
        scanned_title=_clean(variant.get("title")),
        units_per_scan=factor,
        target_variant_id=_clean(target.get("id")),
        target_sku=target_sku,
        target_inventory_item_id=inventory_item_id,
        target_inventory_quantity=_available_quantity(inventory_item),
        shopify_location_id=_clean(location.get("id")),
        shopify_location_name=_clean(location.get("name")),
        stock_model=stock_model,
    )


def resolve_barcode_stock_target(
    barcode: str, *, client: ShopifyClient | None = None,
) -> StockCountTarget:
    """Resolve a sale barcode to the inventory item that physically holds stock.

    Bundle parents resolve to their single STOCK-* component. Ordinary active
    Shopify variants resolve to their own inventory at location Weldingshop.
    Ambiguous barcodes and multi-component bundles are blocked instead of guessed.
    """
    clean_barcode = _clean(barcode).replace(" ", "")
    if not clean_barcode:
        raise ValueError("Scan of vul eerst een barcode in.")
    client = client or ShopifyClient.from_settings()
    location = _weldingshop_location(client)
    escaped = clean_barcode.replace("\\", "\\\\").replace('"', '\\"')
    data = client.graphql(
        """
        query MobileStockCountByBarcode($query:String!,$locationId:ID!){
          productVariants(first:10,query:$query){nodes{
            id sku barcode title requiresComponents
            image{url altText}
            product{
              id handle title status vendor
              media(first:1){nodes{... on MediaImage{image{url altText}}}}
            }
            inventoryItem{
              id tracked
              inventoryLevel(locationId:$locationId){
                quantities(names:["available"]){name quantity}
              }
            }
            productVariantComponents(first:10){nodes{
              quantity
              productVariant{
                id sku title
                inventoryItem{
                  id tracked
                  inventoryLevel(locationId:$locationId){
                    quantities(names:["available"]){name quantity}
                  }
                }
              }
            }}
          }}
        }
        """,
        {"query": f'barcode:"{escaped}"', "locationId": location["id"]},
    )
    exact = [
        node for node in (data.get("productVariants") or {}).get("nodes") or []
        if _clean(node.get("barcode")) == clean_barcode
    ]
    if not exact:
        raise ValueError(f"Barcode {clean_barcode} is niet in Shopify gevonden.")
    if len(exact) != 1:
        skus = ", ".join(sorted({_clean(item.get("sku")) or "zonder SKU" for item in exact}))
        raise ValueError(
            f"Barcode {clean_barcode} is aan meerdere varianten gekoppeld ({skus}). "
            "De telling is geblokkeerd."
        )
    return _stock_target_from_variant(clean_barcode, exact[0], location)


def lookup_barcode_link_candidate(
    barcode: str, sku: str, *, client: ShopifyClient | None = None,
    allow_replace: bool = False,
) -> BarcodeLinkCandidate:
    """Validate an unknown physical GTIN against one exact Shopify SKU.

    Linking is allowed only when the GTIN is unused globally and the resulting
    stock route is safe for this counter. Replacing an existing barcode must be
    explicitly enabled by the caller.
    """
    clean_barcode = _clean(barcode).replace(" ", "")
    clean_sku = _clean(sku)
    if not _valid_gtin(clean_barcode):
        raise ValueError("De gescande code is geen geldige GTIN/EAN van 8, 12, 13 of 14 cijfers.")
    if not clean_sku:
        raise ValueError("Vul het exacte Shopify-SKU van het artikel in.")
    client = client or ShopifyClient.from_settings()
    location = _weldingshop_location(client)
    escaped_barcode = clean_barcode.replace("\\", "\\\\").replace('"', '\\"')
    escaped_sku = clean_sku.replace("\\", "\\\\").replace('"', '\\"')
    data = client.graphql(
        """
        query MobileStockBarcodeLinkCandidate(
          $barcodeQuery:String!,$skuQuery:String!,$locationId:ID!
        ){
          barcodeMatches:productVariants(first:10,query:$barcodeQuery){nodes{id sku barcode}}
          skuMatches:productVariants(first:10,query:$skuQuery){nodes{
            id sku barcode title requiresComponents image{url altText}
            product{id handle title status vendor media(first:1){nodes{... on MediaImage{image{url altText}}}}}
            inventoryItem{id tracked inventoryLevel(locationId:$locationId){quantities(names:["available"]){name quantity}}}
            productVariantComponents(first:10){nodes{quantity productVariant{
              id sku title inventoryItem{id tracked inventoryLevel(locationId:$locationId){quantities(names:["available"]){name quantity}}}
            }}}
          }}
        }
        """,
        {
            "barcodeQuery": f'barcode:"{escaped_barcode}"',
            "skuQuery": f'sku:"{escaped_sku}"',
            "locationId": location["id"],
        },
    )
    barcode_matches = [
        item for item in (data.get("barcodeMatches") or {}).get("nodes") or []
        if _clean(item.get("barcode")) == clean_barcode
    ]
    if barcode_matches:
        used = ", ".join(sorted({_clean(item.get("sku")) or "zonder SKU" for item in barcode_matches}))
        raise ValueError(f"Barcode {clean_barcode} is al gekoppeld aan {used}.")
    sku_matches = [
        item for item in (data.get("skuMatches") or {}).get("nodes") or []
        if _clean(item.get("sku")).casefold() == clean_sku.casefold()
    ]
    if not sku_matches:
        raise ValueError(f"SKU {clean_sku} is niet in Shopify gevonden.")
    if len(sku_matches) != 1:
        raise ValueError(f"SKU {clean_sku} is niet uniek in Shopify; koppeling geblokkeerd.")
    variant = sku_matches[0]
    product = variant.get("product") or {}
    if _clean(product.get("status")).upper() != "ACTIVE":
        raise ValueError("Alleen varianten van actieve producten mogen een scanbarcode krijgen.")
    current_barcode = _clean(variant.get("barcode"))
    if current_barcode and not allow_replace:
        raise ValueError(
            f"SKU {clean_sku} heeft al barcode {current_barcode}; deze wordt niet overschreven."
        )
    components = (variant.get("productVariantComponents") or {}).get("nodes") or []
    if variant.get("requiresComponents"):
        if len(components) != 1:
            raise ValueError("Deze variant heeft niet precies één voorraadcomponent.")
        relation = components[0]
        target = relation.get("productVariant") or {}
        factor = int(relation.get("quantity") or 0)
        target_sku = _clean(target.get("sku"))
        if factor < 1 or not target_sku.startswith("STOCK-"):
            raise ValueError("Deze variant heeft geen geldige STOCK-componentkoppeling.")
        stock_model = "component_bundle"
    else:
        target = variant
        factor = 1
        target_sku = clean_sku
        stock_model = "direct"
    inventory_item = target.get("inventoryItem") or {}
    if not inventory_item.get("tracked") or not inventory_item.get("inventoryLevel"):
        raise ValueError(f"Voorraad voor {target_sku} is niet actief op locatie Weldingshop.")
    media = (product.get("media") or {}).get("nodes") or []
    image = (variant.get("image") or {}) or ((media[0].get("image") or {}) if media else {})
    return BarcodeLinkCandidate(
        barcode=clean_barcode,
        product_id=_clean(product.get("id")),
        product_handle=_clean(product.get("handle")),
        product_title=_clean(product.get("title")),
        product_status=_clean(product.get("status")),
        product_vendor=_clean(product.get("vendor")),
        product_image_url=_clean(image.get("url")),
        variant_id=_clean(variant.get("id")),
        variant_sku=clean_sku,
        variant_title=_clean(variant.get("title")),
        current_barcode=current_barcode,
        units_per_scan=factor,
        target_sku=target_sku,
        stock_model=stock_model,
    )


def add_barcode_to_variant(
    candidate: BarcodeLinkCandidate, *, client: ShopifyClient | None = None,
    audit_path: Path = AUDIT_PATH, replace_existing: bool = False,
) -> StockCountTarget:
    """Attach a proven physical barcode, read it back and return its stock route."""
    client = client or ShopifyClient.from_settings()
    if candidate.current_barcode and not replace_existing:
        raise ValueError(
            f"SKU {candidate.variant_sku} heeft al barcode {candidate.current_barcode}; "
            "bevestig expliciet dat deze mag worden vervangen."
        )
    fresh = lookup_barcode_link_candidate(
        candidate.barcode,
        candidate.variant_sku,
        client=client,
        allow_replace=replace_existing,
    )
    if fresh.variant_id != candidate.variant_id or fresh.product_id != candidate.product_id:
        raise RuntimeError("Shopify-variant wijzigde sinds de controle; koppeling is geblokkeerd.")
    if fresh.current_barcode != candidate.current_barcode:
        raise RuntimeError(
            "De huidige Shopify-barcode wijzigde sinds de controle; vervanging is geblokkeerd."
        )
    action = "barcode_replace" if fresh.current_barcode else "barcode_link"
    audit_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    result = client.graphql(
        """
        mutation MobileStockAddBarcode($productId:ID!,$variants:[ProductVariantsBulkInput!]!){
          productVariantsBulkUpdate(productId:$productId,variants:$variants){
            productVariants{id sku barcode}
            userErrors{field message code}
          }
        }
        """,
        {"productId": fresh.product_id, "variants": [{"id": fresh.variant_id, "barcode": fresh.barcode}]},
    ).get("productVariantsBulkUpdate") or {}
    errors = result.get("userErrors") or []
    if errors:
        _append_audit({
            "at": timestamp, "audit_id": audit_id, "action": action,
            "status": "shopify_error", "candidate": asdict(fresh), "errors": errors,
        }, audit_path=audit_path)
        raise RuntimeError("; ".join(_clean(error.get("message")) for error in errors))
    updated = next(
        (item for item in result.get("productVariants") or [] if item.get("id") == fresh.variant_id),
        None,
    )
    if not updated or _clean(updated.get("barcode")) != fresh.barcode:
        raise RuntimeError("Shopify bevestigde de nieuwe barcode niet in de mutatierespons.")
    readback = client.graphql(
        """
        query MobileStockBarcodeVariantReadback($id:ID!,$locationId:ID!){
          node(id:$id){... on ProductVariant{
            id sku barcode title requiresComponents image{url altText}
            product{id handle title status vendor media(first:1){nodes{... on MediaImage{image{url altText}}}}}
            inventoryItem{id tracked inventoryLevel(locationId:$locationId){quantities(names:["available"]){name quantity}}}
            productVariantComponents(first:10){nodes{quantity productVariant{
              id sku title inventoryItem{id tracked inventoryLevel(locationId:$locationId){quantities(names:["available"]){name quantity}}}
            }}}
          }}
        }
        """,
        {"id": fresh.variant_id, "locationId": _weldingshop_location(client)["id"]},
    )
    variant = readback.get("node") or {}
    if _clean(variant.get("id")) != fresh.variant_id:
        raise RuntimeError("De gekoppelde variant kon niet rechtstreeks worden teruggelezen.")
    target = _stock_target_from_variant(
        fresh.barcode, variant, _weldingshop_location(client),
    )
    _append_audit({
        "at": timestamp, "audit_id": audit_id, "action": action,
        "status": "ok", "candidate": asdict(fresh), "resolved_target": asdict(target),
    }, audit_path=audit_path)
    return target


def calculate_component_quantity(
    *, counted_units: int, units_per_scan: int, loose_units: int = 0,
) -> int:
    counted = int(counted_units)
    factor = int(units_per_scan)
    loose = int(loose_units)
    if counted < 0 or loose < 0:
        raise ValueError("Getelde aantallen kunnen niet negatief zijn.")
    if factor < 1:
        raise ValueError("De verpakkingseenheid moet minimaal 1 zijn.")
    if factor == 1 and loose:
        raise ValueError("Losse stuks mogen niet dubbel worden opgeteld bij een stukbarcode.")
    return counted * factor + loose


def _append_audit(entry: dict[str, Any], *, audit_path: Path = AUDIT_PATH) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def set_counted_stock(
    target: StockCountTarget,
    *, counted_units: int,
    loose_units: int = 0,
    mode: str = "total",
    client: ShopifyClient | None = None,
    audit_path: Path = AUDIT_PATH,
) -> dict[str, Any]:
    """Set or add Weldingshop stock after a compare-and-set inventory count."""
    entered_quantity = calculate_component_quantity(
        counted_units=counted_units,
        units_per_scan=target.units_per_scan,
        loose_units=loose_units,
    )
    clean_mode = _clean(mode).lower()
    if clean_mode not in {"total", "add"}:
        raise ValueError("Onbekende voorraadmodus; kies totaal instellen of toevoegen.")
    if clean_mode == "add" and entered_quantity < 1:
        raise ValueError("Vul minimaal één verpakking of één stuk in om toe te voegen.")
    new_quantity = (
        target.target_inventory_quantity + entered_quantity
        if clean_mode == "add" else entered_quantity
    )
    client = client or ShopifyClient.from_settings()
    audit_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = client.graphql(
        """
        mutation SaveMobileStockCount(
          $input:InventorySetQuantitiesInput!,$idempotencyKey:String!
        ){
          inventorySetQuantities(input:$input) @idempotent(key:$idempotencyKey){
            userErrors{field message code}
          }
        }
        """,
        {"idempotencyKey": audit_id, "input": {
            "name": "available",
            "reason": "correction",
            "referenceDocumentUri": (
                f"pim://weldingshop/mobile-stock-{clean_mode}/{audit_id}"
            ),
            "quantities": [{
                "inventoryItemId": target.target_inventory_item_id,
                "locationId": target.shopify_location_id,
                "quantity": new_quantity,
                "changeFromQuantity": target.target_inventory_quantity,
            }],
        }},
    ).get("inventorySetQuantities") or {}
    errors = payload.get("userErrors") or []
    if errors:
        _append_audit({
            "at": timestamp,
            "audit_id": audit_id,
            "status": "shopify_error",
            "target": asdict(target),
            "mode": clean_mode,
            "counted_units": int(counted_units),
            "loose_units": int(loose_units),
            "entered_quantity": entered_quantity,
            "new_quantity": new_quantity,
            "errors": errors,
        }, audit_path=audit_path)
        raise RuntimeError("; ".join(_clean(error.get("message")) for error in errors))

    readback = client.graphql(
        """
        query MobileStockCountReadback($id:ID!,$locationId:ID!){
          node(id:$id){... on ProductVariant{
            id sku
            inventoryItem{
              inventoryLevel(locationId:$locationId){
                quantities(names:["available"]){name quantity}
              }
            }
          }}
        }
        """,
        {"id": target.target_variant_id, "locationId": target.shopify_location_id},
    )
    node = readback.get("node") or {}
    confirmed = _available_quantity(node.get("inventoryItem") or {})
    entry = {
        "at": timestamp,
        "audit_id": audit_id,
        "status": "ok" if confirmed == new_quantity else "readback_mismatch",
        "target": asdict(target),
        "mode": clean_mode,
        "counted_units": int(counted_units),
        "loose_units": int(loose_units),
        "entered_quantity": entered_quantity,
        "new_quantity": new_quantity,
        "confirmed_quantity": confirmed,
    }
    _append_audit(entry, audit_path=audit_path)
    if confirmed != new_quantity:
        raise RuntimeError(
            f"Shopify-readback wijkt af: verwacht {new_quantity}, teruggelezen {confirmed}."
        )
    return entry
