"""Fail-closed exact-tag taxonomy planning and explicitly gated execution.

Importing this module has no side effects. Dry-run planning never writes files,
databases, Shopify, or PIM state. Full tag arrays are never replaced.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from cryptography.fernet import Fernet

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROVENANCE = ("owner", "policy", "version", "source_digest", "approval_provenance")
_CONFIRM_PREFIX = "taxonomy-apply-sha256:"


class ReconciliationError(ValueError):
    """Unsafe, ambiguous, stale, or unapproved input."""


def _rollback_operations(completed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inverse = {"tagsAdd": "tagsRemove", "tagsRemove": "tagsAdd", "collectionAddProducts": "collectionRemoveProducts", "collectionRemoveProducts": "collectionAddProducts"}
    return [{**deepcopy(op), "mutation": inverse[op["mutation"]]} for op in reversed(completed)]


class PartialApplyError(RuntimeError):
    """Mutation failed; completed/pending operations are explicit for rollback."""

    def __init__(self, message: str, *, completed: list[dict[str, Any]], unknown: list[dict[str, Any]], pending: list[dict[str, Any]]):
        self.state = {"status": "partial-or-unknown", "message": message, "completed": deepcopy(completed), "unknown": deepcopy(unknown), "pending": deepcopy(pending), "rollback": _rollback_operations(completed)}
        super().__init__(json.dumps(self.state, ensure_ascii=False, sort_keys=True))


def strict_readonly_decrypt(value: str, key_path: str | Path) -> str:
    """Decrypt using an existing regular key file; never creates anything."""
    path = Path(key_path)
    if not path.parent.is_dir() or not path.is_file():
        raise ReconciliationError("existing secret key and parent directory are required")
    key = path.read_bytes().strip()
    if not key:
        raise ReconciliationError("secret key is empty")
    try:
        return Fernet(key).decrypt(value.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ReconciliationError("secret decryption failed") from exc


def load_manifest(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    validate_manifest(manifest)
    return manifest


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationError(f"{label} must be a non-empty string")
    return value.strip()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def manifest_digest(manifest: dict[str, Any]) -> str:
    return _canonical_sha256(manifest)


def confirmation_for_digest(digest: str, plan_digest: str | None = None) -> str:
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ReconciliationError("approved manifest digest must be sha256")
    if plan_digest is None:
        return _CONFIRM_PREFIX + digest
    if not isinstance(plan_digest, str) or not _SHA256.fullmatch(plan_digest):
        raise ReconciliationError("approved plan digest must be sha256")
    return _CONFIRM_PREFIX + digest + ":plan:" + plan_digest


def _registry(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = manifest.get("controlled_tag_registry")
    if not isinstance(entries, list) or not entries:
        raise ReconciliationError("controlled_tag_registry must be a non-empty list")
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ReconciliationError(f"registry[{index}] must be an object")
        item = dict(raw)
        tag = _text(item.get("tag"), f"registry[{index}].tag")
        if tag in result:
            raise ReconciliationError(f"duplicate controlled tag: {tag}")
        suppliers = item.get("supplier_scope")
        if not isinstance(suppliers, list) or not suppliers:
            raise ReconciliationError(f"registry[{index}].supplier_scope must be non-empty")
        item["supplier_scope"] = [_text(x, f"registry[{index}].supplier_scope") for x in suppliers]
        if len(item["supplier_scope"]) != len(set(item["supplier_scope"])):
            raise ReconciliationError(f"registry[{index}] has duplicate supplier scope")
        for field in _PROVENANCE:
            item[field] = _text(item.get(field), f"registry[{index}].{field}")
        if not _SHA256.fullmatch(item["source_digest"]):
            raise ReconciliationError(f"registry[{index}].source_digest must be sha256")
        result[tag] = item
    return result


def supplier_alias_map(manifest: dict[str, Any]) -> dict[str, str]:
    entries = manifest.get("supplier_alias_registry")
    if not isinstance(entries, list) or not entries:
        raise ReconciliationError("supplier_alias_registry must be a non-empty list")
    scope = set(manifest.get("supplier_scope") or [])
    result: dict[str, str] = {}
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ReconciliationError(f"supplier_alias_registry[{index}] must be an object")
        alias = _text(item.get("alias"), f"supplier_alias_registry[{index}].alias").casefold()
        supplier = _text(item.get("supplier"), f"supplier_alias_registry[{index}].supplier")
        if supplier not in scope:
            raise ReconciliationError(f"alias {alias} maps outside supplier scope")
        if alias in result:
            raise ReconciliationError(f"supplier alias collision: {alias}")
        result[alias] = supplier
    return result


def _validate_lock(lock: Any, *, desired: list[str], supplier: str, registry: dict[str, dict[str, Any]], label: str) -> None:
    if not isinstance(lock, dict) or lock.get("locked") is not True:
        raise ReconciliationError(f"{label} has malformed or unlocked taxonomy lock")
    approved = lock.get("approved_tags")
    if not isinstance(approved, list) or any(not isinstance(x, str) or not x for x in approved):
        raise ReconciliationError(f"{label}.approved_tags must be a string list")
    if approved != desired or len(approved) != len(set(approved)):
        raise ReconciliationError(f"{label} approved tags must exactly equal desired controlled tags")
    for tag in approved:
        reg = registry.get(tag)
        if reg is None:
            raise ReconciliationError(f"{label} references unknown controlled tag: {tag}")
        if supplier not in reg["supplier_scope"]:
            raise ReconciliationError(f"{label} supplier is outside registry scope for {tag}")
        for field in _PROVENANCE:
            if lock.get(field) != reg[field]:
                raise ReconciliationError(f"{label}.{field} does not match registry for {tag}")
    allowed = {"locked", "approved_tags", *_PROVENANCE}
    if set(lock) != allowed:
        raise ReconciliationError(f"{label} taxonomy lock has unknown or missing fields")


def _validate_removal_provenance(item: dict[str, Any], registry_item: dict[str, Any], label: str) -> None:
    for field in _PROVENANCE:
        value = _text(item.get(field), f"{label}.{field}")
        if value != registry_item[field]:
            raise ReconciliationError(f"{label}.{field} does not match registry")
    if not _SHA256.fullmatch(item["source_digest"]):
        raise ReconciliationError(f"{label}.source_digest must be sha256")


def _validate_manifest_v1(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ReconciliationError("manifest must use schema_version 1")
    scope = manifest.get("supplier_scope")
    if not isinstance(scope, list) or not scope:
        raise ReconciliationError("supplier_scope must be a non-empty list")
    scope = [_text(x, "supplier_scope") for x in scope]
    if len(scope) != len(set(scope)):
        raise ReconciliationError("duplicate supplier scope")
    supplier_alias_map(manifest)
    registry = _registry(manifest)
    products = manifest.get("products")
    if not isinstance(products, list):
        raise ReconciliationError("products must be a list")
    seen: set[str] = set()
    for index, product in enumerate(products):
        if not isinstance(product, dict):
            raise ReconciliationError(f"products[{index}] must be an object")
        sku = _text(product.get("sku"), f"products[{index}].sku")
        supplier = _text(product.get("supplier"), f"products[{index}].supplier")
        if sku in seen:
            raise ReconciliationError(f"duplicate SKU in manifest: {sku}")
        seen.add(sku)
        if supplier not in scope:
            raise ReconciliationError(f"{sku} is outside manifest supplier scope")
        desired = product.get("desired_controlled_tags")
        if not isinstance(desired, list) or not desired or len(desired) != len(set(desired)):
            raise ReconciliationError(f"{sku} desired_controlled_tags must be unique and non-empty")
        unknown = set(desired) - set(registry)
        if unknown:
            raise ReconciliationError(f"{sku} references unknown controlled tag(s): {sorted(unknown)}")
        _validate_lock(product.get("taxonomy_lock"), desired=desired, supplier=supplier, registry=registry, label=sku)

    removal_keys: set[tuple[str, str]] = set()
    for kind, require_reason in (("tombstones", True), ("prior_projector_owned_ledger", False)):
        entries = manifest.get(kind) or []
        if not isinstance(entries, list):
            raise ReconciliationError(f"{kind} must be a list")
        for index, item in enumerate(entries):
            label = f"{kind}[{index}]"
            if not isinstance(item, dict):
                raise ReconciliationError(f"{label} must be an object")
            sku = _text(item.get("sku"), f"{label}.sku")
            supplier = _text(item.get("supplier"), f"{label}.supplier")
            tag = _text(item.get("tag"), f"{label}.tag")
            if tag not in registry or supplier not in scope or supplier not in registry[tag]["supplier_scope"]:
                raise ReconciliationError(f"unsafe removal scope for {sku}/{tag}")
            if require_reason:
                _text(item.get("reason"), f"{label}.reason")
            _validate_removal_provenance(item, registry[tag], label)
            key = (sku, tag)
            if key in removal_keys:
                raise ReconciliationError(f"duplicate removal authorization: {sku}/{tag}")
            removal_keys.add(key)


def _normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    return {"id": record["id"], "sku": record["sku"], "supplier": record["supplier"], "tags": sorted(record["tags"])}


def catalog_digest(catalog: Iterable[dict[str, Any]]) -> str:
    return _canonical_sha256(sorted((_normalized_record(x) for x in catalog), key=lambda x: (x["sku"], x["id"], x["supplier"])))


def _catalog_index(catalog: Iterable[dict[str, Any]], *, relevant_skus: set[str], supplier_scope: set[str], controlled_tags: set[str]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    index: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    records: list[dict[str, Any]] = []
    for position, raw in enumerate(catalog):
        if not isinstance(raw, dict):
            raise ReconciliationError(f"catalog[{position}] must be an object")
        record = deepcopy(raw)
        sku = _text(record.get("sku"), f"catalog[{position}].sku")
        _text(record.get("id"), f"catalog[{position}].id")
        supplier = _text(record.get("supplier"), f"catalog[{position}].supplier")
        tags = record.get("tags")
        if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag for tag in tags) or len(tags) != len(set(tags)):
            raise ReconciliationError(f"catalog[{position}].tags must be a unique string list")
        if sku in index:
            previous = index[sku]
            if sku in relevant_skus or (supplier in supplier_scope and controlled_tags.intersection(tags)) or (previous["supplier"] in supplier_scope and controlled_tags.intersection(previous["tags"])):
                duplicates.add(sku)
        else:
            index[sku] = record
        records.append(record)
    if duplicates:
        raise ReconciliationError(f"duplicate catalog SKU(s): {sorted(duplicates)}")
    return index, records


def _build_plan_v1(manifest: dict[str, Any], catalog: Iterable[dict[str, Any]]) -> dict[str, Any]:
    _validate_manifest_v1(manifest)
    catalog = list(catalog)
    registry = _registry(manifest)
    controlled, scope = set(registry), set(manifest["supplier_scope"])
    desired = {item["sku"]: item for item in manifest["products"]}
    tombstones = {(x["sku"], x["tag"]) for x in manifest.get("tombstones") or []}
    ledger = {(x["sku"], x["tag"]) for x in manifest.get("prior_projector_owned_ledger") or []}
    owned_skus = set(desired) | {sku for sku, _ in tombstones | ledger}
    index, records = _catalog_index(catalog, relevant_skus=owned_skus, supplier_scope=scope, controlled_tags=controlled)
    operations: list[dict[str, Any]] = []
    noops: list[str] = []
    errors: list[str] = []
    for sku, item in desired.items():
        record = index.get(sku)
        if record is None:
            errors.append(f"missing catalog SKU: {sku}")
            continue
        if record["supplier"] != item["supplier"]:
            errors.append(f"supplier mismatch for {sku}")
            continue
        current = set(record["tags"])
        for tag in sorted(set(item["desired_controlled_tags"]) - current):
            operations.append({"mutation": "tagsAdd", "id": record["id"], "sku": sku, "tag": tag})
        for tag in sorted((current & controlled) - set(item["desired_controlled_tags"])):
            if (sku, tag) in tombstones or (sku, tag) in ledger:
                operations.append({"mutation": "tagsRemove", "id": record["id"], "sku": sku, "tag": tag})
        if not any(op["sku"] == sku for op in operations):
            noops.append(sku)
    for sku, tag in sorted(tombstones | ledger):
        if sku in desired:
            continue
        record = index.get(sku)
        if record is None:
            errors.append(f"missing removal SKU: {sku}")
        elif record["supplier"] not in scope:
            errors.append(f"removal SKU outside supplier scope: {sku}")
        elif tag in record["tags"]:
            operations.append({"mutation": "tagsRemove", "id": record["id"], "sku": sku, "tag": tag})
    unexpected = sorted({r["sku"] for r in records if r["supplier"] in scope and r["sku"] not in owned_skus and controlled.intersection(r["tags"])})
    msha, csha = manifest_digest(manifest), catalog_digest(catalog)
    plan_basis = {"manifest_sha256": msha, "catalog_sha256": csha, "operations": operations, "noops": sorted(noops), "unexpected": unexpected, "errors": errors}
    return {"mode": "dry-run", **plan_basis, "plan_sha256": _canonical_sha256(plan_basis), "summary": {"additions": sum(x["mutation"] == "tagsAdd" for x in operations), "removals": sum(x["mutation"] == "tagsRemove" for x in operations), "noops": len(noops), "unexpected": len(unexpected), "errors": len(errors)}}


def _tag_mutation(client: Any, operation: dict[str, Any]) -> None:
    field = operation["mutation"]
    query = f'''mutation TaxonomyTag($id:ID!,$tags:[String!]!){{{field}(id:$id,tags:$tags){{node{{id}} userErrors{{field message}}}}}}'''
    payload = client.graphql(query, {"id": operation["id"], "tags": [operation["tag"]]})
    if not isinstance(payload, dict) or field not in payload or not isinstance(payload[field], dict):
        raise RuntimeError(f"missing or malformed {field} response")
    result = payload[field]
    if "userErrors" not in result or not isinstance(result["userErrors"], list) or result["userErrors"]:
        raise RuntimeError(f"{field} did not return explicit empty userErrors: {result.get('userErrors')!r}")
    node = result.get("node")
    if not isinstance(node, dict) or node.get("id") != operation["id"]:
        raise RuntimeError(f"{field} node id mismatch: {node!r}")


def _affected_snapshot_v1(catalog: Iterable[dict[str, Any]], operations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    wanted = {op["sku"] for op in operations}
    index, _ = _catalog_index(catalog, relevant_skus=wanted, supplier_scope=set(), controlled_tags=set())
    missing = wanted - set(index)
    if missing:
        raise ReconciliationError(f"stale-plan readback missing SKU(s): {sorted(missing)}")
    return {sku: _normalized_record(index[sku]) for sku in wanted}



def _gid(value: Any, kind: str, label: str) -> str:
    value = _text(value, label)
    prefix = f"gid://shopify/{kind}/"
    if not value.startswith(prefix) or not value[len(prefix):].isdigit():
        raise ReconciliationError(f"{label} must be an exact Shopify {kind} GID")
    return value


def _unique_text_list(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ReconciliationError(f"{label} must be a {'non-empty ' if not allow_empty else ''}list")
    result = [_text(x, label) for x in value]
    if len(result) != len(set(result)):
        raise ReconciliationError(f"{label} must be unique")
    return result


def _v2_collection_registry(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(manifest.get("collection_registry") or []):
        label = f"collection_registry[{index}]"
        if not isinstance(raw, dict):
            raise ReconciliationError(f"{label} must be an object")
        item = dict(raw)
        handle = _text(item.get("handle"), f"{label}.handle")
        if handle in result:
            raise ReconciliationError(f"duplicate collection handle: {handle}")
        item["collection_gid"] = _gid(item.get("collection_gid"), "Collection", f"{label}.collection_gid")
        if item.get("rule_mode") not in ("manual", "automatic"):
            raise ReconciliationError(f"{label}.rule_mode must be manual or automatic")
        if item["rule_mode"] == "automatic":
            item["controlled_tag"] = _text(item.get("controlled_tag"), f"{label}.controlled_tag")
        elif "controlled_tag" in item:
            raise ReconciliationError(f"{label}.controlled_tag is invalid for a manual collection")
        item["updated_at"] = _text(item.get("updated_at"), f"{label}.updated_at")
        for field in _PROVENANCE:
            item[field] = _text(item.get(field), f"{label}.{field}")
        if not _SHA256.fullmatch(item["source_digest"]):
            raise ReconciliationError(f"{label}.source_digest must be sha256")
        result[handle] = item
    return result


def _validate_v2_removal(item: Any, label: str, product_gids: set[str], registry: dict[str, dict[str, Any]], *, collection: bool) -> None:
    if not isinstance(item, dict):
        raise ReconciliationError(f"{label} must be an object")
    product_gid = _gid(item.get("product_gid"), "Product", f"{label}.product_gid")
    if product_gid not in product_gids:
        raise ReconciliationError(f"{label} references a product outside manifest ownership")
    if collection:
        collection_gid = _gid(item.get("collection_gid"), "Collection", f"{label}.collection_gid")
        if collection_gid not in {x["collection_gid"] for x in registry.values()}:
            raise ReconciliationError(f"{label} references an unregistered collection")
    else:
        tag = _text(item.get("tag"), f"{label}.tag")
        if tag not in registry:
            raise ReconciliationError(f"{label} references an unregistered controlled tag")
    _text(item.get("reason"), f"{label}.reason")
    authority = next((x for x in registry.values() if collection and x["collection_gid"] == item.get("collection_gid")), None) if collection else registry.get(item.get("tag"))
    for field in _PROVENANCE:
        value = _text(item.get(field), f"{label}.{field}")
        if authority is None or value != authority[field]:
            raise ReconciliationError(f"{label}.{field} does not match registry")
    if not _SHA256.fullmatch(item["source_digest"]):
        raise ReconciliationError(f"{label}.source_digest must be sha256")


def _validate_manifest_v2(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
        raise ReconciliationError("manifest must use schema_version 2")
    registry = _registry(manifest)
    collection_registry = _v2_collection_registry(manifest)
    for handle, route in collection_registry.items():
        if route["rule_mode"] == "automatic" and route["controlled_tag"] not in registry:
            raise ReconciliationError(f"collection route {handle} references an unregistered controlled tag")
    products = manifest.get("products")
    if not isinstance(products, list) or not products:
        raise ReconciliationError("products must be a non-empty list")
    seen_products: set[str] = set()
    seen_variants: set[str] = set()
    for index, product in enumerate(products):
        label = f"products[{index}]"
        if not isinstance(product, dict):
            raise ReconciliationError(f"{label} must be an object")
        product_gid = _gid(product.get("product_gid"), "Product", f"{label}.product_gid")
        if product_gid in seen_products:
            raise ReconciliationError(f"duplicate product_gid in manifest: {product_gid}")
        seen_products.add(product_gid)
        _text(product.get("authority_class"), f"{label}.authority_class")
        _text(product.get("expected_vendor"), f"{label}.expected_vendor")
        if product.get("expected_status") not in ("ACTIVE", "DRAFT", "ARCHIVED"):
            raise ReconciliationError(f"{label}.expected_status is invalid")
        publications = _unique_text_list(product.get("expected_publication_gids"), f"{label}.expected_publication_gids")
        for j, gid in enumerate(publications):
            _gid(gid, "Publication", f"{label}.expected_publication_gids[{j}]")
        _text(product.get("expected_updated_at"), f"{label}.expected_updated_at")
        variants = product.get("variants")
        if not isinstance(variants, list) or not variants:
            raise ReconciliationError(f"{label}.variants must be a non-empty ordered list")
        local_variants: set[str] = set()
        for j, variant in enumerate(variants):
            vlabel = f"{label}.variants[{j}]"
            if not isinstance(variant, dict) or set(variant) != {"variant_gid", "sku", "barcode"}:
                raise ReconciliationError(f"{vlabel} must contain exactly variant_gid, sku and barcode")
            variant_gid = _gid(variant.get("variant_gid"), "ProductVariant", f"{vlabel}.variant_gid")
            if variant_gid in local_variants or variant_gid in seen_variants:
                raise ReconciliationError(f"duplicate supplier ownership for variant: {variant_gid}")
            local_variants.add(variant_gid); seen_variants.add(variant_gid)
            if not isinstance(variant.get("sku"), str) or not isinstance(variant.get("barcode"), str):
                raise ReconciliationError(f"{vlabel}.sku and barcode must be strings (blank allowed)")
        desired_tags = _unique_text_list(product.get("desired_controlled_tags") or [], f"{label}.desired_controlled_tags")
        if set(desired_tags) - set(registry):
            raise ReconciliationError(f"{label} references unknown controlled tags")
        for tag in desired_tags:
            if product["expected_vendor"] not in registry[tag]["supplier_scope"]:
                raise ReconciliationError(f"{label} vendor is outside registry scope for {tag}")
        primary = _text(product.get("primary_route"), f"{label}.primary_route")
        secondary = _unique_text_list(product.get("protected_secondary_routes") or [], f"{label}.protected_secondary_routes")
        future = _unique_text_list(product.get("desired_future_routes") or [], f"{label}.desired_future_routes")
        if primary in secondary or len({primary, *secondary, *future}) != 1 + len(secondary) + len(future):
            raise ReconciliationError(f"{label} route handles must be distinct")
        manual = _unique_text_list(product.get("manual_collection_gids") or [], f"{label}.manual_collection_gids")
        registered_manual = {x["collection_gid"] for x in collection_registry.values() if x["rule_mode"] == "manual"}
        for j, gid in enumerate(manual):
            _gid(gid, "Collection", f"{label}.manual_collection_gids[{j}]")
            if gid not in registered_manual:
                raise ReconciliationError(f"{label} manual collection is not explicitly registered")
        metafields = product.get("expected_controlled_metafields") or []
        if not isinstance(metafields, list):
            raise ReconciliationError(f"{label}.expected_controlled_metafields must be a list")
        for j, field in enumerate(metafields):
            if not isinstance(field, dict) or set(field) != {"namespace", "key", "type", "value"} or not all(isinstance(field[x], str) for x in field):
                raise ReconciliationError(f"{label}.expected_controlled_metafields[{j}] is invalid")
        source = product.get("source")
        if source is not None:
            if not isinstance(source, dict) or not set(source).issubset({"database", "table", "key", "row_digest", "provenance_digest"}):
                raise ReconciliationError(f"{label}.source has unsupported fields")
            for key, value in source.items():
                _text(value, f"{label}.source.{key}")
                if key.endswith("digest") and not _SHA256.fullmatch(value):
                    raise ReconciliationError(f"{label}.source.{key} must be sha256")
    for kind, is_collection in (("tag_tombstones", False), ("manual_collection_tombstones", True)):
        entries = manifest.get(kind) or []
        if not isinstance(entries, list):
            raise ReconciliationError(f"{kind} must be a list")
        keys: set[tuple[str, str]] = set()
        for index, item in enumerate(entries):
            _validate_v2_removal(item, f"{kind}[{index}]", seen_products, collection_registry if is_collection else registry, collection=is_collection)
            key = (item["product_gid"], item["collection_gid"] if is_collection else item["tag"])
            if key in keys:
                raise ReconciliationError(f"duplicate {kind} authorization")
            keys.add(key)


def validate_manifest(manifest: dict[str, Any]) -> None:
    version = manifest.get("schema_version") if isinstance(manifest, dict) else None
    if version == 1:
        _validate_manifest_v1(manifest)
    elif version == 2:
        _validate_manifest_v2(manifest)
    else:
        raise ReconciliationError("manifest must use schema_version 1 or 2")


def _normalized_v2_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_gid": record["product_gid"], "vendor": record["vendor"], "status": record["status"],
        "publication_gids": sorted(record["publication_gids"]), "variants": deepcopy(record["variants"]),
        "tags": sorted(record["tags"]), "controlled_metafields": sorted(deepcopy(record.get("controlled_metafields") or []), key=lambda x: (x["namespace"], x["key"])),
        "manual_collection_gids": sorted(record.get("manual_collection_gids") or []), "updated_at": record["updated_at"],
    }


def _v2_catalog_index(catalog: Iterable[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    index: dict[str, dict[str, Any]] = {}
    variants: set[str] = set()
    records: list[dict[str, Any]] = []
    for position, raw in enumerate(catalog):
        label = f"catalog[{position}]"
        if not isinstance(raw, dict): raise ReconciliationError(f"{label} must be an object")
        record = deepcopy(raw)
        gid = _gid(record.get("product_gid"), "Product", f"{label}.product_gid")
        if gid in index: raise ReconciliationError(f"duplicate catalog product GID: {gid}")
        _text(record.get("vendor"), f"{label}.vendor"); _text(record.get("updated_at"), f"{label}.updated_at")
        if record.get("status") not in ("ACTIVE", "DRAFT", "ARCHIVED"): raise ReconciliationError(f"{label}.status is invalid")
        _unique_text_list(record.get("publication_gids"), f"{label}.publication_gids")
        _unique_text_list(record.get("tags"), f"{label}.tags")
        _unique_text_list(record.get("manual_collection_gids") or [], f"{label}.manual_collection_gids")
        raw_variants = record.get("variants")
        if not isinstance(raw_variants, list) or not raw_variants: raise ReconciliationError(f"{label}.variants must be a non-empty ordered list")
        for j, variant in enumerate(raw_variants):
            vlabel=f"{label}.variants[{j}]"
            if not isinstance(variant, dict) or set(variant) != {"variant_gid", "sku", "barcode"}: raise ReconciliationError(f"{vlabel} is invalid")
            vgid=_gid(variant.get("variant_gid"), "ProductVariant", f"{vlabel}.variant_gid")
            if vgid in variants: raise ReconciliationError(f"duplicate supplier ownership for variant: {vgid}")
            variants.add(vgid)
            if not isinstance(variant.get("sku"), str) or not isinstance(variant.get("barcode"), str): raise ReconciliationError(f"{vlabel} identity strings are invalid")
        fields=record.get("controlled_metafields") or []
        if not isinstance(fields,list): raise ReconciliationError(f"{label}.controlled_metafields must be a list")
        index[gid]=record; records.append(record)
    return index, records


def _route_effects(manifest: dict[str, Any], product: dict[str, Any]) -> tuple[set[str], set[str], list[str]]:
    registry=_v2_collection_registry(manifest); tags=set(product.get("desired_controlled_tags") or []); collections=set(product.get("manual_collection_gids") or []); inert=[]
    handles=[product["primary_route"], *product.get("protected_secondary_routes", []), *product.get("desired_future_routes", [])]
    for handle in handles:
        route=registry.get(handle)
        if route is None:
            inert.append(handle); continue
        if route["rule_mode"] == "automatic": tags.add(route["controlled_tag"])
        else: collections.add(route["collection_gid"])
    return tags, collections, inert


def _build_plan_v2(manifest: dict[str, Any], catalog: Iterable[dict[str, Any]]) -> dict[str, Any]:
    _validate_manifest_v2(manifest); catalog=list(catalog); index, records=_v2_catalog_index(catalog)
    desired={x["product_gid"]:x for x in manifest["products"]}; registry=_registry(manifest)
    tag_tombs={(x["product_gid"],x["tag"]) for x in manifest.get("tag_tombstones") or []}
    collection_tombs={(x["product_gid"],x["collection_gid"]) for x in manifest.get("manual_collection_tombstones") or []}
    operations=[]; noops=[]; errors=[]; inert={}
    for gid,item in desired.items():
        record=index.get(gid)
        if record is None: errors.append(f"missing catalog product_gid: {gid}"); continue
        expected_variants=item["variants"]
        if record["vendor"] != item["expected_vendor"]: errors.append(f"vendor mismatch for {gid}"); continue
        if record["variants"] != expected_variants: errors.append(f"variant identity/order drift for {gid}"); continue
        if record["status"] != item["expected_status"]: errors.append(f"status assertion failed for {gid}"); continue
        if sorted(record["publication_gids"]) != sorted(item["expected_publication_gids"]): errors.append(f"publication assertion failed for {gid}"); continue
        if record["updated_at"] != item["expected_updated_at"]: errors.append(f"updatedAt CAS failed for {gid}"); continue
        if sorted(record.get("controlled_metafields") or [], key=lambda x:(x["namespace"],x["key"])) != sorted(item.get("expected_controlled_metafields") or [], key=lambda x:(x["namespace"],x["key"])): errors.append(f"controlled metafield assertion failed for {gid}"); continue
        desired_tags, desired_collections, unresolved=_route_effects(manifest,item)
        if set(desired_tags)-set(registry): errors.append(f"route for {gid} references unregistered controlled tag"); continue
        if any(record["vendor"] not in registry[tag]["supplier_scope"] for tag in desired_tags): errors.append(f"controlled tag supplier scope mismatch for {gid}"); continue
        if unresolved: inert[gid]=unresolved
        current_tags=set(record["tags"]); current_collections=set(record.get("manual_collection_gids") or [])
        for tag in sorted(desired_tags-current_tags): operations.append({"mutation":"tagsAdd","id":gid,"product_gid":gid,"tag":tag})
        for tag in sorted(current_tags-desired_tags):
            if (gid,tag) in tag_tombs: operations.append({"mutation":"tagsRemove","id":gid,"product_gid":gid,"tag":tag})
        for cg in sorted(desired_collections-current_collections): operations.append({"mutation":"collectionAddProducts","collection_gid":cg,"product_gid":gid})
        for cg in sorted(current_collections-desired_collections):
            if (gid,cg) in collection_tombs: operations.append({"mutation":"collectionRemoveProducts","collection_gid":cg,"product_gid":gid})
        if not any(op["product_gid"]==gid for op in operations): noops.append(gid)
    unexpected=sorted(set(index)-set(desired))
    msha=manifest_digest(manifest); normalized=sorted((_normalized_v2_record(x) for x in records),key=lambda x:x["product_gid"]); csha=_canonical_sha256(normalized)
    basis={"manifest_sha256":msha,"catalog_sha256":csha,"operations":operations,"noops":sorted(noops),"unexpected":unexpected,"errors":errors,"inert_future_routes":inert}
    counts={name:sum(x["mutation"]==name for x in operations) for name in ("tagsAdd","tagsRemove","collectionAddProducts","collectionRemoveProducts")}
    return {"mode":"dry-run",**basis,"plan_sha256":_canonical_sha256(basis),"summary":{**counts,"noops":len(noops),"unexpected":len(unexpected),"errors":len(errors),"inert_future_routes":sum(map(len,inert.values()))}}


def build_plan(manifest: dict[str, Any], catalog: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return _build_plan_v2(manifest,catalog) if manifest.get("schema_version")==2 else _build_plan_v1(manifest,catalog)


def _affected_snapshot_v2(catalog: Iterable[dict[str, Any]], operations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    wanted={op["product_gid"] for op in operations}; index,_=_v2_catalog_index(catalog); missing=wanted-set(index)
    if missing: raise ReconciliationError(f"stale-plan readback missing product GID(s): {sorted(missing)}")
    return {gid:_normalized_v2_record(index[gid]) for gid in wanted}


def _execute_mutation(client: Any, operation: dict[str, Any]) -> None:
    if operation["mutation"] in ("tagsAdd","tagsRemove"):
        _tag_mutation(client,operation); return
    field=operation["mutation"]
    query=f'''mutation TaxonomyCollection($id:ID!,$productIds:[ID!]!){{{field}(id:$id,productIds:$productIds){{userErrors{{field message}}}}}}'''
    payload=client.graphql(query,{"id":operation["collection_gid"],"productIds":[operation["product_gid"]]})
    result=payload.get(field) if isinstance(payload,dict) else None
    if not isinstance(result,dict) or not isinstance(result.get("userErrors"),list) or result["userErrors"]:
        raise RuntimeError(f"{field} did not return explicit empty userErrors")

def reconcile(manifest: dict[str, Any], catalog: Iterable[dict[str, Any]], *, client: Any = None, apply: bool = False, confirm: str | None = None, approved_manifest_sha256: str | None = None, approved_plan_sha256: str | None = None, catalog_reader: Callable[[], Iterable[dict[str, Any]]] | None = None) -> dict[str, Any]:
    catalog = list(catalog)
    plan = build_plan(manifest, catalog)
    if not apply:
        return plan
    actual_digest = manifest_digest(manifest)
    if not approved_manifest_sha256 or not _SHA256.fullmatch(approved_manifest_sha256) or approved_manifest_sha256 != actual_digest:
        raise ReconciliationError("apply requires an independently supplied exact approved manifest sha256")
    if not approved_plan_sha256 or not _SHA256.fullmatch(approved_plan_sha256) or approved_plan_sha256 != plan["plan_sha256"]:
        raise ReconciliationError("apply requires an independently supplied exact approved plan sha256")
    if confirm != confirmation_for_digest(approved_manifest_sha256, approved_plan_sha256):
        raise ReconciliationError("confirmation token is not bound to the approved manifest and plan digests")
    if client is None or catalog_reader is None:
        raise ReconciliationError("apply requires explicit client and fresh affected-product reader")
    if plan["errors"] or plan["unexpected"]:
        raise ReconciliationError("apply blocked by dry-run errors or unexpected ownership")
    snapshotter = _affected_snapshot_v2 if manifest.get("schema_version") == 2 else _affected_snapshot_v1
    expected = snapshotter(catalog, plan["operations"])
    fresh = snapshotter(list(catalog_reader()), plan["operations"])
    if fresh != expected:
        raise ReconciliationError("stale plan: affected product state drifted before first mutation")
    completed: list[dict[str, Any]] = []
    for index, operation in enumerate(plan["operations"]):
        try:
            _execute_mutation(client, operation)
        except Exception as exc:
            raise PartialApplyError(str(exc), completed=completed, unknown=[operation], pending=plan["operations"][index + 1:]) from exc
        completed.append(deepcopy(operation))
    result = deepcopy(plan)
    result.update({"mode": "apply", "completed": completed, "pending": []})
    return result


def evaluate_publication_gate(manifest: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    """Pure future API; intentionally not wired into existing paths."""
    try:
        validate_manifest(manifest)
        sku = _text(product.get("sku"), "product.sku")
        approved = next((x for x in manifest["products"] if x["sku"] == sku), None)
        if approved is None:
            return {"allowed": False, "reason": "unapproved-sku"}
        if product.get("supplier") != approved["supplier"]:
            return {"allowed": False, "reason": "supplier-mismatch"}
        if product.get("desired_controlled_tags") != approved["desired_controlled_tags"]:
            return {"allowed": False, "reason": "desired-tags-mismatch"}
        if product.get("taxonomy_lock") != approved["taxonomy_lock"]:
            return {"allowed": False, "reason": "taxonomy-lock-mismatch"}
        _validate_lock(product["taxonomy_lock"], desired=product["desired_controlled_tags"], supplier=product["supplier"], registry=_registry(manifest), label=sku)
        return {"allowed": True, "reason": "approved-taxonomy"}
    except ReconciliationError as exc:
        return {"allowed": False, "reason": "invalid-approved-taxonomy", "detail": str(exc)}


def apply_plan_in_memory(catalog: Iterable[dict[str, Any]], plan: dict[str, Any]) -> list[dict[str, Any]]:
    result = deepcopy(list(catalog))
    if any("product_gid" in op for op in plan["operations"]):
        by_gid = {item["product_gid"]: item for item in result}
        for op in plan["operations"]:
            product = by_gid[op["product_gid"]]
            if op["mutation"] == "tagsAdd" and op["tag"] not in product["tags"]: product["tags"].append(op["tag"])
            elif op["mutation"] == "tagsRemove" and op["tag"] in product["tags"]: product["tags"].remove(op["tag"])
            elif op["mutation"] == "collectionAddProducts" and op["collection_gid"] not in product["manual_collection_gids"]: product["manual_collection_gids"].append(op["collection_gid"])
            elif op["mutation"] == "collectionRemoveProducts" and op["collection_gid"] in product["manual_collection_gids"]: product["manual_collection_gids"].remove(op["collection_gid"])
        return result
    by_sku = {item["sku"]: item for item in result}
    for op in plan["operations"]:
        tags = by_sku[op["sku"]]["tags"]
        if op["mutation"] == "tagsAdd" and op["tag"] not in tags:
            tags.append(op["tag"])
        elif op["mutation"] == "tagsRemove" and op["tag"] in tags:
            tags.remove(op["tag"])
    return result
