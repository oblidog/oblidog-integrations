from __future__ import annotations

import pytest

from oblidog_integrations import cli


def test_cli_dispatches_the_selected_adapter_through_reporting(monkeypatch) -> None:
    captured: dict[str, object] = {}
    runner = cli.INTEGRATIONS["nju"]

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(
        cli,
        "run_with_reporting",
        lambda integration, selected: captured.update(
            integration=integration, runner=selected
        ),
    )
    monkeypatch.setattr("sys.argv", ["oblidog-integrations", "nju"])

    cli.main()

    assert captured == {"integration": "nju", "runner": runner}


def test_cli_converts_failures_to_nonzero_exit_without_raw_traceback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(
        cli,
        "run_with_reporting",
        lambda *_: (_ for _ in ()).throw(RuntimeError("sensitive failure")),
    )
    monkeypatch.setattr("sys.argv", ["oblidog-integrations", "nju"])

    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert raised.value.code == 1
    assert raised.value.__cause__ is None
