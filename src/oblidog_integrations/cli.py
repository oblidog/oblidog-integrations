from __future__ import annotations

import argparse

import structlog

from oblidog_integrations.integrations.demo import run as run_demo
from oblidog_integrations.integrations.ekartoteka import run as run_ekartoteka
from oblidog_integrations.integrations.iprzedszkole import run as run_iprzedszkole
from oblidog_integrations.integrations.nju import run as run_nju
from oblidog_integrations.logging import configure_logging
from oblidog_integrations.reporting import IntegrationRunner, run_with_reporting

logger = structlog.get_logger(__name__)

INTEGRATIONS: dict[str, IntegrationRunner] = {
    "demo": run_demo,
    "ekartoteka": run_ekartoteka,
    "iprzedszkole": run_iprzedszkole,
    "nju": run_nju,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an Oblidog integration")
    parser.add_argument("integration", choices=sorted(INTEGRATIONS))
    return parser


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()
    try:
        run_with_reporting(args.integration, INTEGRATIONS[args.integration])
    except Exception as error:  # noqa: BLE001
        # The reporter already emits provider tracebacks inside the run context.
        # Exit explicitly so Python does not print a second, unredacted traceback.
        logger.error(
            "integration_command_failed",
            integration=args.integration,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
