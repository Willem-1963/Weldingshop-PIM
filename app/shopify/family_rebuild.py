from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.product_families import load_stored_product_families
from app.shopify.client import ShopifyClient


AUDIT_DIR = Path(__file__).resolve().parents[2] / "data/audit/family-rebuild"
ACTIVE_STATE = AUDIT_DIR / "certilas-family-rebuild-state.json"
FINAL_CONFIRMATION = "CERTILAS FAMILIES ACTIVEREN"


def _require_certilas(slug: str) -> None:
    if str(slug).strip().casefold() != "certilas":
        raise ValueError("Shopify-familieherbouw is uitsluitend toegestaan voor Certilas.")


def _shopify_certilas_products(client: ShopifyClient) -> list[dict[str, Any]]:
    query = """
    query CertilasFamilyBackup($after:String){
      products(first:50,after:$after,query:"vendor:Certilas"){
        pageInfo{hasNextPage endCursor}
        nodes{
          id title handle status vendor
          variants(first:10){
            pageInfo{hasNextPage endCursor}
            nodes{id sku}
          }
        }
      }
    }
    """
    variants_query = """
    query CertilasFamilyBackupVariants($id:ID!,$after:String!){
      product(id:$id){
        variants(first:100,after:$after){
          pageInfo{hasNextPage endCursor}
          nodes{id sku}
        }
      }
    }
    """
    after = None
    products: list[dict[str, Any]] = []
    while True:
        connection = client.graphql(query, {"after": after})["products"]
        for product in connection["nodes"]:
            variants = product.get("variants") or {}
            while variants.get("pageInfo", {}).get("hasNextPage"):
                variants = client.graphql(variants_query, {
                    "id": product["id"],
                    "after": variants["pageInfo"]["endCursor"],
                })["product"]["variants"]
                product["variants"]["nodes"].extend(variants.get("nodes") or [])
            products.append(product)
        if not connection["pageInfo"]["hasNextPage"]:
            return products
        after = connection["pageInfo"]["endCursor"]


def _pim_head_score(variant: dict[str, Any]) -> tuple[int, int, int, int]:
    raw = variant.get("current_raw_data") or {}
    enrichment = raw.get("website_enrichment") or {}
    return (
        len(str(variant.get("html_description") or "")),
        len(variant.get("images") or []),
        len((enrichment.get("facts") or {}).get("properties") or []),
        int(bool(variant.get("eligible"))),
    )


def _positive_sale_price(variant: dict[str, Any]) -> bool:
    try:
        return float(variant.get("sale_price") or 0) > 0
    except (TypeError, ValueError):
        return False


def create_family_rebuild_backup_and_plan(
    slug: str, client: ShopifyClient | None = None,
) -> dict[str, Any]:
    """Create a supplier-scoped, read-only backup and deterministic merge plan."""
    _require_certilas(slug)
    client = client or ShopifyClient.from_settings()
    products = _shopify_certilas_products(client)
    families = load_stored_product_families("certilas")
    by_sku: dict[str, dict[str, Any]] = {}
    for product in products:
        for variant in (product.get("variants") or {}).get("nodes") or []:
            sku = str(variant.get("sku") or "").strip().upper()
            if sku:
                by_sku[sku] = {"product": product, "variant": variant}

    plan = []
    for family in families:
        skus = [
            str(item.get("sku") or "").strip().upper()
            for item in family.get("variants") or []
            if item.get("eligible") and _positive_sale_price(item)
        ]
        current_products = {
            by_sku[sku]["product"]["id"]: by_sku[sku]["product"]
            for sku in skus if sku in by_sku
        }
        pim_candidates = sorted(
            family.get("variants") or [], key=_pim_head_score, reverse=True
        )
        pim_head = pim_candidates[0] if pim_candidates else None
        pim_head_sku = str((pim_head or {}).get("sku") or "").strip().upper()
        head = (
            by_sku[pim_head_sku]["product"]
            if pim_head_sku in by_sku else
            next(iter(current_products.values()), None)
        )
        other_products = [
            product for product in current_products.values()
            if not head or product["id"] != head["id"]
        ]
        plan.append({
            "family_key": family["family_key"],
            "family_title": family["title"],
            "eligible_skus": skus,
            "pim_variant_count": len(family.get("variants") or []),
            "shopify_product_ids": list(current_products),
            "shopify_product_count": len(current_products),
            "head_product_id": (head or {}).get("id"),
            "head_handle": (head or {}).get("handle"),
            "pim_head_sku": pim_head_sku,
            "pim_head_html_length": len(
                str((pim_head or {}).get("html_description") or "")
            ),
            "pim_head_image_count": len((pim_head or {}).get("images") or []),
            "head_selection": (
                "PIM: rijkste verrijkte HTML, daarna afbeeldingen en feiten"
            ),
            "old_handles": [
                item.get("handle") for item in other_products if item.get("handle")
            ],
            "requires_merge": len(current_products) > 1,
            "requires_create": head is None and bool(skus),
        })

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = AUDIT_DIR / f"certilas-shopify-routing-{stamp}.json"
    plan_path = AUDIT_DIR / f"certilas-family-plan-{stamp}.json"
    snapshot_path.write_text(json.dumps({
        "supplier": "certilas",
        "created_at": stamp,
        "purpose": "Shopify-ID's, handles en SKU's voor migratie en redirects",
        "product_content_source": "PIM",
        "shopify_routing": products,
    }, ensure_ascii=False, indent=2) + "\n")
    result = {
        "supplier": "certilas",
        "created_at": stamp,
        "shopify_products": len(products),
        "pim_families": len(families),
        "families_to_build": sum(
            bool(item["eligible_skus"]) for item in plan
        ),
        "families_to_merge": sum(item["requires_merge"] for item in plan),
        "families_to_create": sum(item["requires_create"] for item in plan),
        "migration_snapshot_path": str(snapshot_path),
        # Tijdelijk behouden voor compatibiliteit met een al geopende dashboardpagina.
        "backup_path": str(snapshot_path),
        "plan_path": str(plan_path),
        "families": plan,
    }
    plan_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def get_family_rebuild_state() -> dict[str, Any] | None:
    try:
        return json.loads(ACTIVE_STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _state_update(state: dict[str, Any], **changes: Any) -> None:
    state.update(changes, updated_at=datetime.now(timezone.utc).isoformat())
    _write_json(ACTIVE_STATE, state)


def start_family_rebuild(slug: str, plan_path: str) -> dict[str, Any]:
    """Start resumable draft construction in a detached worker."""
    _require_certilas(slug)
    plan = Path(plan_path).resolve()
    if plan.parent != AUDIT_DIR.resolve() or not plan.name.startswith(
        "certilas-family-plan-"
    ):
        raise ValueError("Selecteer een geldig Certilas-familieplan.")
    if not plan.exists():
        raise ValueError("Het geselecteerde familieplan bestaat niet meer.")
    current = get_family_rebuild_state() or {}
    if current.get("status") in {"queued", "building", "finalizing"}:
        raise ValueError("Er draait al een Certilas-familiemigratie.")
    if (
        current.get("status") == "failed"
        and Path(str(current.get("plan_path") or "")).resolve() == plan
        and current.get("phase") in {"draft_build", "review"}
    ):
        state = current
        _state_update(
            state, status="queued", error=None,
            message="Conceptopbouw wordt hervat.",
        )
    else:
        state = {
            "id": uuid.uuid4().hex,
            "supplier": "certilas",
            "phase": "draft_build",
            "status": "queued",
            "plan_path": str(plan),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "progress": 0,
            "message": "Conceptfamilies staan in de wachtrij.",
            "built": {},
        }
        _write_json(ACTIVE_STATE, state)
    process = subprocess.Popen(
        [sys.executable, "-m", "app.shopify.family_rebuild", "build"],
        cwd=Path(__file__).resolve().parents[2],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    _state_update(state, pid=process.pid)
    return state


def _prepared_sources() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    from app.shopify.sync import (
        _apply_canonical_family_content, _load_family_variant_options,
        _source_products,
    )
    from app.suppliers.hub import get_supplier

    supplier = get_supplier("certilas") or {}
    family_options = _load_family_variant_options("certilas")
    continue_selling = bool(
        (supplier.get("request_options") or {}).get(
            "continue_selling_when_out_of_stock", False
        )
    )
    products = {}
    for source in _source_products("certilas"):
        source = dict(source)
        try:
            source["_raw_data"] = json.loads(source.get("raw_data_json") or "{}")
        except json.JSONDecodeError:
            source["_raw_data"] = {}
        source["_shopify_field_mapping"] = supplier.get("shopify_field_mapping") or {}
        source["_shopify_metafield_mapping"] = supplier.get("shopify_metafield_mapping") or {}
        source["inventory_policy"] = "continue" if continue_selling else "deny"
        source["_out_of_stock_disclaimer"] = continue_selling
        source["_family_variant_options"] = family_options.get(source["sku"].upper(), {})
        _apply_canonical_family_content(source, source["_family_variant_options"])
        products[source["sku"].upper()] = source
    return products, supplier


def _temporary_handle(family_key: str) -> str:
    suffix = family_key.rsplit("|", 1)[-1]
    stem = re.sub(r"[^a-z0-9]+", "-", family_key.casefold()).strip("-")
    return f"certilas-migratie-{stem[:170]}-{suffix}"[:250]


def _validate_family_content(family: dict[str, Any], product_input: dict[str, Any]) -> None:
    """Voorkom dat een familie zonder PIM-verrijking een rijk product vervangt."""
    description = str(product_input.get("descriptionHtml") or "").strip()
    files = [
        item for item in product_input.get("files") or []
        if str(item.get("originalSource") or "").strip()
    ]
    missing = []
    if len(description) < 100:
        missing.append("verrijkte HTML")
    if not files:
        missing.append("productfoto's")
    if missing:
        raise RuntimeError(
            f"Familie {family.get('family_title') or family.get('family_key')} "
            "wordt niet opgebouwd; PIM mist " + " en ".join(missing) + "."
        )


def run_family_rebuild_drafts() -> None:
    """Worker: create every PIM family as an isolated Shopify draft."""
    from app.shopify.sync import _grouped_product_rows, _has_valid_price

    state = get_family_rebuild_state() or {}
    if state.get("supplier") != "certilas" or state.get("status") not in {
        "queued", "failed"
    }:
        return
    try:
        plan = json.loads(Path(state["plan_path"]).read_text())
        sources, _supplier = _prepared_sources()
        client = ShopifyClient.from_settings()
        families = []
        for original in plan.get("families") or []:
            wanted = [
                sku for sku in original.get("eligible_skus") or []
                if sku in sources and _has_valid_price(sources[sku])
            ]
            if wanted:
                families.append({**original, "eligible_skus": wanted})
        live_products = _shopify_certilas_products(client)
        migration_by_handle = {
            str(product.get("handle") or ""): product
            for product in live_products
            if str(product.get("handle") or "").startswith("certilas-migratie-")
        }
        built = dict(state.get("built") or {})
        _state_update(state, status="building", message="Conceptfamilies worden opgebouwd.")
        for index, family in enumerate(families, 1):
            key = family["family_key"]
            if built.get(key, {}).get("validated"):
                continue
            members = [sources[sku] for sku in family["eligible_skus"] if sku in sources]
            missing = sorted(set(family["eligible_skus"]) - set(sources))
            if missing or len(members) != len(family["eligible_skus"]):
                raise RuntimeError(f"PIM-SKU's ontbreken in {key}: {', '.join(missing)}")
            rows = _grouped_product_rows(members, {}, group_variants=True)
            if len(rows) != 1:
                raise RuntimeError(f"Familie {key} leverde {len(rows)} Shopify-producten op.")
            product_input = rows[0]["input"]
            _validate_family_content(family, product_input)
            product_input.update({
                "title": str(family["family_title"])[:255],
                "handle": _temporary_handle(key),
                "status": "DRAFT",
                "tags": list(dict.fromkeys([
                    *(product_input.get("tags") or []),
                    "certilas-familiemigratie",
                    f"certilas-familie-{key.rsplit('|', 1)[-1]}",
                ])),
            })
            existing_target = migration_by_handle.get(product_input["handle"])
            if existing_target:
                product_input["id"] = existing_target["id"]
                variant_ids = {
                    str(item.get("sku") or "").upper(): item.get("id")
                    for item in (existing_target.get("variants") or {}).get("nodes") or []
                }
                for variant in product_input.get("variants") or []:
                    variant_id = variant_ids.get(
                        str(variant.get("sku") or "").upper()
                    )
                    if variant_id:
                        variant["id"] = variant_id
            payload = client.graphql(
                """mutation($input:ProductSetInput!){
                  productSet(synchronous:true,input:$input){
                    product{id title handle status variants(first:100){nodes{sku price}}}
                    userErrors{field message code}
                  }}""",
                {"input": product_input},
            )["productSet"]
            if payload.get("userErrors"):
                raise RuntimeError(f"{key}: {payload['userErrors']}")
            product = payload.get("product") or {}
            actual = {str(v.get("sku") or "").upper() for v in (product.get("variants") or {}).get("nodes") or []}
            expected = set(family["eligible_skus"])
            prices_ok = all(float(v.get("price") or 0) > 0 for v in (product.get("variants") or {}).get("nodes") or [])
            if actual != expected or product.get("status") != "DRAFT" or not prices_ok:
                raise RuntimeError(f"Validatie conceptfamilie mislukt: {key}")
            built[key] = {
                "product_id": product["id"], "handle": product["handle"],
                "skus": sorted(actual), "validated": True,
                "expected_images": len(product_input.get("files") or []),
            }
            _state_update(
                state, built=built, progress=int(index * 95 / max(1, len(families))),
                message=f"Conceptfamilie {index} van {len(families)} opgebouwd.",
            )
        _validate_built_media(client, state, families)
        _state_update(
            state, status="ready_to_finalize", phase="review",
            progress=100,
            message="Alle conceptfamilies zijn opgebouwd en gecontroleerd. Activatie wacht op bevestiging.",
        )
    except Exception as exc:
        _state_update(state, status="failed", error=str(exc), message=f"Familieopbouw mislukt: {exc}")


def _validate_built_media(
    client: ShopifyClient, state: dict[str, Any], families: list[dict[str, Any]],
) -> None:
    built = state.get("built") or {}
    query = """query($id:ID!){product(id:$id){id status media(first:100){nodes{status mediaErrors{message}}}}}"""
    pending = [family for family in families if int(
        built[family["family_key"]].get("expected_images") or 0
    )]
    errors = []
    for attempt in range(4):
        errors = []
        retry = []
        for family in pending:
            item = built[family["family_key"]]
            product = client.graphql(query, {"id": item["product_id"]}).get("product") or {}
            media = (product.get("media") or {}).get("nodes") or []
            ready = sum(m.get("status") == "READY" and not m.get("mediaErrors") for m in media)
            expected = int(item.get("expected_images") or 0)
            if ready < expected:
                errors.append(
                    f"{family['family_title']}: {ready}/{expected} afbeeldingen gereed"
                )
                retry.append(family)
        if not retry:
            return
        pending = retry
        if attempt < 3:
            time.sleep(10)
    if errors:
        raise RuntimeError("Shopify-mediacontrole mislukt: " + "; ".join(errors[:25]))


def finalize_family_rebuild(slug: str, confirmation: str) -> dict[str, Any]:
    """Queue activation/removal after the explicit final confirmation."""
    _require_certilas(slug)
    if confirmation != FINAL_CONFIRMATION:
        raise ValueError("De vereiste bevestiging ontbreekt.")
    state = get_family_rebuild_state() or {}
    can_resume_finalize = (
        state.get("status") in {"failed", "redirects_pending"}
        and state.get("phase") in {"activate", "remove_old", "redirects"}
    )
    if state.get("status") != "ready_to_finalize" and not can_resume_finalize:
        raise ValueError("De conceptfamilies zijn nog niet volledig gecontroleerd.")
    _state_update(
        state, status="finalizing", phase="activate",
        message="Activering en gecontroleerde vervanging zijn gestart.",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "app.shopify.family_rebuild", "finalize"],
        cwd=Path(__file__).resolve().parents[2],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
    )
    _state_update(state, pid=process.pid)
    return state


def run_family_rebuild_finalize() -> None:
    """Worker: activate validated targets, then delete replaced old products."""
    state = get_family_rebuild_state() or {}
    if state.get("supplier") != "certilas" or state.get("status") != "finalizing":
        return
    try:
        plan = json.loads(Path(state["plan_path"]).read_text())
        built = state.get("built") or {}
        if not built:
            raise ValueError("Er zijn geen gecontroleerde conceptfamilies.")
        client = ShopifyClient.from_settings()
        publications = client.graphql("query{publications(first:100){nodes{id}}}")["publications"]["nodes"]
        publication_input = [{"publicationId": item["id"]} for item in publications]
        activated = set(state.get("activated_product_ids") or [])
        for family in plan.get("families") or []:
            target = built.get(family["family_key"])
            if not target or target["product_id"] in activated:
                continue
            payload = client.graphql(
                """mutation($input:ProductSetInput!){productSet(synchronous:true,input:$input){product{id status} userErrors{message}}}""",
                {"input": {"id": target["product_id"], "status": "ACTIVE"}},
            )["productSet"]
            if payload.get("userErrors"):
                raise RuntimeError(str(payload["userErrors"]))
            if publication_input:
                published = client.graphql(
                    """mutation($id:ID!,$input:[PublicationInput!]!){publishablePublish(id:$id,input:$input){userErrors{message}}}""",
                    {"id": target["product_id"], "input": publication_input},
                )["publishablePublish"]
                if published.get("userErrors"):
                    raise RuntimeError(f"Publiceren mislukt: {published['userErrors']}")
            activated.add(target["product_id"])
            _state_update(state, activated_product_ids=sorted(activated))
        _state_update(state, phase="remove_old", message="Nieuwe families actief; oude Certilas-producten worden vervangen.")
        old_products = {}
        for family in plan.get("families") or []:
            for product_id in family.get("shopify_product_ids") or []:
                old_products[product_id] = family
        deleted = set(state.get("deleted_product_ids") or [])
        for product_id in old_products:
            if product_id in deleted:
                continue
            result = client.graphql(
                """mutation($input:ProductDeleteInput!){productDelete(input:$input){deletedProductId userErrors{message}}}""",
                {"input": {"id": product_id}},
            )["productDelete"]
            if result.get("userErrors") or not result.get("deletedProductId"):
                raise RuntimeError(f"Verwijderen mislukt voor {product_id}: {result.get('userErrors')}")
            deleted.add(product_id)
            _state_update(state, deleted_product_ids=sorted(deleted))
        _state_update(state, phase="redirects", message="Oude URLs worden aan de nieuwe families gekoppeld.")
        used_handles = set()
        redirects = 0
        updated_handles = dict(state.get("updated_handles") or {})
        created_redirects = set(state.get("created_redirect_paths") or [])
        for family in plan.get("families") or []:
            target = built.get(family["family_key"])
            if not target:
                continue
            preferred = str(family.get("head_handle") or "").strip()
            desired = preferred if preferred and preferred not in used_handles else target["handle"]
            used_handles.add(desired)
            if desired != target["handle"] and updated_handles.get(family["family_key"]) != desired:
                changed = client.graphql(
                    """mutation($input:ProductSetInput!){productSet(synchronous:true,input:$input){userErrors{message}}}""",
                    {"input": {"id": target["product_id"], "handle": desired}},
                )["productSet"]
                if changed.get("userErrors"):
                    raise RuntimeError(f"Handle wijzigen mislukt: {changed['userErrors']}")
                updated_handles[family["family_key"]] = desired
                _state_update(state, updated_handles=updated_handles)
            for old_handle in family.get("old_handles") or []:
                path = f"/products/{old_handle}"
                if old_handle and old_handle != desired and path not in created_redirects:
                    result = client.graphql(
                        """mutation($input:UrlRedirectInput!){urlRedirectCreate(urlRedirect:$input){urlRedirect{id} userErrors{message}}}""",
                        {"input": {"path": f"/products/{old_handle}", "target": f"/products/{desired}"}},
                    )["urlRedirectCreate"]
                    if result.get("userErrors"):
                        raise RuntimeError(f"Redirect voor {path} mislukt: {result['userErrors']}")
                    redirects += 1
                    created_redirects.add(path)
                    _state_update(state, created_redirect_paths=sorted(created_redirects))
        _state_update(
            state, status="completed", phase="completed", progress=100,
            message="Certilas-familieherbouw voltooid.", redirects=redirects,
        )
    except Exception as exc:
        message = str(exc)
        if (
            state.get("phase") == "redirects"
            and "write_online_store_navigation" in message
        ):
            _state_update(
                state, status="redirects_pending", error=message,
                message=(
                    "Productmigratie voltooid; redirects wachten op Shopify-scope "
                    "write_online_store_navigation."
                ),
            )
        else:
            _state_update(
                state, status="failed", error=message,
                message=f"Afronding mislukt: {message}",
            )


if __name__ == "__main__" and len(sys.argv) == 2:
    if sys.argv[1] == "build":
        run_family_rebuild_drafts()
    elif sys.argv[1] == "finalize":
        run_family_rebuild_finalize()
