from app.shopify import client as client_module
import pytest


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_graphql_retries_throttled_response(monkeypatch):
    responses = iter([
        {
            "errors": [{"extensions": {"code": "THROTTLED"}}],
            "extensions": {
                "cost": {
                    "requestedQueryCost": 20,
                    "throttleStatus": {"currentlyAvailable": 10, "restoreRate": 10},
                }
            },
        },
        {"data": {"shop": {"name": "Weldingshop"}}},
    ])
    sleeps = []
    monkeypatch.setattr(
        client_module.requests, "post", lambda *args, **kwargs: Response(next(responses))
    )
    monkeypatch.setattr(client_module.time, "sleep", sleeps.append)

    client = client_module.ShopifyClient("example.myshopify.com", "token")
    assert client.graphql("query { shop { name } }") == {
        "shop": {"name": "Weldingshop"}
    }
    assert sleeps == [1.25]


def test_graphql_does_not_retry_permanent_error(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return Response({"errors": [{"extensions": {"code": "MAX_COST_EXCEEDED"}}]})

    monkeypatch.setattr(client_module.requests, "post", post)
    client = client_module.ShopifyClient("example.myshopify.com", "token")

    try:
        client.graphql("query { shop { name } }")
    except RuntimeError as exc:
        assert "MAX_COST_EXCEEDED" in str(exc)
    else:
        raise AssertionError("RuntimeError expected")
    assert len(calls) == 1


def test_repeated_throttling_escalates_wait(monkeypatch):
    calls = []
    sleeps = []
    payload = {
        "errors": [{"extensions": {"code": "THROTTLED"}}],
        "extensions": {"cost": {
            "requestedQueryCost": 20,
            "throttleStatus": {"currentlyAvailable": 10, "restoreRate": 10},
        }},
    }

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return Response(payload if len(calls) <= 8 else {"data": {"ok": True}})

    monkeypatch.setattr(client_module.requests, "post", post)
    monkeypatch.setattr(client_module.time, "sleep", sleeps.append)
    client = client_module.ShopifyClient("example.myshopify.com", "token")
    assert client.graphql("query", {"id": "test"}) == {"ok": True}
    assert sleeps == [1.25, 2.5, 5, 10, 20, 30, 30, 30]
    assert all(call == calls[0] for call in calls)


def test_persistent_throttle_stops_after_limit(monkeypatch):
    calls = []
    sleeps = []

    def post(*args, **kwargs):
        calls.append(1)
        return Response({"errors": [{"extensions": {"code": "THROTTLED"}}]})

    monkeypatch.setattr(client_module.requests, "post", post)
    monkeypatch.setattr(client_module.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="THROTTLED"):
        client_module.ShopifyClient("example.myshopify.com", "token").graphql("query")
    assert len(calls) == client_module.THROTTLE_RETRIES
    assert len(sleeps) == client_module.THROTTLE_RETRIES - 1


def test_mixed_errors_are_not_retried(monkeypatch):
    def post(*args, **kwargs):
        return Response({"errors": [
            {"extensions": {"code": "THROTTLED"}},
            {"extensions": {"code": "ACCESS_DENIED"}},
        ]})

    monkeypatch.setattr(client_module.requests, "post", post)
    monkeypatch.setattr(client_module.time, "sleep", lambda _: pytest.fail("Unexpected retry"))
    with pytest.raises(RuntimeError, match="ACCESS_DENIED"):
        client_module.ShopifyClient("example.myshopify.com", "token").graphql("query")


def test_budget_wait_is_not_shortened():
    assert client_module._throttle_wait_seconds({"extensions": {"cost": {
        "requestedQueryCost": 100, "throttleStatus": {
            "currentlyAvailable": 0, "restoreRate": 1,
        },
    }}}, 0) == 100.25
