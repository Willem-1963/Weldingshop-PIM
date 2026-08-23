import json
import sqlite3

from app.suppliers import complementary_products as module


def _database(tmp_path, count=25):
    path = tmp_path / "supplier.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE products(
                sku TEXT PRIMARY KEY,source_title TEXT,brand TEXT,category TEXT,
                category_full TEXT,product_type TEXT,product_group_name TEXT,
                filter_values_json TEXT,source_present INTEGER
            )"""
        )
        connection.executemany(
            "INSERT INTO products VALUES(?,?,?,?,?,?,?,?,1)",
            [
                (f"SKU-{index:02d}", f"Reduceerventiel stikstof {index}",
                 "Kentie", "Autogeen", "Autogeen", "Reduceerventiel",
                 "Reduceerventiel", json.dumps(["Stikstof", str(index % 3)]))
                for index in range(count)
            ],
        )
    return path


def test_balanced_links_never_exceed_shopify_limit(tmp_path, monkeypatch):
    path = _database(tmp_path)
    monkeypatch.setattr(module, "init_supplier_database", lambda slug: path)
    result = module.generate_complementary_products("demo")
    assert result["groups_over_limit"] == 1
    with sqlite3.connect(path) as connection:
        counts = connection.execute(
            "SELECT source_sku,COUNT(*) FROM complementary_product_links GROUP BY source_sku"
        ).fetchall()
        assert len(counts) == 25
        assert {count for _, count in counts} == {10}
        assert connection.execute(
            "SELECT COUNT(*) FROM complementary_product_links WHERE source_sku=target_sku"
        ).fetchone()[0] == 0
        inbound = connection.execute(
            "SELECT target_sku,COUNT(*) FROM complementary_product_links GROUP BY target_sku"
        ).fetchall()
        assert len(inbound) == 25
        assert max(count for _, count in inbound) - min(count for _, count in inbound) <= 1


def test_small_group_links_every_other_product(tmp_path, monkeypatch):
    path = _database(tmp_path, count=6)
    monkeypatch.setattr(module, "init_supplier_database", lambda slug: path)
    module.generate_complementary_products("demo")
    with sqlite3.connect(path) as connection:
        counts = dict(connection.execute(
            "SELECT source_sku,COUNT(*) FROM complementary_product_links GROUP BY source_sku"
        ))
    assert set(counts.values()) == {5}


def test_preview_contains_actionable_group_and_link_details(tmp_path, monkeypatch):
    path = _database(tmp_path, count=6)
    monkeypatch.setattr(module, "init_supplier_database", lambda slug: path)

    preview = module.build_complementary_preview("demo")

    assert preview["group_details"] == [{
        "product_group": "Reduceerventiel",
        "products": 6,
        "proposed_links": 30,
        "over_shopify_limit": False,
        "example_products": (
            "Reduceerventiel stikstof 0, Reduceerventiel stikstof 3, "
            "Reduceerventiel stikstof 1"
        ),
    }]
    first = preview["proposals"][0]
    assert first["product_group"] == "Reduceerventiel"
    assert first["source_title"].startswith("Reduceerventiel stikstof")
    assert first["target_title"].startswith("Reduceerventiel stikstof")


def test_safe_workflow_enforces_import_generate_upload_order(monkeypatch):
    calls = []
    client = object()
    monkeypatch.setattr(
        module, "import_shopify_complementary_products",
        lambda slug, client=None: calls.append(("import", slug, client)) or {"links_imported": 2},
    )
    monkeypatch.setattr(
        module, "generate_complementary_products",
        lambda slug: calls.append(("generate", slug)) or {"links": 12},
    )
    monkeypatch.setattr(
        module, "sync_complementary_products_to_shopify",
        lambda slug, client=None: calls.append(("upload", slug, client)) or {"updated": 4},
    )

    result = module.synchronize_complementary_products("kentie", client=client)

    assert calls == [
        ("import", "kentie", client),
        ("generate", "kentie"),
        ("upload", "kentie", client),
    ]
    assert result["uploaded"]["updated"] == 4


def test_graphql_retries_temporary_shopify_throttling(monkeypatch):
    calls = []
    sleeps = []

    class Client:
        def graphql(self, query, variables):
            calls.append((query, variables))
            if len(calls) < 3:
                raise RuntimeError(
                    "[{'message': 'Throttled', "
                    "'extensions': {'code': 'THROTTLED'}}]"
                )
            return {"products": {"nodes": []}}

    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    result = module._graphql_with_throttle_retry(Client(), "query", {"after": None})

    assert result == {"products": {"nodes": []}}
    assert len(calls) == 3
    assert sleeps == [1.5, 3.0]


def test_graphql_does_not_retry_non_throttle_errors(monkeypatch):
    class Client:
        def graphql(self, query, variables):
            raise RuntimeError("Invalid query")

    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: (_ for _ in ()).throw(
            AssertionError("sleep should not be called")
        ),
    )

    try:
        module._graphql_with_throttle_retry(Client(), "query")
    except RuntimeError as exc:
        assert str(exc) == "Invalid query"
    else:
        raise AssertionError("RuntimeError was not raised")


def test_shopify_import_removes_stale_imported_relations(tmp_path, monkeypatch):
    path = _database(tmp_path, count=3)
    monkeypatch.setattr(module, "init_supplier_database", lambda slug: path)
    module.init_complementary_products("demo")
    with sqlite3.connect(path) as connection:
        connection.executemany(
            """INSERT INTO complementary_product_links(
                   source_sku,target_sku,position,reason,confidence,source,locked,
                   target_shopify_product_id,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                ("SKU-00", "SKU-01", 1, "old", 1, "shopify_existing", 1,
                 "gid://shopify/Product/deleted", "now", "now"),
                ("SKU-00", "SKU-02", 2, "manual", 1, "manual", 1,
                 None, "now", "now"),
            ],
        )
    monkeypatch.setattr(module, "_shopify_snapshot", lambda slug, client: [{
        "id": "gid://shopify/Product/source",
        "variants": {"nodes": [{"sku": "SKU-00"}]},
        "complementary": None,
    }])

    module.import_shopify_complementary_products("demo", client=object())

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT target_sku,source FROM complementary_product_links ORDER BY target_sku"
        ).fetchall()
    assert rows == [("SKU-02", "manual")]


def test_upload_prefers_current_shopify_id_for_target_sku(tmp_path, monkeypatch):
    path = _database(tmp_path, count=2)
    monkeypatch.setattr(module, "init_supplier_database", lambda slug: path)
    module.init_complementary_products("demo")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO complementary_product_links(
                   source_sku,target_sku,position,reason,confidence,source,locked,
                   target_shopify_product_id,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("SKU-00", "SKU-01", 1, "old", 1, "shopify_existing", 1,
             "gid://shopify/Product/deleted", "now", "now"),
        )
    monkeypatch.setattr(module, "_shopify_snapshot", lambda slug, client: [
        {"id": "gid://shopify/Product/source",
         "variants": {"nodes": [{"sku": "SKU-00"}]}},
        {"id": "gid://shopify/Product/current",
         "variants": {"nodes": [{"sku": "SKU-01"}]}},
    ])
    payloads = []

    class Client:
        def graphql(self, query, variables):
            payloads.extend(variables["metafields"])
            return {"metafieldsSet": {"metafields": [{"id": "1"}], "userErrors": []}}

    module.sync_complementary_products_to_shopify("demo", client=Client())

    source_payload = next(
        item for item in payloads
        if item["ownerId"] == "gid://shopify/Product/source"
    )
    assert json.loads(source_payload["value"]) == ["gid://shopify/Product/current"]
