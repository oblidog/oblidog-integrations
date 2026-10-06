from contextlib import nullcontext
from io import BytesIO
from ssl import SSLCertVerificationError
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from oblidog_integrations import retry
from oblidog_integrations.integrations.ekartoteka import api as ek_api
from oblidog_integrations.integrations.nju.api import NjuClient, NjuError


@pytest.fixture
def waits(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr(retry.time, "sleep", sleep)
    monkeypatch.setattr(retry.random, "uniform", lambda *_: 0.5)
    return sleep


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ConnectionResetError(),
        URLError(TimeoutError()),
        HTTPError("https://provider", 503, "unavailable", {}, None),
    ],
)
def test_transient_failure_recovers_with_bounded_backoff(error, waits):
    operation = Mock(side_effect=[error, error, "ok"])
    assert (
        retry.retry_provider(operation, integration="test", operation_name="fetch")
        == "ok"
    )
    assert operation.call_count == 3
    assert [c.args[0] for c in waits.call_args_list] == [2.5, 4.5]


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("https://provider", code, "error", {}, None)
        for code in [400, 401, 403, 404, 429]
    ]
    + [
        ValueError("invalid data"),
        URLError(SSLCertVerificationError()),
        NjuError("credentials rejected"),
    ],
)
def test_permanent_failures_are_not_retried(error, waits):
    operation = Mock(side_effect=error)
    with pytest.raises(type(error)):
        retry.retry_provider(operation, integration="test", operation_name="fetch")
    assert operation.call_count == 1
    waits.assert_not_called()


def test_exhaustion_preserves_original_exception(waits):
    error = TimeoutError("timed out")
    operation = Mock(side_effect=error)
    with pytest.raises(TimeoutError) as caught:
        retry.retry_provider(operation, integration="test", operation_name="fetch")
    assert caught.value is error
    assert operation.call_count == 3
    assert waits.call_count == 2


def test_ekartoteka_retries_wrapped_http_failure(monkeypatch, waits):
    request = Mock(
        side_effect=[
            HTTPError("https://provider", 502, "bad gateway", {}, None),
            nullcontext(BytesIO(b'{"ok": true}')),
        ]
    )
    monkeypatch.setattr(ek_api, "urlopen", request)
    assert ek_api.EkartotekaApi({})._request_json("https://provider") == {"ok": True}
    assert request.call_count == 2
    assert waits.call_count == 1


def test_ekartoteka_does_not_retry_invalid_json(monkeypatch, waits):
    request = Mock(return_value=nullcontext(BytesIO(b"not-json")))
    monkeypatch.setattr(ek_api, "urlopen", request)
    with pytest.raises(ek_api.EkartotekaError, match="invalid JSON"):
        ek_api.EkartotekaApi({})._request_json("https://provider")
    assert request.call_count == 1
    waits.assert_not_called()


def test_nju_missing_token_restarts_cookie_session(waits):
    first = Mock()
    first.open.return_value = nullcontext(BytesIO(b"<html>maintenance</html>"))
    second = Mock()
    second.open.side_effect = [
        nullcontext(BytesIO(b'<input name="_dynSessConf" value="fresh">')),
        nullcontext(BytesIO(b"<html>authenticated empty invoice list</html>")),
    ]
    factory = Mock(side_effect=[first, second])
    assert (
        NjuClient(
            phone="secret", password="secret", opener_factory=factory
        ).fetch_invoices()
        == []
    )
    assert factory.call_count == 2
    assert first.open.call_count == 1
    assert second.open.call_count == 2
    assert (
        factory.call_args_list[0].args[0].cookiejar
        is not factory.call_args_list[1].args[0].cookiejar
    )


def test_nju_rejected_credentials_are_not_retried(waits):
    opener = Mock()
    opener.open.side_effect = [
        nullcontext(BytesIO(b'<input name="_dynSessConf" value="token">')),
        nullcontext(BytesIO(b'<input name="password-form">')),
    ]
    factory = Mock(return_value=opener)
    with pytest.raises(NjuError, match="rejected"):
        NjuClient(
            phone="secret", password="secret", opener_factory=factory
        ).fetch_invoices()
    assert factory.call_count == 1
    waits.assert_not_called()


def test_retry_log_has_attempt_delay_and_no_error_payload(monkeypatch, waits):
    logger = Mock()
    monkeypatch.setattr(retry, "logger", logger)
    operation = Mock(side_effect=[TimeoutError("password=secret"), "ok"])
    retry.retry_provider(operation, integration="nju", operation_name="login_session")
    logger.warning.assert_called_once_with(
        "request_retry",
        integration="nju",
        operation="login_session",
        attempt=2,
        max_attempts=3,
        reason="TimeoutError",
        delay_seconds=2.5,
    )


def test_nju_network_failure_restarts_session_and_exhausts_budget(waits):
    opener = Mock()
    opener.open.side_effect = URLError(TimeoutError("timeout"))
    factory = Mock(return_value=opener)
    with pytest.raises(NjuError, match="request failed"):
        NjuClient(
            phone="secret", password="secret", opener_factory=factory
        ).fetch_invoices()
    assert factory.call_count == 3
    assert opener.open.call_count == 3
    assert waits.call_count == 2
