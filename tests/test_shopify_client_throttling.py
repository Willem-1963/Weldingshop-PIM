from app.shopify import client as client_module


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
