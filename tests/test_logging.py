from oblidog_integrations.logging import _redact_secrets


def test_redacts_configured_secrets_recursively(monkeypatch) -> None:
    monkeypatch.setenv("OBLIDOG_API_KEY", "sensitive-api-key")
    monkeypatch.setenv("NJU_PASSWORD", "sensitive-password")
    monkeypatch.setenv("NJU_PHONE", "48123456789")

    result = _redact_secrets(
        None,
        "error",
        {
            "event": "failed with sensitive-api-key",
            "exception": "request password=sensitive-password phone=48123456789",
            "nested": {"values": ["sensitive-api-key"]},
        },
    )

    assert result == {
        "event": "failed with [REDACTED]",
        "exception": "request password=[REDACTED] phone=[REDACTED]",
        "nested": {"values": ["[REDACTED]"]},
    }
