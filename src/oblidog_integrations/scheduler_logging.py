"""Render shell scheduler events with the application's logging configuration."""

import argparse

import structlog

from oblidog_integrations.logging import configure_logging


def main() -> None:
    """Emit a scheduler event from key=value arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "event",
        choices=[
            "scheduled_run_started",
            "scheduled_run_finished",
            "scheduled_run_skipped",
        ],
    )
    parser.add_argument("fields", nargs="*")
    args = parser.parse_args()
    fields: dict[str, str | int] = {}
    for field in args.fields:
        key, separator, value = field.partition("=")
        if not separator or not key:
            parser.error("fields must use key=value")
        fields[key] = int(value) if key in {"exit_code", "duration_seconds"} else value
    configure_logging()
    logger = structlog.get_logger(__name__)
    if fields.get("status") in {"failure", "interrupted"}:
        logger.error(args.event, **fields)
    else:
        logger.info(args.event, **fields)
