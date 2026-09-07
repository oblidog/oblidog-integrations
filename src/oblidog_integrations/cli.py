from __future__ import annotations

import argparse
from collections.abc import Callable

from oblidog_integrations.integrations.demo import run as run_demo
from oblidog_integrations.integrations.ekartoteka import run as run_ekartoteka
from oblidog_integrations.integrations.iprzedszkole import run as run_iprzedszkole
from oblidog_integrations.integrations.nju import run as run_nju
from oblidog_integrations.logging import configure_logging

IntegrationRunner = Callable[[], None]

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
    INTEGRATIONS[args.integration]()


if __name__ == "__main__":
    main()
