from app.suppliers import hub


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.payload


def supplier():
    return {
        "source_location": "https://example.test/feed",
        "source_type": "xml_url",
        "auth_type": "none",
        "request_options": {"timeout": 1},
    }


def test_xml_feed_retries_invalid_responses(monkeypatch):
    responses = iter([
        b"",
        b"<html>storing</html>",
        b"<result><product /></result>",
    ])
    calls = []

    def urlopen(*args, **kwargs):
        calls.append((args, kwargs))
        return Response(next(responses))

    monkeypatch.setattr(hub.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(hub.time, "sleep", lambda seconds: None)

    result = hub._request_bytes(supplier())

    assert result == b"<result><product /></result>"
    assert len(calls) == 3


def test_xml_feed_reports_error_after_three_attempts(monkeypatch):
    calls = []

    def urlopen(*args, **kwargs):
        calls.append((args, kwargs))
        return Response(b"Service unavailable")

    monkeypatch.setattr(hub.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(hub.time, "sleep", lambda seconds: None)

    try:
        hub._request_bytes(supplier())
    except ValueError as exc:
        assert "na 3 pogingen" in str(exc)
    else:
        raise AssertionError("Een ongeldige XML-feed had moeten mislukken")
    assert len(calls) == 3
