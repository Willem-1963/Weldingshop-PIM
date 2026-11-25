from __future__ import annotations

import pytest

from app.workplace_photo_model import generate_photo_model, validate_photo_model


def _valid_model() -> dict:
    return {
        "profile": "test-photo",
        "confidence": 0.92,
        "parts": [
            {
                "type": "box", "color": "#112233",
                "x": 0, "y": 0.1 + index * 0.05, "z": 0,
                "w": 0.4, "h": 0.05, "d": 0.3,
                "rx": 0, "ry": 0, "rz": 0,
            }
            for index in range(8)
        ],
    }


def test_validate_photo_model_accepts_normalized_geometry() -> None:
    result = validate_photo_model(_valid_model())
    assert result["schemaVersion"] == 1
    assert result["confidence"] == 0.92
    assert len(result["parts"]) == 8


@pytest.mark.parametrize(
    ("source", "expected"),
    [(85, 0.85), ("85%", 0.85), ("85,5%", 0.855)],
)
def test_validate_photo_model_normalizes_percentage_confidence(
    source: object, expected: float
) -> None:
    value = _valid_model()
    value["confidence"] = source
    assert validate_photo_model(value)["confidence"] == expected


def test_validate_photo_model_rejects_confidence_over_100_percent() -> None:
    value = _valid_model()
    value["confidence"] = 101
    with pytest.raises(ValueError, match="valt buiten"):
        validate_photo_model(value)


def test_validate_photo_model_rejects_low_confidence() -> None:
    value = _valid_model()
    value["confidence"] = 0.4
    with pytest.raises(ValueError, match="onvoldoende betrouwbaar"):
        validate_photo_model(value)


def test_validate_photo_model_rejects_too_few_parts() -> None:
    value = _valid_model()
    value["parts"] = value["parts"][:7]
    with pytest.raises(ValueError, match="8 tot en met 120"):
        validate_photo_model(value)


def test_validate_photo_model_accepts_tiny_boundary_rounding() -> None:
    value = _valid_model()
    value["parts"][0]["y"] = 1.2500001
    assert validate_photo_model(value)["parts"][0]["y"] == 1.25


def test_generate_photo_model_retries_invalid_coordinate(monkeypatch) -> None:
    invalid = _valid_model()
    invalid["parts"][5]["y"] = 6
    valid = _valid_model()

    class FakeResponses:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            payload = invalid if len(self.calls) == 1 else valid
            return type("Response", (), {"output_text": __import__("json").dumps(payload)})()

    fake_responses = FakeResponses()

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = fake_responses

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("app.workplace_photo_model.OpenAI", FakeOpenAI)
    record = {
        "image": "https://example.com/product.jpg",
        "title": "Testmachine", "code": "TEST-1", "category": "Machines",
        "modelKind": "machine", "width": 50, "depth": 60, "height": 120,
    }

    result = generate_photo_model(record, reason="Model lijkt niet op de foto")

    assert len(fake_responses.calls) == 2
    assert result["parts"][5]["y"] < 1.25
    correction = fake_responses.calls[1]["input"][0]["content"][0]["text"]
    assert "CORRECTIE VEREIST" in correction
    assert "deel 6.y (6) valt buiten" in correction
