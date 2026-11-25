from datetime import datetime, timezone

import pytest

from app.shopify.sync import _missing_product_status_rows


NOW = datetime(2026, 10, 17, 23, tzinfo=timezone.utc)
POLICY = {"missing_products_to_draft": 1, "delete_missing_products": 1,
          "delete_missing_after_months": 2}


def missing(since, status="DRAFT"):
    return {"shopify_product_id": "gid://shopify/Product/1",
            "shopify_draft_since": since, "shopify_live_status": status}


@pytest.mark.parametrize("since", [
    "2026-08-17T21:50:40+00:00", "2026-08-17T21:50:40",
    "2026-08-17T23:00:00+00:00",
])
def test_expired_products_are_archived_without_intermediate_draft(since):
    drafts, archives, months = _missing_product_status_rows(
        POLICY, [missing(since)], NOW
    )
    assert drafts == []
    assert archives == [{"input": {"id": "gid://shopify/Product/1",
                                   "status": "ARCHIVED"}}]
    assert months == 2


@pytest.mark.parametrize("since", [
    None, "", "invalid", "2026-08-17T23:00:01+00:00",
])
def test_unexpired_or_unknown_dates_remain_drafts(since):
    drafts, archives, _ = _missing_product_status_rows(POLICY, [missing(since)], NOW)
    assert drafts[0]["input"]["status"] == "DRAFT"
    assert archives == []


def test_archived_products_are_never_returned_to_draft():
    drafts, archives, _ = _missing_product_status_rows(
        POLICY, [missing(None, "ARCHIVED")], NOW
    )
    assert drafts == archives == []


def test_disabled_archiving_keeps_expired_products_as_drafts():
    drafts, archives, _ = _missing_product_status_rows(
        {**POLICY, "delete_missing_products": 0},
        [missing("2026-08-01T00:00:00+00:00")], NOW
    )
    assert drafts[0]["input"]["status"] == "DRAFT"
    assert archives == []


def test_disabled_missing_policy_changes_no_statuses():
    drafts, archives, _ = _missing_product_status_rows(
        {**POLICY, "missing_products_to_draft": 0},
        [missing("2026-08-01T00:00:00+00:00")], NOW
    )
    assert drafts == archives == []
