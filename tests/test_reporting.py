from __future__ import annotations

import pytest
import structlog.contextvars

from oblidog_integrations.reporting import RunResult, run_with_reporting


@pytest.mark.parametrize("changes", [True, False, None])
def test_returns_the_adapter_result(changes: bool | None) -> None:
    expected = RunResult(changes_detected=changes)

    assert run_with_reporting("nju", lambda: expected) is expected


def test_propagates_adapter_errors() -> None:
    original = ValueError("provider failed")

    with pytest.raises(ValueError) as raised:
        run_with_reporting("nju", lambda: (_ for _ in ()).throw(original))

    assert raised.value is original


def test_rejects_an_invalid_adapter_result() -> None:
    with pytest.raises(TypeError, match="must return RunResult"):
        run_with_reporting("nju", lambda: None)  # type: ignore[arg-type]


def test_provider_logs_include_the_integration_name() -> None:
    def runner() -> RunResult:
        assert structlog.contextvars.get_contextvars() == {"integration": "nju"}
        return RunResult(changes_detected=False)

    run_with_reporting("nju", runner)
