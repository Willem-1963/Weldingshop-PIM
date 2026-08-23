def test_incidental_supplier_filter_excludes_central_sync_supplier():
    suppliers = [
        {
            "id": 1, "name": "Vynckiers", "sync_slug": "vynckiers",
            "approved_domains": ["vynckier.biz"],
        },
        {
            "id": 2, "name": "Vynckiers (incidenteel)", "sync_slug": None,
            "approved_domains": ["vynckier.biz"],
        },
    ]
    domain = "vynckier.biz"

    match = next((
        item for item in suppliers
        if not item.get("sync_slug")
        if domain in set(item.get("approved_domains") or [])
    ), None)

    assert match["id"] == 2
