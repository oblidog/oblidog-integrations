from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Self

import httpx
import pytest
import structlog.contextvars
from oblidog_client import (
    IntegrationConflictCode,
    IntegrationResult,
    OblidogConflictError,
)

from oblidog_integrations import reporting
from oblidog_integrations.reporting import RunResult


class FakeIntegrations:
    def __init__(self) -> None:
        self.instance = SimpleNamespace(enabled=True, revision=7, provider="nju")
        self.get_calls: list[str] = []
        self.start_calls: list[dict[str, object]] = []
        self.finish_calls: list[dict[str, object]] = []
        self.get_errors: list[Exception] = []
        self.start_errors: list[Exception] = []
        self.finish_errors: list[Exception] = []

    def get(self, key: str) -> object:
        self.get_calls.append(key)
        if self.get_errors:
            raise self.get_errors.pop(0)
        return self.instance

    def start(self, key: str, **kwargs: object) -> object:
        self.start_calls.append({"key": key, **kwargs})
        if self.start_errors:
            raise self.start_errors.pop(0)
        return self.instance

    def finish(self, key: str, **kwargs: object) -> object:
        self.finish_calls.append({"key": key, **kwargs})
        if self.finish_errors:
            raise self.finish_errors.pop(0)
        return self.instance


class FakeClient:
    def __init__(self, integrations: FakeIntegrations) -> None:
        self.integrations = integrations

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture
def configured_reporting(monkeypatch):
    integrations = FakeIntegrations()
    client = FakeClient(integrations)
    created_with: list[dict[str, str]] = []

    def client_factory(**kwargs: str) -> FakeClient:
        created_with.append(kwargs)
        return client

    monkeypatch.setattr(reporting, "OblidogClient", client_factory)
    monkeypatch.setattr(reporting, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(reporting.time, "sleep", lambda _delay: None)
    monkeypatch.setenv("OBLIDOG_URL", "https://oblidog.example.com")
    monkeypatch.setenv("OBLIDOG_API_KEY", "ledger-api-key")
    monkeypatch.setenv("OBLIDOG_INTEGRATION_KEY", "nju-account-one")
    return integrations, created_with


def transport_error() -> httpx.ConnectError:
    return httpx.ConnectError(
        "connection lost", request=httpx.Request("POST", "https://example.test")
    )


def test_missing_key_preserves_the_unmonitored_path(monkeypatch) -> None:
    monkeypatch.delenv("OBLIDOG_INTEGRATION_KEY", raising=False)
    monkeypatch.setattr(
        reporting,
        "OblidogClient",
        lambda **_: pytest.fail("reporting client must not be created"),
    )
    expected = RunResult(changes_detected=False)

    assert reporting.run_with_reporting("nju", lambda: expected) is expected


def test_disabled_instance_skips_provider_work(configured_reporting) -> None:
    integrations, _ = configured_reporting
    integrations.instance.enabled = False
    called = False

    def runner() -> RunResult:
        nonlocal called
        called = True
        return RunResult(changes_detected=False)

    assert reporting.run_with_reporting("nju", runner) is None
    assert not called
    assert integrations.start_calls == []
    assert integrations.finish_calls == []


@pytest.mark.parametrize("changes", [True, False, None])
def test_success_reports_one_run_with_nullable_changes(
    configured_reporting, changes: bool | None
) -> None:
    integrations, created_with = configured_reporting

    result = reporting.run_with_reporting(
        "nju", lambda: RunResult(changes_detected=changes)
    )

    assert result == RunResult(changes_detected=changes)
    assert created_with == [
        {
            "base_url": "https://oblidog.example.com",
            "api_key": "ledger-api-key",
        }
    ]
    assert integrations.get_calls == ["nju-account-one"]
    assert integrations.start_calls[0]["expected_revision"] == 7
    run_id = integrations.start_calls[0]["run_id"]
    assert isinstance(run_id, uuid.UUID)
    assert integrations.finish_calls == [
        {
            "key": "nju-account-one",
            "run_id": run_id,
            "result": IntegrationResult.SUCCESS,
            "changes_detected": changes,
            "error": None,
        }
    ]


def test_start_retry_reuses_run_id_and_revision(configured_reporting) -> None:
    integrations, _ = configured_reporting
    integrations.start_errors = [transport_error()]

    reporting.run_with_reporting("nju", lambda: RunResult(changes_detected=False))

    assert len(integrations.start_calls) == 2
    assert integrations.start_calls[0] == integrations.start_calls[1]


def test_unconfirmed_start_stops_after_bounded_retries(
    configured_reporting,
) -> None:
    integrations, _ = configured_reporting
    integrations.start_errors = [
        transport_error(),
        transport_error(),
        transport_error(),
    ]

    with pytest.raises(httpx.ConnectError):
        reporting.run_with_reporting(
            "nju", lambda: pytest.fail("provider must not be called")
        )

    assert len(integrations.start_calls) == 3
    assert integrations.start_calls[0] == integrations.start_calls[1]
    assert integrations.start_calls[1] == integrations.start_calls[2]
    assert integrations.finish_calls == []


def test_non_retryable_start_conflict_stops_before_provider(
    configured_reporting,
) -> None:
    integrations, _ = configured_reporting
    integrations.start_errors = [
        OblidogConflictError(IntegrationConflictCode.REVISION_CONFLICT)
    ]
    called = False

    def runner() -> RunResult:
        nonlocal called
        called = True
        return RunResult(changes_detected=False)

    with pytest.raises(OblidogConflictError):
        reporting.run_with_reporting("nju", runner)

    assert not called
    assert len(integrations.start_calls) == 1
    assert integrations.finish_calls == []


def test_provider_failure_is_reported_and_original_error_is_preserved(
    configured_reporting,
) -> None:
    integrations, _ = configured_reporting
    original = ValueError("provider response included ledger-api-key")

    with pytest.raises(ValueError) as raised:
        reporting.run_with_reporting("nju", lambda: (_ for _ in ()).throw(original))

    assert raised.value is original
    failure = integrations.finish_calls[0]
    assert failure["result"] is IntegrationResult.FAILURE
    assert failure["changes_detected"] is None
    assert failure["run_id"] == integrations.start_calls[0]["run_id"]
    error = failure["error"]
    assert error.code == "runner_failed"
    assert "ledger-api-key" not in error.message


def test_failure_reporting_error_does_not_replace_provider_error(
    configured_reporting,
) -> None:
    integrations, _ = configured_reporting
    integrations.finish_errors = [
        OblidogConflictError(IntegrationConflictCode.RUN_CONFLICT)
    ]
    original = RuntimeError("provider failed")

    with pytest.raises(RuntimeError) as raised:
        reporting.run_with_reporting("nju", lambda: (_ for _ in ()).throw(original))

    assert raised.value is original
    assert len(integrations.finish_calls) == 1


def test_success_reporting_error_is_not_converted_to_failure(
    configured_reporting,
) -> None:
    integrations, _ = configured_reporting
    integrations.finish_errors = [
        OblidogConflictError(IntegrationConflictCode.RUN_CONFLICT)
    ]

    with pytest.raises(OblidogConflictError):
        reporting.run_with_reporting("nju", lambda: RunResult(changes_detected=True))

    assert len(integrations.finish_calls) == 1
    assert integrations.finish_calls[0]["result"] is IntegrationResult.SUCCESS


def test_provider_mismatch_stops_before_provider(configured_reporting) -> None:
    integrations, _ = configured_reporting
    integrations.instance.provider = "ekartoteka"

    with pytest.raises(RuntimeError, match="provider mismatch"):
        reporting.run_with_reporting(
            "nju", lambda: pytest.fail("provider must not be called")
        )

    assert integrations.start_calls == []


def test_each_invocation_gets_a_fresh_run_id(configured_reporting) -> None:
    integrations, _ = configured_reporting

    for _ in range(2):
        reporting.run_with_reporting("nju", lambda: RunResult(changes_detected=False))

    assert (
        integrations.start_calls[0]["run_id"] != integrations.start_calls[1]["run_id"]
    )


def test_provider_logs_share_the_reported_run_context(configured_reporting) -> None:
    integrations, _ = configured_reporting

    def runner() -> RunResult:
        assert structlog.contextvars.get_contextvars() == {
            "integration": "nju",
            "integration_key": "nju-account-one",
            "run_id": str(integrations.start_calls[0]["run_id"]),
        }
        return RunResult(changes_detected=False)

    reporting.run_with_reporting("nju", runner)

    assert structlog.contextvars.get_contextvars() == {}


def test_two_instances_share_one_api_key_but_report_independently(
    configured_reporting, monkeypatch
) -> None:
    integrations, created_with = configured_reporting

    reporting.run_with_reporting("nju", lambda: RunResult(changes_detected=False))
    monkeypatch.setenv("OBLIDOG_INTEGRATION_KEY", "nju-account-two")
    reporting.run_with_reporting("nju", lambda: RunResult(changes_detected=True))

    assert integrations.get_calls == ["nju-account-one", "nju-account-two"]
    assert [call["key"] for call in integrations.start_calls] == [
        "nju-account-one",
        "nju-account-two",
    ]
    assert (
        integrations.start_calls[0]["run_id"] != integrations.start_calls[1]["run_id"]
    )
    assert {call["api_key"] for call in created_with} == {"ledger-api-key"}
