import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("exit_code", [0, 7])
def test_shell_scheduler_uses_shared_json_logs_and_preserves_exit(tmp_path, exit_code):
    child = tmp_path / "oblidog-integrations"
    child.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    child.chmod(0o755)
    environment = os.environ | {
        "HOME": str(tmp_path),
        "PATH": f"{tmp_path}:{Path('.venv/bin').resolve()}:{os.environ['PATH']}",
        "OBLIDOG_LOG_FORMAT": "json",
    }
    result = subprocess.run(
        ["sh", "scripts/oblidog-scheduled-run", "nju"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == exit_code
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [event["event"] for event in events] == [
        "scheduled_run_started",
        "scheduled_run_finished",
    ]
    assert all(event["timestamp"].endswith("Z") for event in events)
    assert all(event["integration"] == "nju" for event in events)
    finished = events[1]
    assert finished["status"] == ("success" if exit_code == 0 else "failure")
    assert finished["level"] == ("info" if exit_code == 0 else "error")
    assert finished["exit_code"] == exit_code
    assert isinstance(finished["duration_seconds"], int)


@pytest.mark.parametrize("tracebacks", ["0", "1"])
def test_reporter_controls_traceback_and_preserves_exception(monkeypatch, tracebacks):
    from unittest.mock import Mock

    from oblidog_integrations import reporting

    monkeypatch.setenv("OBLIDOG_LOG_TRACEBACKS", tracebacks)
    logger = Mock()
    monkeypatch.setattr(reporting, "logger", logger)
    original = TimeoutError("provider timed out")
    with pytest.raises(TimeoutError) as raised:
        reporting.run_with_reporting("nju", lambda: (_ for _ in ()).throw(original))
    assert raised.value is original
    logger.error.assert_called_once_with(
        "integration_run_failed",
        error_type="TimeoutError",
        error="provider timed out",
        exc_info=tracebacks == "1",
    )


@pytest.mark.parametrize(
    "cause",
    [
        TimeoutError("timeout"),
        ConnectionResetError("reset"),
    ],
)
def test_wrapped_network_errors_have_no_traceback(monkeypatch, cause):
    from unittest.mock import Mock

    from oblidog_integrations import reporting

    monkeypatch.delenv("OBLIDOG_LOG_TRACEBACKS", raising=False)
    logger = Mock()
    monkeypatch.setattr(reporting, "logger", logger)
    error = RuntimeError("provider request failed")
    error.__cause__ = cause
    with pytest.raises(RuntimeError):
        reporting.run_with_reporting("nju", lambda: (_ for _ in ()).throw(error))
    assert logger.error.call_args.kwargs["exc_info"] is False


def test_invalid_payload_keeps_traceback_by_default(monkeypatch):
    import json
    from unittest.mock import Mock

    from oblidog_integrations import reporting

    monkeypatch.delenv("OBLIDOG_LOG_TRACEBACKS", raising=False)
    logger = Mock()
    monkeypatch.setattr(reporting, "logger", logger)
    error = RuntimeError("invalid provider JSON")
    error.__cause__ = json.JSONDecodeError("invalid", "bad", 0)
    with pytest.raises(RuntimeError):
        reporting.run_with_reporting("nju", lambda: (_ for _ in ()).throw(error))
    assert logger.error.call_args.kwargs["exc_info"] is True
