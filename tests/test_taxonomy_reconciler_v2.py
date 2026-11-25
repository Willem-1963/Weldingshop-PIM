from copy import deepcopy
import inspect

import pytest

from app.shopify.taxonomy_reconciler import (
    PartialApplyError, ReconciliationError, apply_plan_in_memory, build_plan,
    confirmation_for_digest, manifest_digest, reconcile,
)

D = "a" * 64
P1 = "gid://shopify/Product/1001"
P2 = "gid://shopify/Product/1002"
V1 = "gid://shopify/ProductVariant/2001"
V2 = "gid://shopify/ProductVariant/2002"
C1 = "gid://shopify/Collection/3001"
C2 = "gid://shopify/Collection/3002"
PUBS = [f"gid://shopify/Publication/{4000+i}" for i in range(10)]
PROV = {"owner":"taxonomy","policy":"exact","version":"2","source_digest":D,"approval_provenance":"owner-approved"}


def base():
    manifest = {
        "schema_version": 2,
        "controlled_tag_registry": [
            {"tag":"route-primary","supplier_scope":["Vendor"],**PROV},
            {"tag":"route-secondary","supplier_scope":["Vendor"],**PROV},
            {"tag":"old-owned","supplier_scope":["Vendor"],**PROV},
        ],
        "collection_registry": [
            {"handle":"primary","collection_gid":C1,"rule_mode":"automatic","controlled_tag":"route-primary","updated_at":"2026-10-22T10:00:00Z",**PROV},
            {"handle":"secondary","collection_gid":C2,"rule_mode":"manual","updated_at":"2026-10-22T10:00:00Z",**PROV},
        ],
        "products": [{
            "product_gid":P1,"variants":[{"variant_gid":V1,"sku":"","barcode":"111"},{"variant_gid":V2,"sku":"MULTI","barcode":""}],
            "authority_class":"supplier-canonical","expected_vendor":"Vendor","expected_status":"ACTIVE",
            "expected_publication_gids":PUBS,"expected_updated_at":"2026-10-22T10:01:00Z",
            "expected_controlled_metafields":[{"namespace":"custom","key":"lock","type":"single_line_text_field","value":"yes"}],
            "desired_controlled_tags":[],"primary_route":"primary","protected_secondary_routes":["secondary"],
            "manual_collection_gids":[],"desired_future_routes":["future-not-registered"],
            "source":{"database":"db","table":"products","key":"one","row_digest":D,"provenance_digest":D},
        }],
        "tag_tombstones": [{"product_gid":P1,"tag":"old-owned","reason":"retired",**PROV}],
        "manual_collection_tombstones": [],
    }
    catalog = [{
        "product_gid":P1,"vendor":"Vendor","status":"ACTIVE","publication_gids":list(reversed(PUBS)),
        "variants":deepcopy(manifest["products"][0]["variants"]),"tags":["merchant","old-owned"],
        "controlled_metafields":deepcopy(manifest["products"][0]["expected_controlled_metafields"]),
        "manual_collection_gids":[],"updated_at":"2026-10-22T10:01:00Z",
    }]
    return manifest,catalog


def approval(manifest,catalog):
    plan=build_plan(manifest,catalog); md=manifest_digest(manifest)
    return md,plan["plan_sha256"],confirmation_for_digest(md,plan["plan_sha256"])


class Client:
    def __init__(self, fail_at=None): self.calls=[]; self.fail_at=fail_at
    def graphql(self,query,variables):
        self.calls.append((query,variables))
        if self.fail_at == len(self.calls): return {}
        for field in ("tagsAdd","tagsRemove"):
            if field in query: return {field:{"node":{"id":variables["id"]},"userErrors":[]}}
        for field in ("collectionAddProducts","collectionRemoveProducts"):
            if field in query: return {field:{"userErrors":[]}}
        raise AssertionError("forbidden mutation")


def test_blank_sku_and_multi_sku_stay_one_ordered_product_identity():
    manifest,catalog=base(); plan=build_plan(manifest,catalog)
    assert not plan["errors"] and {op["product_gid"] for op in plan["operations"]} == {P1}
    drift=deepcopy(catalog); drift[0]["variants"].reverse()
    assert build_plan(manifest,drift)["errors"] == [f"variant identity/order drift for {P1}"]


def test_duplicate_supplier_ownership_rejects():
    manifest,catalog=base(); other=deepcopy(manifest["products"][0]); other["product_gid"]=P2; other["variants"]=[deepcopy(other["variants"][0])]
    manifest["products"].append(other)
    with pytest.raises(ReconciliationError,match="duplicate supplier ownership"):
        build_plan(manifest,catalog)


@pytest.mark.parametrize("field,value,match", [
    ("vendor","Wrong","vendor mismatch"), ("status","DRAFT","status assertion"),
    ("updated_at","later","updatedAt CAS"),
])
def test_product_vendor_status_and_cas_drift_fail_closed(field,value,match):
    manifest,catalog=base(); catalog[0][field]=value
    assert match in build_plan(manifest,catalog)["errors"][0]


def test_product_gid_and_variant_drift_fail_closed():
    manifest,catalog=base(); catalog[0]["product_gid"]=P2
    assert build_plan(manifest,catalog)["errors"] == [f"missing catalog product_gid: {P1}"]
    catalog=base()[1]; catalog[0]["variants"][0]["barcode"]="drift"
    assert "variant identity/order drift" in build_plan(manifest,catalog)["errors"][0]


def test_exact_tombstone_and_manual_add_remove_preserve_unrelated_memberships_and_tags():
    manifest,catalog=base(); manifest["products"][0]["manual_collection_gids"]=[C2]
    extra="gid://shopify/Collection/3999"; catalog[0]["manual_collection_gids"]=[extra]
    plan=build_plan(manifest,catalog)
    assert {op["mutation"] for op in plan["operations"]} == {"tagsAdd","tagsRemove","collectionAddProducts"}
    projected=apply_plan_in_memory(catalog,plan)[0]
    assert "merchant" in projected["tags"] and extra in projected["manual_collection_gids"] and C2 in projected["manual_collection_gids"]
    manifest["products"][0]["manual_collection_gids"]=[]; manifest["products"][0]["protected_secondary_routes"]=[]; catalog[0]["manual_collection_gids"]=[C2,extra]
    manifest["manual_collection_tombstones"]=[{"product_gid":P1,"collection_gid":C2,"reason":"retired",**PROV}]
    ops=build_plan(manifest,catalog)["operations"]
    assert [op for op in ops if op["mutation"]=="collectionRemoveProducts"] == [{"mutation":"collectionRemoveProducts","collection_gid":C2,"product_gid":P1}]


def test_primary_secondary_preserved_and_future_route_inert_without_registry():
    manifest,catalog=base(); catalog[0]["tags"].append("route-primary"); catalog[0]["manual_collection_gids"].append(C2)
    plan=build_plan(manifest,catalog)
    assert plan["operations"] == [{"mutation":"tagsRemove","id":P1,"product_gid":P1,"tag":"old-owned"}]
    assert plan["inert_future_routes"] == {P1:["future-not-registered"]}


def test_ten_channel_guard_is_assertion_only_and_never_mutates_status_or_publications():
    manifest,catalog=base(); assert len(manifest["products"][0]["expected_publication_gids"])==10
    assert all(op["mutation"] not in {"productUpdate","publishablePublish","publishableUnpublish"} for op in build_plan(manifest,catalog)["operations"])
    catalog[0]["publication_gids"]=PUBS[:-1]
    plan=build_plan(manifest,catalog); assert "publication assertion" in plan["errors"][0] and plan["operations"]==[]


def test_idempotent_second_plan_and_no_forbidden_writes():
    manifest,catalog=base(); first=build_plan(manifest,catalog); second=build_plan(manifest,apply_plan_in_memory(catalog,first))
    assert second["operations"]==[]
    source=inspect.getsource(__import__("app.shopify.taxonomy_reconciler",fromlist=["x"]))
    assert "productUpdate(" not in source and "publishablePublish(" not in source and "metafieldsSet(" not in source


def test_digest_cas_partial_failure_and_rollback_journal():
    manifest,catalog=base(); manifest["products"][0]["manual_collection_gids"]=[C2]
    md,pd,token=approval(manifest,catalog); client=Client(fail_at=2)
    with pytest.raises(PartialApplyError) as caught:
        reconcile(manifest,catalog,client=client,apply=True,approved_manifest_sha256=md,approved_plan_sha256=pd,confirm=token,catalog_reader=lambda:deepcopy(catalog))
    state=caught.value.state
    assert len(state["completed"])==1 and len(state["unknown"])==1 and state["pending"]
    rollback=[]
    inverse={"tagsAdd":"tagsRemove","tagsRemove":"tagsAdd","collectionAddProducts":"collectionRemoveProducts","collectionRemoveProducts":"collectionAddProducts"}
    for op in reversed(state["completed"]): rollback.append({**op,"mutation":inverse[op["mutation"]]})
    assert rollback and rollback[0]["mutation"] in inverse


def test_fresh_variant_drift_blocks_before_first_write():
    manifest,catalog=base(); fresh=deepcopy(catalog); fresh[0]["variants"][0]["sku"]="changed"
    md,pd,token=approval(manifest,catalog); client=Client()
    with pytest.raises(ReconciliationError,match="stale plan"):
        reconcile(manifest,catalog,client=client,apply=True,approved_manifest_sha256=md,approved_plan_sha256=pd,confirm=token,catalog_reader=lambda:fresh)
    assert client.calls==[]


def test_v2_controlled_tag_supplier_scope_is_enforced_for_direct_and_route_tags():
    manifest,catalog=base()
    manifest["controlled_tag_registry"][0]["supplier_scope"]=["Other"]
    assert build_plan(manifest,catalog)["errors"] == [f"controlled tag supplier scope mismatch for {P1}"]
    manifest,catalog=base(); manifest["products"][0]["desired_controlled_tags"]=["route-primary"]; manifest["controlled_tag_registry"][0]["supplier_scope"]=["Other"]
    with pytest.raises(ReconciliationError,match="outside registry scope"):
        build_plan(manifest,catalog)


def test_partial_failure_contains_exact_reverse_order_rollback_journal():
    manifest,catalog=base(); manifest["products"][0]["manual_collection_gids"]=[C2]
    md,pd,token=approval(manifest,catalog); client=Client(fail_at=3)
    with pytest.raises(PartialApplyError) as caught:
        reconcile(manifest,catalog,client=client,apply=True,approved_manifest_sha256=md,approved_plan_sha256=pd,confirm=token,catalog_reader=lambda:deepcopy(catalog))
    completed=caught.value.state["completed"]; rollback=caught.value.state["rollback"]
    inverse={"tagsAdd":"tagsRemove","tagsRemove":"tagsAdd","collectionAddProducts":"collectionRemoveProducts","collectionRemoveProducts":"collectionAddProducts"}
    assert rollback == [{**op,"mutation":inverse[op["mutation"]]} for op in reversed(completed)]


def test_cli_deduplicates_affected_product_ids_and_contains_no_forbidden_mutations():
    import scripts.reconcile_taxonomy as cli
    source=inspect.getsource(cli)
    assert 'affected_ids = sorted({' in source
    for forbidden in ("productUpdate(","publishablePublish(","publishableUnpublish(","metafieldsSet("):
        assert forbidden not in source


def test_collection_registry_cas_accepts_exact_state_and_rejects_drift():
    from scripts.reconcile_taxonomy import _assert_collection_registry
    manifest,_=base()
    nodes=[]
    for item in manifest["collection_registry"]:
        rules=None if item["rule_mode"]=="manual" else {"appliedDisjunctively":False,"rules":[{"column":"TAG","relation":"EQUALS","condition":item["controlled_tag"]}]}
        nodes.append({"id":item["collection_gid"],"handle":item["handle"],"updatedAt":item["updated_at"],"ruleSet":rules})
    class RegistryClient:
        def __init__(self,nodes): self.nodes=nodes
        def graphql(self,query,variables): return {"nodes":deepcopy(self.nodes)}
    _assert_collection_registry(RegistryClient(nodes),manifest)
    nodes[0]["updatedAt"]="drift"
    with pytest.raises(RuntimeError,match="CAS drift"):
        _assert_collection_registry(RegistryClient(nodes),manifest)
