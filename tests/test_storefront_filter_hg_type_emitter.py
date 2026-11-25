import hashlib
import json
import sqlite3
from copy import deepcopy

import pytest

from app.shopify.sync import (
    COLLECTION_CUSTOM_METAFIELD_KEYS,
    approved_tree_typed_metafield_rows,
    sync_approved_tree_typed_metafields,
)
from app.suppliers.category_locks import (
    CATEGORY_FIELDS,
    STOREFRONT_FILTER_HG_TYPE_VALUES_KEY,
    install_category_locks,
)


SKU = "VP-NEAR30-PROBE"
LEGACY_VALUES = {
    "product_group_name": "Hef- en hijsmateriaal",
    "execution": "Elektrische takels",
    "filter_values_json": '["Toebehoren"]',
    "subcategory_3": "",
    "subcategory_4": "",
    "subcategory_5": "",
    "productsoort": "Takelonderdeel",
    "collection_tags_json": '["cat-hef-en-hijsmateriaal"]',
}


def _row(raw=...):
    values = dict(LEGACY_VALUES)
    if raw is not ...:
        values[STOREFRONT_FILTER_HG_TYPE_VALUES_KEY] = raw
    provenance = {
        "locked": True,
        "source": "near-30-phase-a-test",
        "sku": SKU,
        "supplier_slug": "valkenpower",
        "supplier_vendor": "Valkenpower",
        "values": values,
    }
    return {
        "sku": SKU,
        "values_json": json.dumps(values, ensure_ascii=False),
        "provenance_json": json.dumps(provenance, ensure_ascii=False),
    }


def _digest(row):
    payload = "\0".join(
        (row["sku"], row["values_json"], row["provenance_json"])
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _product():
    return {
        "sku": SKU,
        "vendor": "Valkenpower",
        "_supplier_slug": "valkenpower",
        "sale_price": 99,
        "status": "ACTIVE",
        "product_group_name": "must-not-change",
        "execution": "must-not-change",
        "filter_values_json": '["must-not-change"]',
        "productsoort": "must-not-change",
    }


def _existing(current=None):
    nodes = [
        {
            "namespace": "custom",
            "key": "filter",
            "type": "single_line_text_field",
            "value": "Existing filter",
        },
        {
            "namespace": "custom",
            "key": "productsoort",
            "type": "single_line_text_field",
            "value": "Existing productsoort",
        },
    ]
    if current is not None:
        nodes.append({
            "namespace": "custom",
            "key": "filter_hg_type",
            "type": "list.single_line_text_field",
            "value": current,
        })
    return {
        "variant": {"id": "gid://shopify/ProductVariant/probe", "sku": SKU},
        "product": {
            "id": "gid://shopify/Product/probe",
            "vendor": "Valkenpower",
            "status": "ACTIVE",
            "tags": ["cat-existing", "filter-existing"],
            "category": {"id": "gid://shopify/TaxonomyCategory/existing"},
            "variants": {"nodes": [{"sku": SKU}]},
            "metafields": {"nodes": nodes},
        },
    }


def _emit(raw=..., existing=None):
    row = _row(raw)
    return approved_tree_typed_metafield_rows(
        _product(),
        existing or _existing(),
        explicit_approved_tree_write=True,
        lock_reader=lambda sku: row if sku == SKU else None,
        expected_lock_digest=_digest(row),
    )


def test_absent_key_is_inert_and_inputs_are_byte_identical():
    product = _product()
    existing = _existing()
    before = json.dumps([product, existing], ensure_ascii=False, sort_keys=True)
    row = _row()
    assert approved_tree_typed_metafield_rows(
        product,
        existing,
        explicit_approved_tree_write=True,
        lock_reader=lambda sku: row if sku == SKU else None,
        expected_lock_digest=_digest(row),
    ) == []
    after = json.dumps([product, existing], ensure_ascii=False, sort_keys=True)
    assert after == before


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["Takel"]', '["Takel"]'),
        (
            '["Takel","Staaldraadtakel","Takelaccessoire"]',
            '["Takel","Staaldraadtakel","Takelaccessoire"]',
        ),
    ],
)
def test_valid_values_emit_exact_one_typed_field_and_preserve_order(raw, expected):
    existing = _existing()
    before = deepcopy(existing)
    rows = _emit(raw, existing)
    assert rows == [{
        "metafields": [{
            "ownerId": "gid://shopify/Product/probe",
            "namespace": "custom",
            "key": "filter_hg_type",
            "type": "list.single_line_text_field",
            "value": expected,
        }],
    }]
    assert existing == before


@pytest.mark.parametrize(
    ("raw", "diagnostic"),
    [
        ("{", "JSON array"),
        ('{"value":"Takel"}', "non-empty JSON array"),
        ('"Takel"', "non-empty JSON array"),
        (None, "serialized JSON"),
        (["Takel"], "serialized JSON"),
        ("[]", "non-empty JSON array"),
        ('[""]', "non-empty strings"),
        ('["   "]', "non-empty strings"),
        ('["Takel",42]', "non-empty strings"),
        ('["Takel","Takel"]', "unique"),
        ('[ "Takel" ]', "canonical serialized JSON"),
        (r'["line\nfeed"]', "controls"),
        ('["bidi\u202eoverride"]', "format/surrogate"),
        (r'["surrogate\ud800"]', "format/surrogate"),
    ],
)
def test_invalid_values_fail_closed_with_clear_diagnostic(raw, diagnostic):
    with pytest.raises(
        ValueError,
        match=STOREFRONT_FILTER_HG_TYPE_VALUES_KEY + ".*" + diagnostic,
    ):
        _emit(raw)


def test_dry_run_and_real_writer_use_identical_validated_rows_only():
    raw = '["Takelonderdeel","Takelaccessoire"]'
    row = _row(raw)
    product = _product()
    existing = _existing()
    kwargs = {
        "lock_reader": lambda sku: row if sku == SKU else None,
        "expected_lock_digest": _digest(row),
    }

    class FakeClient:
        def __init__(self):
            self.calls = []

        def graphql(self, query, variables):
            self.calls.append((query, deepcopy(variables)))
            return {"metafieldsSet": {"metafields": [{"id": "probe"}], "userErrors": []}}

    client = FakeClient()
    dry_run_rows = sync_approved_tree_typed_metafields(
        None, product, existing, dry_run=True, **kwargs
    )
    real_rows = sync_approved_tree_typed_metafields(
        client, product, existing, dry_run=False, **kwargs
    )
    assert real_rows == dry_run_rows
    assert client.calls[0][1] == dry_run_rows[0]
    assert "metafieldsSet" in client.calls[0][0]
    assert {
        (field["namespace"], field["key"], field["type"])
        for row in real_rows for field in row["metafields"]
    } == {("custom", "filter_hg_type", "list.single_line_text_field")}


def test_idempotent_existing_value_has_no_destructive_fallback():
    assert _emit('["Takel"]', _existing('["Takel"]')) == []
    assert "filter_hg_type" in COLLECTION_CUSTOM_METAFIELD_KEYS


def test_product_level_emission_fails_closed_without_single_variant_proof():
    missing = _existing()
    missing["product"].pop("variants")
    with pytest.raises(ValueError, match="single-variant"):
        _emit('["Takel"]', missing)
    family = _existing()
    family["product"]["variants"]["nodes"].append({"sku": "VP-SIBLING"})
    with pytest.raises(ValueError, match="single-variant"):
        _emit('["Takel"]', family)


def test_malformed_optional_key_is_rejected_before_lock_database_write():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE products (sku TEXT PRIMARY KEY, raw_data_json TEXT, "
        + ",".join(f"{field} TEXT" for field in CATEGORY_FIELDS.values())
        + ")"
    )
    values = dict(LEGACY_VALUES)
    values[STOREFRONT_FILTER_HG_TYPE_VALUES_KEY] = '["dup","dup"]'
    with pytest.raises(ValueError, match="unique"):
        install_category_locks(conn, [{
            "sku": SKU,
            "provenance": {
                "source": "near-30-phase-a-test",
                "sku": SKU,
                "supplier_slug": "valkenpower",
                "supplier_vendor": "Valkenpower",
                "values": values,
            },
        }])
    assert conn.execute(
        "SELECT count(*) FROM category_import_locks"
    ).fetchone()[0] == 0
