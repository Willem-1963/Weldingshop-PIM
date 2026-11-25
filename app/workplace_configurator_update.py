from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests

from app.shopify.client import ShopifyClient
from app.workplace_configurator import (
    DB_PATH,
    LIVE_URL,
    REQUEST_HEADERS,
    _connect,
    _new_candidates,
    _now,
    _valkenpower_check,
    current_version,
    init_database,
    parse_live_configurator,
    save_live_snapshot,
    set_model_improvement_state,
    version_models,
)
from app.workplace_photo_model import generate_photo_model


STATUS_NAMESPACE = "pim"
STATUS_KEY = "workplace_configurator_sync"
CONFIG_NAMESPACE = "pim"
CONFIG_KEY = "workplace_configurator_model"
UPDATE_NAME_PREFIX = "PIM Werkplaatsconfigurator"
THEME_LIBRARY_LIMIT = 20
APP_DIR = Path(__file__).resolve().parent
AUTO_MODEL_ASSETS = {
    "assets/workplace-3d-models-v84.js": APP_DIR / "assets" / "workplace-3d-models-v84.js",
    "assets/workplace-configurator-3d-v107.js": APP_DIR / "assets" / "workplace-configurator-3d-v107.js",
}
CONFIGURATOR_SECTION = "sections/workplace-configurator.liquid"


def _gid(kind: str, value: str | int) -> str:
    text = str(value)
    return text if text.startswith("gid://") else f"gid://shopify/{kind}/{text}"


def _tail(value: str | None) -> str:
    return str(value or "").rsplit("/", 1)[-1]


def _user_errors(payload: dict[str, Any], field: str) -> None:
    errors = (payload.get(field) or {}).get("userErrors") or []
    if errors:
        raise RuntimeError("; ".join(str(row.get("message") or row) for row in errors))


def _set_run(path: Path, run_id: int, **values: Any) -> None:
    if not values:
        return
    columns = ",".join(f"{name}=?" for name in values)
    with _connect(path) as conn:
        conn.execute(
            f"UPDATE sync_runs SET {columns} WHERE id=?",
            [*values.values(), run_id],
        )


def _start_run(path: Path, trigger_type: str) -> int:
    init_database(path)
    trigger = "AUTOMATISCH" if trigger_type.upper().startswith("AUTO") else "HANDMATIG"
    with _connect(path) as conn:
        return int(
            conn.execute(
                """
                INSERT INTO sync_runs(
                    started_at,status,trigger_type,phase,shopify_status,regression_status
                ) VALUES(?,?,?,?,?,?)
                """,
                (_now(), "RUNNING", trigger, "ANALYSE", "NIET_GESTART", "NIET_GESTART"),
            ).lastrowid
        )


def _save_findings(
    path: Path, run_id: int, groups: dict[str, list[dict[str, Any]]]
) -> None:
    with _connect(path) as conn:
        for kind, rows in groups.items():
            conn.executemany(
                """
                INSERT INTO sync_findings(
                    run_id,kind,finding_key,name,sku,status,reason,url,details_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        run_id,
                        kind,
                        row["key"],
                        row["name"],
                        row.get("sku", ""),
                        row["status"],
                        row["reason"],
                        row.get("url", ""),
                        json.dumps(row.get("details") or {}, ensure_ascii=False),
                    )
                    for row in rows
                ],
            )


VARIANT_QUERY = """
query PimConfiguratorVariants($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on ProductVariant {
      id sku title price inventoryQuantity inventoryPolicy
      image { url }
      product {
        id title handle status onlineStoreUrl
        featuredMedia { ... on MediaImage { image { url } } }
      }
    }
  }
}
"""


PRODUCT_QUERY = """
query PimConfiguratorProducts($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Product {
      id title handle status onlineStoreUrl descriptionHtml productType vendor tags
      featuredMedia { ... on MediaImage { image { url } } }
      configurator: metafield(namespace: "pim", key: "workplace_configurator_model") {
        value type
      }
      prodLength: metafield(namespace: "custom", key: "prodlength") { value }
      prodWidth: metafield(namespace: "custom", key: "prodwidth") { value }
      prodHeight: metafield(namespace: "custom", key: "prodheight") { value }
      dimensions: metafield(namespace: "pim", key: "afmetingen") { value }
      variants(first: 100) {
        nodes {
          id sku title price inventoryQuantity inventoryPolicy
        }
      }
    }
  }
}
"""


def _chunks(values: list[str], size: int = 100) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _variant_nodes(
    client: ShopifyClient, variant_ids: Iterable[str]
) -> dict[str, dict[str, Any] | None]:
    result: dict[str, dict[str, Any] | None] = {}
    ids = [_gid("ProductVariant", value) for value in variant_ids]
    for batch in _chunks(ids):
        data = client.graphql(VARIANT_QUERY, {"ids": batch})
        for requested, node in zip(batch, data.get("nodes") or []):
            result[_tail(requested)] = node
    return result


def _product_nodes(
    client: ShopifyClient, product_ids: Iterable[str]
) -> dict[str, dict[str, Any] | None]:
    result: dict[str, dict[str, Any] | None] = {}
    ids = [_gid("Product", value) for value in product_ids]
    for batch in _chunks(ids):
        data = client.graphql(PRODUCT_QUERY, {"ids": batch})
        for requested, node in zip(batch, data.get("nodes") or []):
            result[requested] = node
    return result


def _price_cents(value: Any, fallback: Any) -> int:
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError, TypeError):
        return int(fallback or 0)


def _variant_available(node: dict[str, Any]) -> bool:
    product = node.get("product") or {}
    if product.get("status") != "ACTIVE" or not product.get("onlineStoreUrl"):
        return False
    quantity = node.get("inventoryQuantity")
    return quantity is None or int(quantity or 0) > 0 or node.get("inventoryPolicy") == "CONTINUE"


def _sync_record(record: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    product = node.get("product") or {}
    image = ((node.get("image") or {}).get("url") or
             (((product.get("featuredMedia") or {}).get("image") or {}).get("url")))
    synced = dict(record)
    synced.update(
        {
            "title": product.get("title") or record.get("title"),
            "handle": product.get("handle") or record.get("handle"),
            "url": product.get("onlineStoreUrl") or record.get("url"),
            "price": _price_cents(node.get("price"), record.get("price")),
            "available": _variant_available(node),
            "code": str(node.get("sku") or record.get("code") or "").strip(),
        }
    )
    if image:
        synced["image"] = image
    return synced


def _parse_explicit_models(
    product: dict[str, Any], existing_model_kinds: set[str]
) -> tuple[list[dict[str, Any]], str | None]:
    metafield = product.get("configurator") or {}
    if not metafield.get("value"):
        return [], "Shopify mist het metadataveld pim.workplace_configurator_model."
    try:
        config = json.loads(metafield["value"])
    except json.JSONDecodeError:
        return [], "Het configurator-metadataveld bevat geen geldige JSON."
    required = {"modelKind", "category", "width", "depth", "height"}
    missing = sorted(name for name in required if not config.get(name))
    if missing:
        return [], "Configurator-JSON mist: " + ", ".join(missing) + "."
    if config["modelKind"] not in existing_model_kinds:
        return [], "3D-modeltype bestaat nog niet in de live modelbibliotheek."
    records = []
    image = (((product.get("featuredMedia") or {}).get("image") or {}).get("url")) or ""
    for variant in (product.get("variants") or {}).get("nodes") or []:
        record = dict(config)
        record.update(
            {
                "variantId": _tail(variant.get("id")),
                "title": product.get("title") or "Onbenoemd artikel",
                "handle": product.get("handle") or "",
                "url": product.get("onlineStoreUrl") or "",
                "price": _price_cents(variant.get("price"), 0),
                "available": (
                    product.get("status") == "ACTIVE"
                    and bool(product.get("onlineStoreUrl"))
                    and (
                        variant.get("inventoryQuantity") is None
                        or int(variant.get("inventoryQuantity") or 0) > 0
                        or variant.get("inventoryPolicy") == "CONTINUE"
                    )
                ),
                "code": str(variant.get("sku") or "").strip(),
                "image": ((variant.get("image") or {}).get("url") or image),
                "series": config.get("series") or "equipment",
                "mount": config.get("mount") or "floor",
            }
        )
        records.append(record)
    return records, None


def _dimension_number(value: Any) -> float | None:
    text = str(value or "").strip().lower().replace(",", ".")
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group(0))
    if number <= 0:
        return None
    if " cm" in text:
        return number * 10
    if " m" in text and "mm" not in text:
        return number * 1000
    return number


def _product_dimensions_cm(product: dict[str, Any]) -> tuple[float, float, float] | None:
    """Return storefront width/depth/height in cm from factual Shopify data."""
    height = _dimension_number((product.get("prodHeight") or {}).get("value"))
    horizontal = [
        _dimension_number((product.get("prodLength") or {}).get("value")),
        _dimension_number((product.get("prodWidth") or {}).get("value")),
    ]
    if height and all(horizontal):
        return max(horizontal) / 10, min(horizontal) / 10, height / 10

    sources = [
        (product.get("dimensions") or {}).get("value") or "",
        BeautifulSoup(product.get("descriptionHtml") or "", "html.parser").get_text(" "),
    ]
    for source in sources:
        normalized = str(source).replace("×", "x").replace("\u00d7", "x")
        labelled = re.search(
            r"(\d+(?:[.,]\d+)?)\s*[bBwW]\s*x\s*(\d+(?:[.,]\d+)?)\s*[dD]\s*x\s*(\d+(?:[.,]\d+)?)\s*[hH]",
            normalized,
        )
        if labelled:
            values = [float(value.replace(",", ".")) / 10 for value in labelled.groups()]
            return values[0], values[1], values[2]
        generic = re.search(
            r"(\d+(?:[.,]\d+)?)\s*(?:mm)?\s*x\s*(\d+(?:[.,]\d+)?)\s*(?:mm)?\s*x\s*(\d+(?:[.,]\d+)?)\s*mm",
            normalized,
            re.IGNORECASE,
        )
        if generic:
            values = [float(value.replace(",", ".")) for value in generic.groups()]
            return max(values[:2]) / 10, min(values[:2]) / 10, values[2] / 10
    return None


def _automatic_model_defaults(
    product: dict[str, Any], existing_model_kinds: set[str]
) -> tuple[str, str] | None:
    haystack = " ".join(
        [
            str(product.get("title") or ""), str(product.get("productType") or ""),
            str(product.get("vendor") or ""), " ".join(product.get("tags") or []),
        ]
    ).lower()
    rules = [
        (("kast", "cabinet", "werkbank", "workshop"), "workbench", "Werkbanken en werktafels"),
        (("pallet", "stapelaar"), "pallet_truck", "Palletwagens"),
        (("bandendemonte", "bandenwissel"), "wheel_changer", "Banden(de)montagemachines"),
        (("balanceer",), "wheel_balancer", "Balanceermachines"),
        (("werkplaatspers", "hydraulische pers"), "shop_press", "Werkplaatspersen"),
        (("compressor",), "compressor", "Compressoren"),
        (("lasapparaat", "lasmachine"), "welding_machine", "Lasapparaten"),
    ]
    for needles, model_kind, category in rules:
        if any(needle in haystack for needle in needles) and model_kind in existing_model_kinds:
            return model_kind, category
    return None


def _generate_missing_model(
    product: dict[str, Any], existing_model_kinds: set[str]
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
    dimensions = _product_dimensions_cm(product)
    defaults = _automatic_model_defaults(product, existing_model_kinds)
    image = (((product.get("featuredMedia") or {}).get("image") or {}).get("url")) or ""
    if not dimensions:
        return [], "Shopify bevat geen betrouwbare lengte, breedte en hoogte.", None
    if not defaults:
        return [], "Productgroep kan niet betrouwbaar aan een bestaand 3D-modeltype worden gekoppeld.", None
    if not image:
        return [], "Shopify bevat geen productfoto voor automatische modelopbouw.", None

    model_kind, category = defaults
    width, depth, height = dimensions
    config: dict[str, Any] = {
        "modelKind": model_kind, "category": category,
        "width": width, "depth": depth, "height": height,
        "series": "equipment", "mount": "floor",
    }
    # Build the regular Shopify-backed records first, then derive exact visible geometry
    # from that product's own photo inside the same guarded update.
    synthetic = dict(product)
    synthetic["configurator"] = {"value": json.dumps(config)}
    records, blocker = _parse_explicit_models(synthetic, existing_model_kinds)
    if blocker or not records:
        return [], blocker or "Geen actieve Shopify-variant gevonden.", None
    photo_model = generate_photo_model(
        records[0], reason="Nieuw Shopify-product automatisch toevoegen",
        notes="Gebruik uitsluitend de eigen Shopify-productfoto en de gevalideerde maatvoering.",
    )
    profile = f"auto-new-{_tail(product.get('id'))}-{sha256(json.dumps(photo_model, sort_keys=True).encode()).hexdigest()[:10]}"
    config["photoModel"] = photo_model
    config["modelProfile"] = profile
    for record in records:
        record["photoModel"] = photo_model
        record["modelProfile"] = profile
    return records, None, config


def _catalog_plan(
    client: ShopifyClient,
    models: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_records = [json.loads(row["raw_json"]) for row in models]
    nodes = _variant_nodes(client, [row["variant_id"] for row in models])
    desired: list[dict[str, Any]] = []
    removals: list[dict[str, Any]] = []
    changed = 0
    represented_products: set[str] = set()
    for row, raw in zip(models, raw_records):
        node = nodes.get(row["variant_id"])
        product = (node or {}).get("product") or {}
        if node and product.get("id"):
            represented_products.add(str(product["id"]))
        if not node or product.get("status") != "ACTIVE" or not product.get("onlineStoreUrl"):
            reason = (
                "Variant bestaat niet meer."
                if not node
                else "Product is niet actief of niet meer online gepubliceerd."
            )
            removals.append(
                {
                    "key": row["variant_id"], "name": row["name"], "sku": row["sku"],
                    "status": "Wordt verwijderd", "reason": reason,
                    "url": row["product_url"],
                    "details": {"model": row, "shopify": node},
                }
            )
            continue
        synced = _sync_record(raw, node)
        if synced != raw:
            changed += 1
        desired.append(synced)

    product_ids = [row["key"] for row in candidates]
    product_nodes = _product_nodes(client, product_ids) if product_ids else {}
    model_kinds = {str(row.get("modelKind") or "") for row in raw_records}
    candidate_findings: list[dict[str, Any]] = []
    generated_configs: list[dict[str, Any]] = []
    generated_variant_ids: list[str] = []
    for finding in candidates:
        gid = _gid("Product", finding["key"])
        product = product_nodes.get(gid) or {}
        if gid in represented_products:
            candidate_findings.append(
                {
                    **finding,
                    "status": "Variantkeuze controleren",
                    "reason": (
                        "Dit fysieke product heeft al een live 3D-model. Extra Shopify-varianten "
                        "worden niet als dubbel apparaat toegevoegd."
                    ),
                }
            )
            continue
        records, blocker = _parse_explicit_models(product, model_kinds)
        generated_config = None
        if blocker and blocker.startswith("Shopify mist het metadataveld"):
            try:
                records, blocker, generated_config = _generate_missing_model(product, model_kinds)
            except Exception as exc:
                records, blocker = [], f"Automatische modelopbouw mislukte: {exc}"
        if blocker:
            candidate_findings.append(
                {**finding, "status": "Actie vereist", "reason": blocker}
            )
            continue
        desired.extend(records)
        changed += len(records)
        if generated_config is not None:
            generated_configs.append({"ownerId": gid, "config": generated_config})
            generated_variant_ids.extend(str(row["variantId"]) for row in records)
        candidate_findings.append(
            {
                **finding,
                "status": "Wordt toegevoegd",
                "reason": (
                    "Configuratormodel is automatisch uit Shopify-maatvoering en productfoto opgebouwd."
                    if generated_config is not None
                    else "Volledige Shopify-configurator-JSON is gevalideerd."
                ),
                "details": {**finding.get("details", {}), "records": records},
            }
        )
    return {
        "desired": desired,
        "new": candidate_findings,
        "remove": removals,
        "changed_records": changed + len(removals),
        "generated_configs": generated_configs,
        "generated_variant_ids": generated_variant_ids,
    }


def _open_model_requests(path: Path) -> list[dict[str, Any]]:
    with _connect(path) as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM model_improvement_requests
                WHERE status='OPEN'
                ORDER BY created_at,id
                """
            ).fetchall()
        ]


def _apply_open_model_improvements(
    catalog: list[dict[str, Any]], path: Path, run_id: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generate every queued photo model as part of the same guarded update run."""
    requests = _open_model_requests(path)
    if not requests:
        return catalog, {"requests": [], "variantIds": [], "generated": 0}

    selected: dict[str, dict[str, Any]] = {}
    request_targets: dict[int, list[str]] = {}
    for request in requests:
        if request["scope"] == "GROUP":
            matches = [row for row in catalog if row.get("category") == request["category"]]
        else:
            matches = [
                row for row in catalog
                if str(row.get("variantId") or "") == str(request["variant_id"] or "")
            ]
        if not matches:
            raise RuntimeError(
                f"Modelaanvraag #{request['id']} heeft geen modellen meer in de actuele catalogus."
            )
        variant_ids = [str(row["variantId"]) for row in matches]
        request_targets[int(request["id"])] = variant_ids
        for row in matches:
            selected[str(row["variantId"])] = row
        set_model_improvement_state(
            int(request["id"]), path=path, execution_status="BEZIG",
            validation_status="NIET_GEVALIDEERD", last_error="",
            update_run_id=run_id, started_at=_now(),
        )

    reasons: dict[str, list[str]] = {variant_id: [] for variant_id in selected}
    notes: dict[str, list[str]] = {variant_id: [] for variant_id in selected}
    for request in requests:
        for variant_id in request_targets[int(request["id"])]:
            reasons[variant_id].append(str(request["reason"] or ""))
            if request.get("notes"):
                notes[variant_id].append(str(request["notes"]))

    replacements: dict[str, dict[str, Any]] = {}
    try:
        for variant_id, record in selected.items():
            photo_model = generate_photo_model(
                record,
                reason="; ".join(dict.fromkeys(reasons[variant_id])),
                notes="; ".join(dict.fromkeys(notes[variant_id])),
            )
            improved = dict(record)
            improved["photoModel"] = photo_model
            improved["modelProfile"] = f"auto-photo-{variant_id}-{sha256(json.dumps(photo_model, sort_keys=True).encode()).hexdigest()[:10]}"
            replacements[variant_id] = improved
    except Exception as exc:
        for request in requests:
            set_model_improvement_state(
                int(request["id"]), path=path, status="OPEN",
                execution_status="GEFAALD", validation_status="GEFAALD",
                last_error=str(exc),
                completion_note="De bestaande live configurator is behouden.",
            )
        raise

    desired = [replacements.get(str(row.get("variantId") or ""), row) for row in catalog]
    return desired, {
        "requests": requests,
        "requestTargets": request_targets,
        "variantIds": sorted(replacements),
        "generated": len(replacements),
    }


def _finish_model_requests(
    improvement: dict[str, Any], *, path: Path, success: bool,
    error: str = "", version: dict[str, Any] | None = None,
) -> None:
    for request in improvement.get("requests") or []:
        request_id = int(request["id"])
        targets = improvement.get("requestTargets", {}).get(request_id, [])
        if success:
            set_model_improvement_state(
                request_id, path=path, status="VOLTOOID", execution_status="SUCCES",
                validation_status="SUCCES", last_error="", completed_at=_now(),
                completion_note=(
                    f"{len(targets)} model(len) automatisch gegen de productfoto opgebouwd, "
                    "getest en gepubliceerd."
                ),
                version_id=(version or {}).get("id"),
                theme_id=str((version or {}).get("theme_id") or ""),
                engine_version=str((version or {}).get("engine_version") or ""),
                model_version=str((version or {}).get("model_version") or ""),
            )
        else:
            set_model_improvement_state(
                request_id, path=path, status="OPEN", execution_status="GEFAALD",
                validation_status="GEFAALD", last_error=error,
                completion_note="De bestaande live configurator is behouden.",
            )


def _validate_catalog(catalog: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        tests.append({"test": name, "passed": bool(passed), "detail": detail})
        if not passed:
            raise RuntimeError(f"Regressietest mislukt: {name} — {detail}")

    check("Catalogus niet leeg", bool(catalog), f"{len(catalog)} modellen")
    variant_ids = [str(row.get("variantId") or "") for row in catalog]
    check("Unieke variant-ID's", len(variant_ids) == len(set(variant_ids)) and all(variant_ids), "Geen dubbele of lege variant-ID's")
    baseline_kinds = {str(row.get("modelKind") or "") for row in baseline}
    check(
        "Bestaande 3D-modeltypen",
        all(str(row.get("modelKind") or "") in baseline_kinds for row in catalog),
        "Iedere record gebruikt een bewezen modeltype",
    )
    dimensions_ok = all(
        all(float(row.get(key) or 0) > 0 for key in ("width", "depth", "height"))
        for row in catalog
    )
    check("Positieve buitenmaten", dimensions_ok, "Breedte, diepte en hoogte zijn positief")
    required_ok = all(
        row.get("title") and row.get("code") and row.get("category") and row.get("image")
        for row in catalog
    )
    check("Complete productkoppeling", required_ok, "Titel, SKU, categorie en afbeelding zijn gevuld")
    return tests


def _theme_catalog_filename(catalog_url: str) -> str:
    path = urlparse(catalog_url).path
    marker = "/assets/"
    if marker not in path:
        raise RuntimeError("Catalogusbestand kon niet uit de Shopify-URL worden afgeleid.")
    return "assets/" + path.split(marker, 1)[1]


def _main_theme(client: ShopifyClient) -> dict[str, Any]:
    data = client.graphql(
        """
        query PimMainTheme {
          themes(first: 20, roles: [MAIN]) {
            nodes { id name role processing updatedAt }
          }
        }
        """
    )
    nodes = (data.get("themes") or {}).get("nodes") or []
    if len(nodes) != 1:
        raise RuntimeError(f"Shopify rapporteert {len(nodes)} live-thema's in plaats van één.")
    return nodes[0]


def _themes(client: ShopifyClient) -> list[dict[str, Any]]:
    data = client.graphql(
        """
        query PimThemeLibrary {
          themes(first: 100) {
            nodes { id name role processing createdAt updatedAt }
          }
        }
        """
    )
    return list((data.get("themes") or {}).get("nodes") or [])


def _select_disposable_pim_preview(
    themes: list[dict[str, Any]], main_id: str
) -> dict[str, Any] | None:
    """Return the oldest safe PIM preview without touching manual themes.

    When MAIN is itself a PIM release, its predecessor becomes the new rollback
    immediately after publication. In that case the sole older PIM preview can
    safely make room for the duplicate. For a manual MAIN theme we retain one
    PIM rollback and require at least two PIM previews before pruning.
    """
    main_theme = next(
        (theme for theme in themes if str(theme.get("id") or "") == main_id),
        None,
    )
    main_is_pim_release = bool(
        main_theme
        and main_theme.get("role") == "MAIN"
        and str(main_theme.get("name") or "").startswith(UPDATE_NAME_PREFIX)
    )
    candidates = [
        theme for theme in themes
        if theme.get("role") == "UNPUBLISHED"
        and str(theme.get("name") or "").startswith(UPDATE_NAME_PREFIX)
        and str(theme.get("id") or "") != main_id
    ]
    required_candidates = 1 if main_is_pim_release else 2
    if len(candidates) < required_candidates:
        return None
    candidates.sort(
        key=lambda theme: (
            str(theme.get("createdAt") or theme.get("updatedAt") or ""),
            str(theme.get("id") or ""),
        )
    )
    return candidates[0]


def _delete_theme(client: ShopifyClient, theme_id: str) -> None:
    data = client.graphql(
        """
        mutation PimDeleteOldConfiguratorPreview($id: ID!) {
          themeDelete(id: $id) {
            deletedThemeId
            userErrors { field message }
          }
        }
        """,
        {"id": theme_id},
    )
    _user_errors(data, "themeDelete")
    deleted_id = (data.get("themeDelete") or {}).get("deletedThemeId")
    if deleted_id and str(deleted_id) != str(theme_id):
        raise RuntimeError("Shopify bevestigde de verwijdering van een onverwacht thema.")
    for _ in range(30):
        try:
            state = client.graphql(
                "query PimDeletedTheme($id: ID!) { theme(id: $id) { id } }",
                {"id": theme_id},
            ).get("theme")
        except RuntimeError as exc:
            if "Theme does not exist" in str(exc) or "NOT_FOUND" in str(exc):
                return
            raise
        if not state:
            return
        time.sleep(1)
    raise RuntimeError("Shopify heeft de oude PIM-preview niet tijdig verwijderd.")


def _ensure_theme_capacity(
    client: ShopifyClient, main_id: str
) -> dict[str, Any] | None:
    themes = _themes(client)
    if len(themes) < THEME_LIBRARY_LIMIT:
        return None
    disposable = _select_disposable_pim_preview(themes, main_id)
    if not disposable:
        raise RuntimeError(
            "De Shopify-themabibliotheek is vol. Er is geen oude PIM-preview die "
            "veilig kan worden verwijderd; de nieuwste rollback en alle handmatige "
            "thema's zijn behouden."
        )
    _delete_theme(client, str(disposable["id"]))
    return disposable


def _theme_capacity_preflight(client: ShopifyClient) -> dict[str, Any]:
    """Reserve a preview slot before the expensive update analysis starts."""
    main = _main_theme(client)
    removed = _ensure_theme_capacity(client, str(main["id"]))
    return {"main": main, "removed": removed}


def _find_theme_by_name(client: ShopifyClient, name: str) -> dict[str, Any] | None:
    matches = [theme for theme in _themes(client) if theme.get("name") == name]
    if not matches:
        return None
    matches.sort(
        key=lambda theme: (
            str(theme.get("createdAt") or theme.get("updatedAt") or ""),
            str(theme.get("id") or ""),
        ),
        reverse=True,
    )
    return matches[0]


def _resumable_preview(path: Path, main_id: str) -> str | None:
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT s.preview_theme_id
            FROM sync_runs s
            JOIN configurator_versions v ON v.id=s.version_id
            WHERE s.status='FAILED' AND s.preview_theme_id IS NOT NULL
              AND v.theme_id=?
            ORDER BY s.id DESC LIMIT 1
            """,
            (_tail(main_id),),
        ).fetchone()
    return str(row["preview_theme_id"]) if row else None


def _duplicate_theme(
    client: ShopifyClient,
    main_id: str,
    run_id: int,
    path: Path,
    existing_theme_id: str | None = None,
) -> dict[str, Any]:
    if existing_theme_id:
        theme = {"id": _gid("OnlineStoreTheme", existing_theme_id)}
    else:
        theme_name = f"{UPDATE_NAME_PREFIX} update {run_id}"
        _ensure_theme_capacity(client, main_id)
        data = client.graphql(
            """
            mutation PimDuplicateConfiguratorTheme($id: ID!, $name: String) {
              themeDuplicate(id: $id, name: $name) {
                newTheme { id name role processing }
                userErrors { field message }
              }
            }
            """,
            {"id": main_id, "name": theme_name},
        )
        _user_errors(data, "themeDuplicate")
        theme = (data.get("themeDuplicate") or {}).get("newTheme")
        if not theme:
            for _ in range(30):
                theme = _find_theme_by_name(client, theme_name)
                if theme:
                    break
                time.sleep(1)
        if not theme:
            raise RuntimeError(
                "Shopify maakte na het vrijmaken van themaruimte geen previewthema. "
                "De bestaande live configurator is behouden."
            )
    _set_run(path, run_id, preview_theme_id=_tail(theme["id"]))
    for _ in range(450):
        state = client.graphql(
            """query PimThemeProcessing($id: ID!) {
              theme(id: $id) { id name role processing }
            }""",
            {"id": theme["id"]},
        ).get("theme") or {}
        if state.get("role") not in (None, "UNPUBLISHED"):
            raise RuntimeError("De eerder gemaakte themakopie is niet meer ongepubliceerd.")
        if not state.get("processing"):
            return state
        time.sleep(2)
    raise RuntimeError("Shopify heeft de themakopie niet binnen vijftien minuten voltooid.")


def _upsert_theme_files(
    client: ShopifyClient, theme_id: str, files: dict[str, str]
) -> None:
    data = client.graphql(
        """
        mutation PimUpsertConfiguratorCatalog(
          $themeId: ID!, $files: [OnlineStoreThemeFilesUpsertFileInput!]!
        ) {
          themeFilesUpsert(themeId: $themeId, files: $files) {
            upsertedThemeFiles { filename }
            job { id }
            userErrors { field message }
          }
        }
        """,
        {
            "themeId": theme_id,
            "files": [
                {"filename": filename, "body": {"type": "TEXT", "value": value}}
                for filename, value in files.items()
            ],
        },
    )
    _user_errors(data, "themeFilesUpsert")
    job = ((data.get("themeFilesUpsert") or {}).get("job") or {}).get("id")
    if job:
        for _ in range(90):
            row = client.graphql(
                "query PimThemeJob($id: ID!) { job(id: $id) { done } }", {"id": job}
            ).get("job") or {}
            if row.get("done"):
                break
            time.sleep(1)
        else:
            raise RuntimeError("Shopify heeft het catalogusbestand niet tijdig verwerkt.")


def _upsert_catalog(
    client: ShopifyClient, theme_id: str, filename: str, catalog: list[dict[str, Any]]
) -> None:
    _upsert_theme_files(
        client, theme_id,
        {filename: json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))},
    )


def _theme_text_file(client: ShopifyClient, theme_id: str, filename: str) -> str:
    data = client.graphql(
        """
        query PimThemeTextFile($id: ID!, $filenames: [String!]!) {
          theme(id: $id) {
            files(filenames: $filenames, first: 1) {
              nodes {
                filename
                body { ... on OnlineStoreThemeFileBodyText { content } }
              }
            }
          }
        }
        """,
        {"id": theme_id, "filenames": [filename]},
    )
    nodes = (((data.get("theme") or {}).get("files") or {}).get("nodes") or [])
    if not nodes or not ((nodes[0].get("body") or {}).get("content")):
        raise RuntimeError(f"Shopify-thema mist {filename}.")
    return str(nodes[0]["body"]["content"])


def _install_auto_model_renderer(client: ShopifyClient, theme_id: str) -> None:
    section = _theme_text_file(client, theme_id, CONFIGURATOR_SECTION)
    if "workplace-configurator-3d-v107.js" not in section:
        if "workplace-configurator-3d-v106.js" not in section:
            raise RuntimeError("De configuratorsectie verwijst niet naar de verwachte engine v106.")
        section = section.replace(
            "workplace-configurator-3d-v106.js",
            "workplace-configurator-3d-v107.js",
        )
    files = {CONFIGURATOR_SECTION: section}
    for filename, source in AUTO_MODEL_ASSETS.items():
        if not source.exists():
            raise RuntimeError(f"Automatisch rendererbestand ontbreekt: {source.name}.")
        files[filename] = source.read_text(encoding="utf-8")
    _upsert_theme_files(client, theme_id, files)


def _publish_theme(client: ShopifyClient, theme_id: str) -> None:
    data = client.graphql(
        """
        mutation PimPublishConfiguratorTheme($id: ID!) {
          themePublish(id: $id) {
            theme { id name role }
            userErrors { field message }
          }
        }
        """,
        {"id": theme_id},
    )
    _user_errors(data, "themePublish")
    role = ((data.get("themePublish") or {}).get("theme") or {}).get("role")
    if role != "MAIN":
        raise RuntimeError("Het nieuwe thema is niet als MAIN bevestigd.")


def _preview_regression(
    theme_id: str | None,
    expected: list[dict[str, Any]],
    filename: str,
    target_variant_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    numeric_id = _tail(theme_id) if theme_id else ""
    url = LIVE_URL + (f"?preview_theme_id={numeric_id}" if numeric_id else "")
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=60)
    response.raise_for_status()
    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("script.ws-configurator__equipment-url")
    if not node:
        raise RuntimeError("Preview mist de catalogusverwijzing.")
    catalog_url = json.loads(node.string or node.get_text())
    if catalog_url.startswith("//"):
        catalog_url = "https:" + catalog_url
    elif catalog_url.startswith("/"):
        catalog_url = "https://www.weldingshop.nl" + catalog_url
    catalog_response = requests.get(catalog_url, headers=REQUEST_HEADERS, timeout=60)
    catalog_response.raise_for_status()
    actual = catalog_response.json()
    expected_hash = sha256(json.dumps(expected, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    actual_hash = sha256(json.dumps(actual, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    tests = [
        {"test": "Preview HTTP", "passed": True, "detail": str(response.status_code)},
        {"test": "Previewcatalogus", "passed": actual_hash == expected_hash, "detail": f"{len(actual)} modellen"},
        {"test": "Catalogusbestand", "passed": filename.endswith(Path(urlparse(catalog_url).path).name), "detail": catalog_url},
    ]
    if not all(row["passed"] for row in tests):
        raise RuntimeError("Preview gebruikt niet exact de gevalideerde catalogus.")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_selector("canvas", timeout=60_000)
            page.wait_for_timeout(3_000)
            body = page.locator("body").inner_text()
            canvas_count = page.locator("canvas").count()
            target_records = [
                row for row in expected
                if str(row.get("variantId") or "") in set(target_variant_ids or [])
            ]
            model_result = {"ok": True, "tested": 0, "failed": []}
            if target_records:
                model_result = page.evaluate(
                    """async records => {
                      const root=document.querySelector('[id^="WorkshopConfigurator-"]');
                      const result={ok:true,tested:0,failed:[]};
                      for(const product of records){
                        root.__ws3d.sync([{instanceId:'pim-auto-test',product}]);
                        await Promise.resolve();
                        const entry=root.__ws3d.items.get('pim-auto-test');
                        let meshes=0;
                        entry?.group?.traverse(node=>{if(node.isMesh)meshes+=1});
                        if(!entry||!entry.group?.userData?.automaticPhotoModel||meshes<8){
                          result.failed.push(`${product.code||product.variantId}: ${meshes} meshes`);
                        }
                        result.tested+=1;
                        root.__ws3d.sync([]);
                      }
                      result.ok=!result.failed.length&&root.__ws3d.items.size===0;
                      return result;
                    }""",
                    target_records,
                )
            catalog_result = page.evaluate(
                """async records => {
                  const root=document.querySelector('[id^="WorkshopConfigurator-"]');
                  const result={ok:true,tested:0,failed:[]};
                  for(const product of records){
                    try{
                      root.__ws3d.sync([{instanceId:'pim-regression',product}]);
                      await Promise.resolve();
                      const entry=root.__ws3d.items.get('pim-regression');
                      if(!entry||root.__ws3d.items.size!==1){
                        result.failed.push(String(product.code||product.variantId));
                      }
                      result.tested+=1;
                    }catch(error){
                      result.failed.push(`${product.code||product.variantId}: ${error.message||error}`);
                    }finally{
                      root.__ws3d.sync([]);
                    }
                  }
                  result.ok=!result.failed.length&&root.__ws3d.items.size===0;
                  return result;
                }""",
                actual,
            )
            tests.extend(
                [
                    {"test": "3D-canvas", "passed": canvas_count == 1, "detail": f"{canvas_count} canvas"},
                    {"test": "Configuratorinterface", "passed": "Stel uw werkplaats samen" in body, "detail": "Hoofdinterface zichtbaar"},
                    {"test": "Browserfouten", "passed": not errors, "detail": "; ".join(errors[:3]) or "Geen"},
                    {
                        "test": "Automatische fotomodellen",
                        "passed": bool(model_result["ok"]),
                        "detail": (
                            f"{model_result['tested']} modellen gebouwd"
                            if model_result["ok"]
                            else "; ".join(model_result["failed"][:5])
                        ),
                    },
                    {
                        "test": "Volledige modelregressie",
                        "passed": bool(catalog_result["ok"]),
                        "detail": (
                            f"{catalog_result['tested']} modellen gebouwd en verwijderd"
                            if catalog_result["ok"]
                            else "; ".join(catalog_result["failed"][:5])
                        ),
                    },
                ]
            )
        finally:
            browser.close()
    if not all(row["passed"] for row in tests):
        failed = [row["test"] for row in tests if not row["passed"]]
        raise RuntimeError("Previewregressie mislukt: " + ", ".join(failed))
    return tests


def _status_metafields(
    client: ShopifyClient,
    run_id: int,
    candidates: list[dict[str, Any]],
    removals: list[dict[str, Any]],
) -> int:
    rows: dict[str, dict[str, Any]] = {}
    for finding in candidates:
        owner_id = _gid("Product", finding["key"])
        rows[owner_id] = {
            "status": finding["status"], "reason": finding["reason"],
            "runId": run_id, "checkedAt": _now(),
        }
    for finding in removals:
        shopify = (finding.get("details") or {}).get("shopify") or {}
        product = shopify.get("product") or {}
        if product.get("id"):
            rows[product["id"]] = {
                "status": finding["status"], "reason": finding["reason"],
                "runId": run_id, "checkedAt": _now(),
            }
    if not rows:
        return 0
    mutation = """
    mutation PimConfiguratorStatuses($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id key namespace }
        userErrors { field message code }
      }
    }
    """
    changed = 0
    entries = [
        {
            "ownerId": owner_id,
            "namespace": STATUS_NAMESPACE,
            "key": STATUS_KEY,
            "type": "json",
            "value": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        }
        for owner_id, value in rows.items()
    ]
    for batch in _chunks(entries, 25):
        data = client.graphql(mutation, {"metafields": batch})
        _user_errors(data, "metafieldsSet")
        changed += len((data.get("metafieldsSet") or {}).get("metafields") or [])
    return changed


def _config_metafields(
    client: ShopifyClient, generated_configs: list[dict[str, Any]]
) -> int:
    """Persist validated auto-generated model definitions for future updates."""
    if not generated_configs:
        return 0
    mutation = """
    mutation PimConfiguratorModels($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id key namespace value }
        userErrors { field message code }
      }
    }
    """
    changed = 0
    entries = [
        {
            "ownerId": row["ownerId"], "namespace": CONFIG_NAMESPACE,
            "key": CONFIG_KEY, "type": "json",
            "value": json.dumps(row["config"], ensure_ascii=False, separators=(",", ":")),
        }
        for row in generated_configs
    ]
    for batch in _chunks(entries, 25):
        data = client.graphql(mutation, {"metafields": batch})
        _user_errors(data, "metafieldsSet")
        changed += len((data.get("metafieldsSet") or {}).get("metafields") or [])
    return changed


def run_update(
    *, trigger_type: str = "HANDMATIG", path: Path = DB_PATH
) -> dict[str, Any]:
    """Synchronize Shopify and the live configurator, guarded by preview regression."""
    run_id = _start_run(path, trigger_type)
    details: dict[str, Any] = {"tests": []}
    improvement: dict[str, Any] = {"requests": [], "variantIds": [], "generated": 0}
    try:
        snapshot = parse_live_configurator()
        version_id = save_live_snapshot(snapshot, path)
        models = version_models(version_id, path)
        client = ShopifyClient.from_settings()
        capacity = _theme_capacity_preflight(client)
        if capacity["removed"]:
            removed = capacity["removed"]
            details["tests"].append(
                {
                    "test": "Shopify-themacapaciteit",
                    "passed": True,
                    "detail": (
                        "Vooraf ruimte vrijgemaakt door alleen de oude PIM-preview "
                        f"{removed.get('name') or _tail(removed.get('id'))} te verwijderen"
                    ),
                }
            )
        candidates = _new_candidates(client, models)
        plan = _catalog_plan(client, models, candidates)
        plan["desired"], improvement = _apply_open_model_improvements(
            plan["desired"], path, run_id
        )
        plan["changed_records"] += improvement["generated"]
        valkenpower = _valkenpower_check(path)
        groups = {"new": plan["new"], "remove": plan["remove"], "valkenpower": valkenpower}
        _save_findings(path, run_id, groups)
        _set_run(
            path, run_id, phase="VALIDATIE", version_id=version_id,
            new_count=len(plan["new"]), removal_count=len(plan["remove"]),
            valkenpower_count=len(valkenpower),
        )

        baseline = snapshot["models"]
        details["tests"].extend(_validate_catalog(plan["desired"], baseline))
        details["tests"].append(
            {
                "test": "Shopify-koppelingen",
                "passed": True,
                "detail": f"{len(plan['desired'])} varianten tijdens de analyse gecontroleerd",
            }
        )
        if improvement["generated"]:
            details["tests"].append(
                {
                    "test": "Fotomodelgeneratie",
                    "passed": True,
                    "detail": f"{improvement['generated']} modellen automatisch gegenereerd",
                }
            )
        if plan["generated_configs"]:
            details["tests"].append(
                {
                    "test": "Nieuwe Shopify-producten",
                    "passed": True,
                    "detail": f"{len(plan['generated_configs'])} ontbrekende configuratormodellen automatisch opgebouwd",
                }
            )

        metafield_changes = 0
        preview_theme_id = None
        published_theme_id = None
        catalog_changed = plan["desired"] != baseline
        filename = _theme_catalog_filename(snapshot["version"]["catalog_url"])
        if catalog_changed:
            main = _main_theme(client)
            if _tail(main["id"]) != str(snapshot["version"]["theme_id"]):
                raise RuntimeError("Het live Shopify-thema wijzigde tijdens de update; start opnieuw.")
            reusable_preview = _resumable_preview(path, main["id"])
            preview = _duplicate_theme(
                client, main["id"], run_id, path, reusable_preview
            )
            preview_theme_id = _tail(preview["id"])
            _set_run(path, run_id, preview_theme_id=preview_theme_id)
            _upsert_catalog(client, preview["id"], filename, plan["desired"])
            if improvement["generated"] or plan["generated_configs"]:
                _install_auto_model_renderer(client, preview["id"])
            _set_run(path, run_id, phase="REGRESSIETEST", regression_status="BEZIG")
            details["tests"].extend(
                _preview_regression(
                    preview["id"], plan["desired"], filename,
                    sorted(set(improvement["variantIds"] + plan["generated_variant_ids"])),
                )
            )
            _set_run(path, run_id, phase="SHOPIFY", shopify_status="BEZIG")
            metafield_changes = _config_metafields(client, plan["generated_configs"])
            metafield_changes += _status_metafields(client, run_id, plan["new"], plan["remove"])
            main_now = _main_theme(client)
            if main_now["id"] != main["id"]:
                raise RuntimeError("Het live thema wijzigde vóór publicatie; preview is niet gepubliceerd.")
            _publish_theme(client, preview["id"])
            published_theme_id = preview_theme_id
            _set_run(path, run_id, published_theme_id=published_theme_id)
            for _ in range(30):
                live = parse_live_configurator()
                if live["version"]["theme_id"] == published_theme_id:
                    published_version_id = save_live_snapshot(live, path)
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Shopify bevestigde publicatie, maar de storefront schakelde niet tijdig over.")
            published_version = current_version(path) or {"id": published_version_id}
            _finish_model_requests(
                improvement, path=path, success=True, version=published_version
            )
        else:
            _set_run(path, run_id, phase="REGRESSIETEST", regression_status="BEZIG")
            details["tests"].extend(
                _preview_regression(
                    None, baseline, filename,
                    sorted(set(improvement["variantIds"] + plan["generated_variant_ids"])),
                )
            )
            _set_run(path, run_id, phase="SHOPIFY", shopify_status="BEZIG")
            metafield_changes = _config_metafields(client, plan["generated_configs"])
            metafield_changes += _status_metafields(client, run_id, plan["new"], plan["remove"])
            if improvement["requests"]:
                _finish_model_requests(
                    improvement, path=path, success=True,
                    version=current_version(path) or {},
                )

        shopify_changes = metafield_changes + int(catalog_changed)
        details.update(
            {
                "catalogChanged": catalog_changed,
                "catalogRecordChanges": plan["changed_records"],
                "metafieldChanges": metafield_changes,
                "actionRequired": sum(row["status"] == "Actie vereist" for row in plan["new"]),
                "automaticModelImprovements": improvement["generated"],
                "automaticNewProducts": len(plan["generated_configs"]),
            }
        )
        _set_run(
            path,
            run_id,
            finished_at=_now(),
            status="SUCCEEDED",
            phase="VOLTOOID",
            shopify_status="SUCCES",
            shopify_changed_count=shopify_changes,
            regression_status="SUCCES",
            details_json=json.dumps(details, ensure_ascii=False),
        )
        return {
            "run_id": run_id,
            "status": "SUCCEEDED",
            "new": plan["new"],
            "remove": plan["remove"],
            "valkenpower": valkenpower,
            "shopify_changed_count": shopify_changes,
            "preview_theme_id": preview_theme_id,
            "published_theme_id": published_theme_id,
            "tests": details["tests"],
            "automatic_model_improvements": improvement["generated"],
        }
    except Exception as exc:
        details["exception"] = str(exc)
        _set_run(
            path,
            run_id,
            finished_at=_now(),
            status="FAILED",
            phase="GEFAALD",
            shopify_status="GEFAALD",
            regression_status=("GEFAALD" if details.get("tests") else "NIET_VOLTOOID"),
            error=str(exc),
            details_json=json.dumps(details, ensure_ascii=False),
        )
        if improvement.get("requests"):
            _finish_model_requests(
                improvement, path=path, success=False, error=str(exc)
            )
        raise


def run_model_improvement(
    request_id: int,
    payload: dict[str, Any],
    *,
    path: Path = DB_PATH,
) -> dict[str, Any]:
    """Publish an uploaded model bundle through the guarded Shopify preview flow."""
    run_id = _start_run(path, "HANDMATIG")
    details: dict[str, Any] = {
        "kind": "MODELVERBETERING",
        "requestId": int(request_id),
        "tests": [],
    }
    try:
        snapshot = parse_live_configurator()
        version_id = save_live_snapshot(snapshot, path)
        validation = validate_model_improvement_bundle(request_id, payload, path=path)
        changed_ids = set(validation["changedVariantIds"])
        replacement = {
            str(row["variantId"]): row for row in validation["models"]
        }
        baseline = snapshot["models"]
        desired = [
            replacement.get(str(row.get("variantId") or ""), row)
            for row in baseline
        ]
        if not changed_ids.issubset({str(row.get("variantId") or "") for row in baseline}):
            raise RuntimeError("Een geselecteerd model staat niet meer in de actuele livecatalogus.")
        details["tests"].extend(_validate_catalog(desired, baseline))
        details["tests"].append(
            {
                "test": "Geselecteerde modellen",
                "passed": True,
                "detail": f"{len(changed_ids)} van {validation['modelCount']} modellen gewijzigd",
            }
        )
        set_model_improvement_state(
            request_id,
            path=path,
            validation_status="SUCCES",
            execution_status="BEZIG",
            payload_json=json.dumps(payload, ensure_ascii=False),
            last_error="",
            update_run_id=run_id,
            started_at=_now(),
        )
        _set_run(
            path,
            run_id,
            phase="VALIDATIE",
            version_id=version_id,
            details_json=json.dumps(details, ensure_ascii=False),
        )

        client = ShopifyClient.from_settings()
        main = _main_theme(client)
        if _tail(main["id"]) != str(snapshot["version"]["theme_id"]):
            raise RuntimeError("Het live Shopify-thema wijzigde tijdens de validatie; start opnieuw.")
        preview = _duplicate_theme(client, main["id"], run_id, path)
        preview_theme_id = _tail(preview["id"])
        filename = _theme_catalog_filename(snapshot["version"]["catalog_url"])
        _upsert_catalog(client, preview["id"], filename, desired)
        _set_run(path, run_id, phase="REGRESSIETEST", regression_status="BEZIG")
        details["tests"].extend(
            _preview_regression(preview["id"], desired, filename)
        )
        _set_run(path, run_id, phase="SHOPIFY", shopify_status="BEZIG")
        main_now = _main_theme(client)
        if main_now["id"] != main["id"]:
            raise RuntimeError("Het live thema wijzigde vóór publicatie; de preview bleef ongepubliceerd.")
        _publish_theme(client, preview["id"])
        for _ in range(30):
            live = parse_live_configurator()
            if live["version"]["theme_id"] == preview_theme_id:
                published_version_id = save_live_snapshot(live, path)
                published_version = current_version(path) or {}
                break
            time.sleep(2)
        else:
            raise RuntimeError(
                "Shopify bevestigde publicatie, maar de storefront schakelde niet tijdig over."
            )
        details.update(
            {
                "changedVariantIds": sorted(changed_ids),
                "modelCount": validation["modelCount"],
                "previewThemeId": preview_theme_id,
                "publishedThemeId": preview_theme_id,
            }
        )
        _set_run(
            path,
            run_id,
            finished_at=_now(),
            status="SUCCEEDED",
            phase="VOLTOOID",
            shopify_status="SUCCES",
            shopify_changed_count=1,
            regression_status="SUCCES",
            preview_theme_id=preview_theme_id,
            published_theme_id=preview_theme_id,
            details_json=json.dumps(details, ensure_ascii=False),
        )
        set_model_improvement_state(
            request_id,
            path=path,
            status="VOLTOOID",
            execution_status="SUCCES",
            validation_status="SUCCES",
            last_error="",
            completed_at=_now(),
            completion_note=(
                f"{len(changed_ids)} model(len) gepubliceerd na geslaagde preview- en regressietest."
            ),
            version_id=published_version_id,
            theme_id=str(published_version.get("theme_id") or preview_theme_id),
            engine_version=str(published_version.get("engine_version") or ""),
            model_version=str(published_version.get("model_version") or ""),
        )
        return {
            "run_id": run_id,
            "status": "SUCCEEDED",
            "request_id": int(request_id),
            "changed_variant_ids": sorted(changed_ids),
            "published_theme_id": preview_theme_id,
            "tests": details["tests"],
        }
    except Exception as exc:
        details["exception"] = str(exc)
        _set_run(
            path,
            run_id,
            finished_at=_now(),
            status="FAILED",
            phase="GEFAALD",
            shopify_status="GEFAALD",
            regression_status=("GEFAALD" if details.get("tests") else "NIET_VOLTOOID"),
            error=str(exc),
            details_json=json.dumps(details, ensure_ascii=False),
        )
        set_model_improvement_state(
            request_id,
            path=path,
            status="OPEN",
            execution_status="GEFAALD",
            validation_status=("SUCCES" if details.get("tests") else "GEFAALD"),
            last_error=str(exc),
            completion_note="De bestaande live configurator is behouden.",
        )
        raise


def update_history(path: Path = DB_PATH, limit: int = 100) -> list[dict[str, Any]]:
    init_database(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [dict(row) for row in rows]
