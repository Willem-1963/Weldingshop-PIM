import hashlib
import json
import sqlite3

from app.suppliers.category_locks import (
    CATEGORY_FIELDS, TYPED_CUSTOM_METAFIELD_TYPES, TYPED_CUSTOM_VALUE_KEYS,
    TYPED_LIST_MAX_ENCODED_UTF8_BYTES, TYPED_LIST_MAX_ITEM_UTF8_BYTES,
    TYPED_LIST_MAX_ITEMS, install_category_locks, typed_metafields_from_authority,
    verified_category_import_lock,
)
from app.shopify.sync import (
    COLLECTION_CUSTOM_METAFIELD_KEYS, _input,
    approved_tree_typed_metafield_rows,
)


def test_import_and_enrichment_cannot_replace_locked_fields_or_empty_levels():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA recursive_triggers=ON')
    conn.execute('CREATE TABLE products (sku TEXT PRIMARY KEY, raw_data_json TEXT, price REAL, '
                 + ','.join(f'{field} TEXT' for field in CATEGORY_FIELDS.values()) + ')')
    conn.execute("INSERT INTO products(sku,raw_data_json,price) VALUES ('VP-A','{}',10),('VP-B','{}',10)")
    values = dict(zip(CATEGORY_FIELDS.values(), ['Werkplaats', 'Handgereedschap', '["Sleutelsets"]', '', '', '']))
    install_category_locks(conn, [{'sku': 'VP-A', 'provenance': {'values': values}}])
    # A source import replaces raw data; an enrichment writer fills empty levels.
    conn.execute("UPDATE products SET product_group_name='Fout',filter_values_json='[]',"
                 "execution='Afgeleid',subcategory_3='Ongewenst',raw_data_json='{\"stock\":5}',price=20")
    saved = dict(conn.execute("SELECT * FROM products WHERE sku='VP-A'").fetchone())
    assert all(saved[field] == value for field, value in values.items())
    assert saved['price'] == 20
    assert json.loads(saved['raw_data_json'])['stock'] == 5
    assert json.loads(saved['raw_data_json'])['category_file_import']['locked'] is True
    assert conn.execute("SELECT product_group_name FROM products WHERE sku='VP-B'").fetchone()[0] == 'Fout'
    # The supplier feed's INSERT ... ON CONFLICT UPDATE path is protected too.
    conn.execute("INSERT INTO products(sku,raw_data_json,product_group_name,subcategory_4) "
                 "VALUES ('VP-A','{}','Nieuw','Afgeleid') ON CONFLICT(sku) DO UPDATE SET "
                 "raw_data_json=excluded.raw_data_json,product_group_name=excluded.product_group_name,"
                 "subcategory_4=excluded.subcategory_4")
    saved = dict(conn.execute("SELECT * FROM products WHERE sku='VP-A'").fetchone())
    assert all(saved[field] == value for field, value in values.items())


def test_normal_shopify_sync_uses_approved_values_over_conflicting_mappings():
    values = dict(zip(CATEGORY_FIELDS.values(), ['Werkplaats', 'Handgereedschap', '["Sleutelsets"]', '', '', '']))
    product = {'sku': 'VP-A', '_supplier_slug': 'valkenpower', 'sale_price': 20, 'images': [],
               'product_group_name': 'Afgeleid', 'subcategory_3': 'Fout',
               '_raw_data': {'category_file_import': {'locked': True, 'values': values}, 'wrong': 'Fout'},
               '_shopify_metafield_mapping': {
                   key: {'owner': 'product', 'namespace': 'custom', 'key': key,
                         'type': 'single_line_text_field', 'source_field': 'wrong'}
                   for key in ['productgroep', 'subcategorie_3', 'ander_veld']}}
    fields = {item['key']: item['value'] for item in _input(product, None)['metafields']}
    assert fields['productgroep'] == 'Werkplaats'
    assert fields['uitvoering'] == 'Handgereedschap'
    assert fields['filter'] == 'Sleutelsets'
    assert 'subcategorie_3' not in fields
    assert fields['ander_veld'] == 'Fout'


NEAR_27_CS_BLADES = (
    "VP-CS225BALPH", "VP-CS250BALPH", "VP-CS350BALPH", "VP-CS350B",
)
CANONICAL_HG = '["Zaagbladen en zaagbanden"]'


def _values(typed_value=CANONICAL_HG):
    values = dict(zip(CATEGORY_FIELDS.values(), [
        "Hout- en metaalbewerking", "Metaalbewerking", '["Afkortzagen"]', "", "", "",
    ]))
    if typed_value is not None:
        values["filter_hg_type"] = typed_value
    return values


def _near_27_product(sku=NEAR_27_CS_BLADES[0], vendor="Valkenpower", slug="valkenpower"):
    return {
        "sku": sku, "vendor": vendor, "_supplier_slug": slug,
        # Deliberately forged: this object is never accepted as lock authority.
        "_raw_data": {"category_file_import": {
            "locked": True, "source": "self-attested-forgery", "values": _values(),
        }},
    }


def _near_27_existing(sku=NEAR_27_CS_BLADES[0], hg_value=None, vendor="Valkenpower"):
    nodes = [{
        "namespace": "custom", "key": "filter",
        "type": "single_line_text_field", "value": "Afkortzagen",
    }]
    if hg_value is not None:
        nodes.append({
            "namespace": "custom", "key": "filter_hg_type",
            "type": "list.single_line_text_field", "value": hg_value,
        })
    return {
        "variant": {"sku": sku},
        "product": {
            "id": "gid://shopify/Product/near27", "vendor": vendor,
            "metafields": {"nodes": nodes},
        },
    }


def _lock_row(sku=NEAR_27_CS_BLADES[0], typed_value=CANONICAL_HG, **provenance_overrides):
    values = _values(typed_value)
    provenance = {
        "locked": True, "source": "near-27-reviewed-db-lock",
        "sku": sku, "supplier_slug": "valkenpower",
        "supplier_vendor": "Valkenpower", "values": values,
        **provenance_overrides,
    }
    return {
        "sku": sku,
        "values_json": json.dumps(values, ensure_ascii=False),
        "provenance_json": json.dumps(provenance, ensure_ascii=False),
    }


def _digest(row):
    payload = "\0".join((
        row["sku"], row["values_json"], row["provenance_json"],
    )).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _reader(row):
    return lambda sku: row if sku == row["sku"] else None


def _plan(product=None, existing=None, row=None, digest=None):
    product = product or _near_27_product()
    existing = existing or _near_27_existing()
    row = row or _lock_row()
    return approved_tree_typed_metafield_rows(
        product, existing, explicit_approved_tree_write=True,
        lock_reader=_reader(row), expected_lock_digest=digest or _digest(row),
    )


def _assert_rejected(call):
    try:
        call()
    except (AttributeError, TypeError, ValueError):
        return
    raise AssertionError("unsafe input was accepted")


def test_near_27_fixtures_are_four_blades_without_machine_role_changes():
    assert len(NEAR_27_CS_BLADES) == len(set(NEAR_27_CS_BLADES)) == 4
    for sku in NEAR_27_CS_BLADES:
        row = _lock_row(sku=sku)
        authority = verified_category_import_lock(
            _reader(row), sku=sku, supplier_slug="valkenpower",
            supplier_vendor="Valkenpower", expected_digest=_digest(row),
        )
        assert authority.values["filter_values_json"] == '["Afkortzagen"]'
        assert typed_metafields_from_authority(authority)["custom.filter_hg_type"] == {
            "type": "list.single_line_text_field", "value": CANONICAL_HG,
        }


def test_self_attested_raw_data_and_source_never_authorize():
    product = _near_27_product()
    assert approved_tree_typed_metafield_rows(product, _near_27_existing()) == []
    _assert_rejected(lambda: approved_tree_typed_metafield_rows(
        product, _near_27_existing(), explicit_approved_tree_write=True,
    ))
    _assert_rejected(lambda: typed_metafields_from_authority(
        product["_raw_data"]["category_file_import"]
    ))


def test_db_lock_authority_rejects_missing_wrong_and_stale_rows():
    row = _lock_row()
    product = _near_27_product()
    existing = _near_27_existing()
    _assert_rejected(lambda: approved_tree_typed_metafield_rows(
        product, existing, explicit_approved_tree_write=True,
        lock_reader=lambda sku: None, expected_lock_digest=_digest(row),
    ))
    wrong = dict(row, sku="VP-WRONG")
    _assert_rejected(lambda: approved_tree_typed_metafield_rows(
        product, existing, explicit_approved_tree_write=True,
        lock_reader=lambda sku: wrong, expected_lock_digest=_digest(row),
    ))
    _assert_rejected(lambda: _plan(row=row, digest="0" * 64))
    stale = _lock_row(typed_value='["Stale"]')
    _assert_rejected(lambda: _plan(row=stale, digest=_digest(row)))


def test_exact_supplier_vendor_slug_and_lock_ownership_are_required():
    cases = [
        (_near_27_product(vendor=""), _near_27_existing(), _lock_row()),
        (_near_27_product(slug=""), _near_27_existing(), _lock_row()),
        (_near_27_product(slug=" valkenpower"), _near_27_existing(), _lock_row()),
        (_near_27_product(vendor="Valkenpower "), _near_27_existing(), _lock_row()),
        (_near_27_product(), _near_27_existing(vendor=""), _lock_row()),
        (_near_27_product(), _near_27_existing(vendor=" Valkenpower"), _lock_row()),
        (_near_27_product(vendor="valkenpower"), _near_27_existing(), _lock_row()),
        (_near_27_product(), _near_27_existing(vendor="Other"), _lock_row()),
        (_near_27_product(), _near_27_existing(), _lock_row(supplier_slug="other")),
        (_near_27_product(), _near_27_existing(), _lock_row(supplier_vendor="Other")),
        (_near_27_product(), _near_27_existing(), _lock_row(sku="VP-WRONG")),
    ]
    for product, existing, row in cases:
        _assert_rejected(lambda p=product, e=existing, r=row: _plan(p, e, r))


def test_strict_canonical_json_nfc_controls_duplicates_and_limits():
    invalid = [
        "Zaagbladen", '"Zaagbladen"', "null", "{", "[]", '[""]',
        '[" leading"]', '["trailing "]', '[ "space" ]', '["pipe|joined"]',
        '["line\nfeed"]', '["null\u0000char"]', '[null]', '[["nested"]]',
        '["dup","dup"]', '["é","e\u0301"]', json.dumps(["x" * 256]),
        json.dumps([str(i) for i in range(129)], separators=(",", ":")),
    ]
    large = ["x" * 250 + f"{index:03d}" for index in range(128)]
    assert all(len(item.encode("utf-8")) <= TYPED_LIST_MAX_ITEM_UTF8_BYTES for item in large)
    oversized_total = json.dumps(large, separators=(",", ":"))
    assert len(oversized_total.encode("utf-8")) > TYPED_LIST_MAX_ENCODED_UTF8_BYTES
    invalid.append(oversized_total)
    for raw in invalid:
        row = _lock_row(typed_value=raw)
        _assert_rejected(lambda r=row: _plan(row=r))
    assert TYPED_LIST_MAX_ITEMS == 128
    assert TYPED_LIST_MAX_ITEM_UTF8_BYTES == 255
    assert TYPED_LIST_MAX_ENCODED_UTF8_BYTES == 32768


def test_typed_list_rejects_every_newline_separator_with_valid_unicode_adjacent():
    forbidden = {
        "CR": "\r",
        "LF": "\n",
        "NEXT LINE": "\u0085",
        "LINE SEPARATOR": "\u2028",
        "PARAGRAPH SEPARATOR": "\u2029",
    }
    for name, character in forbidden.items():
        raw = json.dumps(
            [f"é界{character}العربية"], ensure_ascii=False, separators=(",", ":"),
        )
        row = _lock_row(typed_value=raw)
        _assert_rejected(lambda r=row: _plan(row=r))

    # Adjacent multilingual letters, combining marks, symbols and normal spaces
    # remain valid; only the explicit Cc/Zl/Zp categories above are forbidden.
    valid = "Café 世界 العربية देवनागरी 🛠️"
    raw = json.dumps([valid], ensure_ascii=False, separators=(",", ":"))
    rows = _plan(row=_lock_row(typed_value=raw))
    assert rows[0]["metafields"][0]["value"] == raw


def test_typed_registries_are_immutable_and_unknown_key_type_fail_closed():
    _assert_rejected(lambda: TYPED_CUSTOM_METAFIELD_TYPES.__setitem__("custom.x", "json"))
    _assert_rejected(lambda: TYPED_CUSTOM_VALUE_KEYS.__setitem__("x", "custom.x"))
    assert isinstance(COLLECTION_CUSTOM_METAFIELD_KEYS, frozenset)
    _assert_rejected(lambda: COLLECTION_CUSTOM_METAFIELD_KEYS.add("unknown"))
    values = _values(None)
    values["filter_hg_unknown"] = '["x"]'
    row = _lock_row(typed_value=None)
    row["values_json"] = json.dumps(values)
    provenance = json.loads(row["provenance_json"])
    provenance["values"] = values
    row["provenance_json"] = json.dumps(provenance)
    _assert_rejected(lambda: _plan(row=row))
    values = _values(None)
    values["typed_custom_metafields"] = {
        "custom.filter_hg_type": {"type": "json", "value": '["x"]'}
    }
    row = _lock_row(typed_value=None)
    row["values_json"] = json.dumps(values)
    provenance = json.loads(row["provenance_json"])
    provenance["values"] = values
    row["provenance_json"] = json.dumps(provenance)
    _assert_rejected(lambda: _plan(row=row))


def test_explicit_plan_is_canonical_idempotent_and_preserves_scalar_filter():
    existing = _near_27_existing()
    rows = _plan(existing=existing)
    assert rows == [{"metafields": [{
        "ownerId": "gid://shopify/Product/near27", "namespace": "custom",
        "key": "filter_hg_type", "type": "list.single_line_text_field",
        "value": CANONICAL_HG,
    }]}]
    assert existing["product"]["metafields"]["nodes"][0]["value"] == "Afkortzagen"
    assert _plan(existing=_near_27_existing(hg_value=CANONICAL_HG)) == []


def test_normal_sync_remains_unwired_and_scalar_output_unchanged():
    product = _near_27_product()
    product.update({"sale_price": 20, "images": []})
    built = _input(product, None)
    fields = {(item["namespace"], item["key"]): item for item in built["metafields"]}
    assert ("custom", "filter_hg_type") not in fields
    assert fields[("custom", "filter")] == {
        "namespace": "custom", "key": "filter", "type": "single_line_text_field",
        "value": "Afkortzagen",
    }


def test_install_lock_validates_typed_value_before_database_write():
    conn = sqlite3.connect(":memory:")
    conn.execute('CREATE TABLE products (sku TEXT PRIMARY KEY, raw_data_json TEXT, ' +
                 ','.join(f'{field} TEXT' for field in CATEGORY_FIELDS.values()) + ')')
    values = _values("pipe|joined")
    _assert_rejected(lambda: install_category_locks(conn, [{
        "sku": NEAR_27_CS_BLADES[0],
        "provenance": {"source": "near-27-test", "values": values},
    }]))
    assert conn.execute("SELECT count(*) FROM category_import_locks").fetchone()[0] == 0


def test_installed_lock_preserves_canonical_typed_value_in_provenance():
    conn = sqlite3.connect(":memory:")
    conn.execute('PRAGMA recursive_triggers=ON')
    conn.execute('CREATE TABLE products (sku TEXT PRIMARY KEY, raw_data_json TEXT, ' +
                 ','.join(f'{field} TEXT' for field in CATEGORY_FIELDS.values()) + ')')
    conn.execute("INSERT INTO products(sku,raw_data_json) VALUES (?, '{}')",
                 (NEAR_27_CS_BLADES[0],))
    row = _lock_row()
    provenance = json.loads(row["provenance_json"])
    install_category_locks(conn, [{
        "sku": NEAR_27_CS_BLADES[0], "provenance": provenance,
    }])
    conn.execute("UPDATE products SET raw_data_json=? WHERE sku=?",
                 ('{"feed":true}', NEAR_27_CS_BLADES[0]))
    raw = json.loads(conn.execute(
        "SELECT raw_data_json FROM products WHERE sku=?", (NEAR_27_CS_BLADES[0],)
    ).fetchone()[0])
    assert raw["feed"] is True
    assert raw["category_file_import"]["values"]["filter_hg_type"] == CANONICAL_HG
