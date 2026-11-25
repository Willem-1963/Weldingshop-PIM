from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.shopify.taxonomy_reconciler import (
    PartialApplyError,
    ReconciliationError,
    apply_plan_in_memory,
    build_plan,
    confirmation_for_digest,
    evaluate_publication_gate,
    load_manifest,
    manifest_digest,
    reconcile,
    strict_readonly_decrypt,
    supplier_alias_map,
)

FIXTURES = Path(__file__).parent / "fixtures" / "taxonomy"


def fixture():
    manifest = load_manifest(FIXTURES / "near25_cat_pompen_manifest.json")
    wrapper = json.loads((FIXTURES / "near25_scoped_curated_catalog.json").read_text())
    assert wrapper["fixture_metadata"]["kind"] == "scoped-curated-test-fixture"
    return manifest, wrapper["products"]


def approval(manifest, catalog):
    digest = manifest_digest(manifest)
    plan_digest = build_plan(manifest, catalog)["plan_sha256"]
    return digest, plan_digest, confirmation_for_digest(digest, plan_digest)


class ResponseClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def graphql(self, query, variables):
        self.calls.append((query, variables))
        if self.responses:
            response = self.responses.pop(0)
            if callable(response):
                return response(query, variables)
            return response
        field = "tagsRemove" if "tagsRemove" in query else "tagsAdd"
        return {field: {"node": {"id": variables["id"]}, "userErrors": []}}


def apply_with(manifest, catalog, client, fresh=None, approved=None, approved_plan=None, confirm=None):
    digest, plan_digest, token = approval(manifest, catalog)
    return reconcile(
        manifest,
        catalog,
        client=client,
        apply=True,
        approved_manifest_sha256=approved or digest,
        approved_plan_sha256=approved_plan or plan_digest,
        confirm=confirm or token,
        catalog_reader=lambda: deepcopy(fresh if fresh is not None else catalog),
    )


def test_scoped_curated_fixture_has_exact_expected_plan_without_mutation():
    manifest, catalog = fixture()
    before = deepcopy(catalog)
    plan = reconcile(manifest, catalog)
    assert plan["mode"] == "dry-run"
    assert plan["summary"] == {"additions": 16, "removals": 2, "noops": 56, "unexpected": 0, "errors": 0}
    assert len(plan["catalog_sha256"]) == len(plan["plan_sha256"]) == 64
    assert catalog == before
    assert {op["mutation"] for op in plan["operations"]} == {"tagsAdd", "tagsRemove"}


def test_existing_good_taxonomy_and_merchant_tags_are_unchanged():
    manifest, catalog = fixture()
    merchant = {p["sku"]: [t for t in p["tags"] if t != "cat-pompen"] for p in catalog}
    projected = apply_plan_in_memory(catalog, build_plan(manifest, catalog))
    assert {p["sku"]: [t for t in p["tags"] if t != "cat-pompen"] for p in projected} == merchant


def test_omission_cannot_remove_registered_tag():
    manifest, catalog = fixture()
    extra = deepcopy(manifest["controlled_tag_registry"][0])
    extra["tag"] = "cat-other-exact"
    manifest["controlled_tag_registry"].append(extra)
    sku = manifest["products"][0]["sku"]
    next(p for p in catalog if p["sku"] == sku)["tags"].append(extra["tag"])
    assert not any(op["tag"] == extra["tag"] for op in build_plan(manifest, catalog)["operations"])


def test_exact_tombstone_removal_preserves_similar_and_merchant_tags():
    manifest, catalog = fixture()
    sku = manifest["tombstones"][0]["sku"]
    product = next(p for p in catalog if p["sku"] == sku)
    product["tags"].extend(["cat-pompen-child", "merchant-keep"])
    removal = [op for op in build_plan(manifest, catalog)["operations"] if op["sku"] == sku]
    assert removal == [{"mutation": "tagsRemove", "id": product["id"], "sku": sku, "tag": "cat-pompen"}]


def test_fabricated_or_incomplete_ledger_provenance_rejects():
    manifest, catalog = fixture()
    reg = manifest["controlled_tag_registry"][0]
    manifest["prior_projector_owned_ledger"] = [{"sku": manifest["products"][0]["sku"], "supplier": "valkenpower", "tag": "cat-pompen", "owner": reg["owner"]}]
    with pytest.raises(ReconciliationError, match="policy"):
        build_plan(manifest, catalog)


def test_exact_registry_ledger_can_remove_only_exact_owned_tag():
    manifest, catalog = fixture()
    extra = deepcopy(manifest["controlled_tag_registry"][0])
    extra["tag"] = "cat-ledger-owned"
    manifest["controlled_tag_registry"].append(extra)
    sku = manifest["products"][0]["sku"]
    product = next(p for p in catalog if p["sku"] == sku)
    product["tags"].extend([extra["tag"], extra["tag"] + "-child"])
    manifest["prior_projector_owned_ledger"] = [{"sku": sku, "supplier": "valkenpower", "tag": extra["tag"], **{f: extra[f] for f in ("owner", "policy", "version", "source_digest", "approval_provenance")}}]
    ops = [op for op in build_plan(manifest, catalog)["operations"] if op["sku"] == sku]
    assert ops == [{"mutation": "tagsRemove", "id": product["id"], "sku": sku, "tag": extra["tag"]}]


def test_malformed_lock_unknown_tag_and_duplicate_owner_sku_reject():
    manifest, catalog = fixture()
    malformed = deepcopy(manifest)
    malformed["products"][0]["taxonomy_lock"]["locked"] = False
    with pytest.raises(ReconciliationError, match="malformed or unlocked"):
        build_plan(malformed, catalog)
    unknown = deepcopy(manifest)
    unknown["products"][0]["desired_controlled_tags"] = ["cat-not-approved"]
    unknown["products"][0]["taxonomy_lock"]["approved_tags"] = ["cat-not-approved"]
    with pytest.raises(ReconciliationError, match="unknown controlled tag"):
        build_plan(unknown, catalog)
    duplicated = deepcopy(catalog) + [deepcopy(catalog[0])]
    with pytest.raises(ReconciliationError, match="duplicate catalog SKU"):
        build_plan(manifest, duplicated)


def test_second_projection_has_zero_mutations():
    manifest, catalog = fixture()
    second = build_plan(manifest, apply_plan_in_memory(catalog, build_plan(manifest, catalog)))
    assert second["summary"]["additions"] == second["summary"]["removals"] == 0
    assert second["operations"] == []


def test_publication_gate_requires_exact_manifest_sku_supplier_tags_and_lock():
    manifest, _ = fixture()
    approved = deepcopy(manifest["products"][0])
    assert evaluate_publication_gate(manifest, approved) == {"allowed": True, "reason": "approved-taxonomy"}
    unapproved = deepcopy(approved); unapproved["sku"] = "NOT-APPROVED"
    assert evaluate_publication_gate(manifest, unapproved)["reason"] == "unapproved-sku"
    wrong_supplier = deepcopy(approved); wrong_supplier["supplier"] = "other"
    assert evaluate_publication_gate(manifest, wrong_supplier)["reason"] == "supplier-mismatch"
    wrong_tags = deepcopy(approved); wrong_tags["desired_controlled_tags"] = ["cat-other"]
    assert evaluate_publication_gate(manifest, wrong_tags)["reason"] == "desired-tags-mismatch"
    wrong_lock = deepcopy(approved); wrong_lock["taxonomy_lock"]["version"] = "fabricated"
    assert evaluate_publication_gate(manifest, wrong_lock)["reason"] == "taxonomy-lock-mismatch"


def test_outside_owner_scope_is_untouched():
    manifest, catalog = fixture()
    outside = next(p for p in catalog if p["supplier"] == "other-supplier")
    projected = apply_plan_in_memory(catalog, build_plan(manifest, catalog))
    assert next(p for p in projected if p["sku"] == outside["sku"]) == outside


def test_supplier_aliases_are_normalized_and_collisions_reject():
    manifest, _ = fixture()
    assert supplier_alias_map(manifest) == {"valkenpower": "valkenpower"}
    manifest["supplier_alias_registry"].append({"alias": "  VALKENPOWER ", "supplier": "valkenpower"})
    with pytest.raises(ReconciliationError, match="alias collision"):
        supplier_alias_map(manifest)


def test_manifest_tampering_and_unbound_confirmation_reject_before_client_call():
    manifest, catalog = fixture()
    digest, plan_digest, token = approval(manifest, catalog)
    manifest["fixture_metadata"]["scope"] += " tampered"
    client = ResponseClient()
    with pytest.raises(ReconciliationError, match="approved manifest sha256"):
        reconcile(manifest, catalog, client=client, apply=True, approved_manifest_sha256=digest, approved_plan_sha256=plan_digest, confirm=token, catalog_reader=lambda: catalog)
    assert client.calls == []
    new_digest = manifest_digest(manifest)
    with pytest.raises(ReconciliationError, match="not bound"):
        reconcile(manifest, catalog, client=client, apply=True, approved_manifest_sha256=new_digest, approved_plan_sha256=build_plan(manifest, catalog)["plan_sha256"], confirm=token, catalog_reader=lambda: catalog)
    assert client.calls == []



def test_independent_plan_digest_mismatch_rejects_before_client_call():
    manifest, catalog = fixture()
    digest, plan_digest, _ = approval(manifest, catalog)
    wrong_plan = "0" * 64
    client = ResponseClient()
    with pytest.raises(ReconciliationError, match="approved plan sha256"):
        reconcile(
            manifest, catalog, client=client, apply=True,
            approved_manifest_sha256=digest, approved_plan_sha256=wrong_plan,
            confirm=confirmation_for_digest(digest, wrong_plan),
            catalog_reader=lambda: catalog,
        )
    assert client.calls == []

def test_missing_decrypt_key_creates_no_files_or_directories(tmp_path):
    missing = tmp_path / "absent-parent" / "missing.key"
    before = list(tmp_path.rglob("*"))
    with pytest.raises(ReconciliationError, match="existing secret key"):
        strict_readonly_decrypt("ciphertext", missing)
    assert list(tmp_path.rglob("*")) == before


def test_concurrent_affected_product_drift_aborts_before_first_mutation():
    manifest, catalog = fixture()
    fresh = deepcopy(catalog)
    affected_sku = build_plan(manifest, catalog)["operations"][0]["sku"]
    target = next(p for p in fresh if p["sku"] == affected_sku)
    target["tags"].append("merchant-concurrent-change")
    client = ResponseClient()
    with pytest.raises(ReconciliationError, match="stale plan"):
        apply_with(manifest, catalog, client, fresh=fresh)
    assert client.calls == []


@pytest.mark.parametrize("response", [
    {},
    {"tagsAdd": {}},
    {"tagsAdd": {"node": {"id": "ignored"}}},
    {"tagsAdd": {"node": {"id": "wrong"}, "userErrors": []}},
    {"tagsAdd": {"node": {"id": "ignored"}, "userErrors": None}},
])
def test_missing_or_malformed_mutation_response_reports_all_pending(response):
    manifest, catalog = fixture()
    client = ResponseClient([response])
    with pytest.raises(PartialApplyError) as caught:
        apply_with(manifest, catalog, client)
    state = caught.value.state
    operations = build_plan(manifest, catalog)["operations"]
    assert state["completed"] == []
    assert state["unknown"] == operations[:1]
    assert state["pending"] == operations[1:]


def test_partial_response_reports_precise_completed_and_pending_operations():
    manifest, catalog = fixture()
    good = lambda query, variables: {("tagsRemove" if "tagsRemove" in query else "tagsAdd"): {"node": {"id": variables["id"]}, "userErrors": []}}
    client = ResponseClient([good, {}])
    plan = build_plan(manifest, catalog)
    with pytest.raises(PartialApplyError) as caught:
        apply_with(manifest, catalog, client)
    assert caught.value.state["completed"] == plan["operations"][:1]
    assert caught.value.state["unknown"] == plan["operations"][1:2]
    assert caught.value.state["pending"] == plan["operations"][2:]


def test_successful_fake_apply_uses_only_incremental_mutations():
    manifest, catalog = fixture()
    client = ResponseClient()
    result = apply_with(manifest, catalog, client)
    assert result["pending"] == []
    assert result["completed"] == build_plan(manifest, catalog)["operations"]
    assert all(("tagsAdd" in query) ^ ("tagsRemove" in query) for query, _ in client.calls)
    assert all(set(variables) == {"id", "tags"} and len(variables["tags"]) == 1 for _, variables in client.calls)
