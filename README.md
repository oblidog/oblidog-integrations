# oblidog-integrations

Batch and one-shot integrations for Oblidog.

The repository is a monorepo for integrations that run periodically, fetch data from an external provider, synchronize it through `oblidog-client`, and exit. Long-running services such as mail ingestion should live separately.

## Structure

```text
src/oblidog_integrations/
├── cli.py
└── integrations/
    └── demo/
        ├── provider.py
        └── sync.py
```

Each integration owns its provider-specific code and exposes a parameterless `run()` function. The central CLI only registers and dispatches integrations.

## Development

Python 3.12 and `uv` are used for dependency management.

```bash
uv sync
uv run ruff check .
uv run pytest
```

The same common commands are available through `make`:

```bash
make sync
make check
make run-ekartoteka
make print-ekartoteka-schema
```

`make print-ekartoteka-schema` prints the JSON Schema for the flat e-Kartoteka
category-data record. Paste it into the category-schema import in Oblidog Ledger.

Before running e-Kartoteka for the first time, prepare its local configuration:

```bash
cp .env.ekartoteka.example .env.ekartoteka
```

Set `EKARTOTEKA_USERNAME`, `EKARTOTEKA_PASSWORD`, `OBLIDOG_URL`,
`OBLIDOG_API_KEY`, and `OBLIDOG_CATEGORY_CODE` in that file. Running
`make run-ekartoteka` prints and creates a category-data observation containing
the e-Kartoteka settlement snapshot.

Logs use a readable console format by default. Set `OBLIDOG_LOG_FORMAT=json`
to emit one JSON object per log event for systemd or a log collector.

Run the proof-of-concept integration with:

```bash
OBLIDOG_URL=https://oblidog.example.com \
OBLIDOG_API_KEY=... \
OBLIDOG_CATEGORY_CODE=DEMO \
uv run oblidog-integrations demo
```

Optional demo provider values:

```text
DEMO_AMOUNT=42.00
DEMO_INVOICE_NUMBER=DEMO-001
```

The demo integration expects exactly one matching obligation for the current month. It updates its amount, appends an import note, and marks it ready.

## Docker

A single, non-root production image contains all batch integrations. The
integration name is passed as the container argument. Builds use the locked
dependencies in `uv.lock`; `.env` files and credentials are excluded from the
build context.

```bash
docker build -t oblidog-integrations:local .
docker run --rm oblidog-integrations:local --help
```

The published image is `ghcr.io/oblidog/oblidog-integrations:vX.Y.Z`. Only
immutable release tags are published—there is no `latest`, `main`, or `edge`
tag. Do not deploy until the selected tag appears in [GitHub
Releases](https://github.com/oblidog/oblidog-integrations/releases) and the
release's image-publication workflow has completed.

## Host deployment

The recommended small-host deployment uses Docker Compose only for one-shot
containers and the host's cron daemon for scheduling. No integration container
needs to stay running between jobs.

The production Compose file defines three independent services:

- `ekartoteka`
- `nju-account-one`
- `nju-account-two`

Each NJU account gets its own credentials, Oblidog category, and state volume.
All services use the same immutable application image.

### Install deployment files

The host needs Docker Engine with the Compose plugin and `curl`. Choose an
existing release tag and download `install.sh` from that same tag:

```bash
export OBLIDOG_INTEGRATIONS_VERSION=vX.Y.Z
curl -fsSLO \
  "https://raw.githubusercontent.com/oblidog/oblidog-integrations/${OBLIDOG_INTEGRATIONS_VERSION}/install.sh"
sh install.sh "$OBLIDOG_INTEGRATIONS_VERSION" "$HOME/oblidog-integrations"
```

The installer downloads the matching `compose.yaml` and environment examples,
then creates the local files below if they do not already exist:

```text
~/oblidog-integrations/
├── compose.yaml
├── .env
├── .env.deploy.example
├── .env.ekartoteka
├── .env.ekartoteka.example
├── .env.nju.account-one
├── .env.nju.account-two
└── .env.nju.example
```

Existing `.env` and credential files are never overwritten, so the installer
can also be used to refresh deployment templates for a newer release.

Edit `.env.ekartoteka`, `.env.nju.account-one`, and `.env.nju.account-two` with
the real credentials and Oblidog category codes. Use a distinct
`NJU_ACCOUNT_NAME` and `OBLIDOG_CATEGORY_CODE` for each NJU account.

If GHCR requires authentication, log in before pulling:

```bash
docker login ghcr.io
```

Validate, pull, and manually test every job:

```bash
cd "$HOME/oblidog-integrations"
docker compose config --quiet
docker compose pull
docker compose run --rm ekartoteka
docker compose run --rm nju-account-one
docker compose run --rm nju-account-two
```

### Schedule with cron

Install the jobs in the crontab of the user that is allowed to run Docker:

```bash
mkdir -p "$HOME/.local/state/oblidog-integrations"
crontab -e
```

A suitable schedule for a small Raspberry Pi host is:

```cron
0 9 * * * cd "$HOME/oblidog-integrations" && /usr/bin/docker compose run --rm ekartoteka >> "$HOME/.local/state/oblidog-integrations/ekartoteka.log" 2>&1
10 9 * * * cd "$HOME/oblidog-integrations" && /usr/bin/docker compose run --rm nju-account-one >> "$HOME/.local/state/oblidog-integrations/nju-account-one.log" 2>&1
20 9 * * * cd "$HOME/oblidog-integrations" && /usr/bin/docker compose run --rm nju-account-two >> "$HOME/.local/state/oblidog-integrations/nju-account-two.log" 2>&1
```

The stagger keeps the integrations from competing for CPU and memory and leaves
time before Ledger's 09:30 daily system run. Adjust the paths and times to the
host as needed.

Each invocation creates a temporary container, runs the integration, writes its
result to stdout/stderr, and removes the container when it exits. The named
state volumes are retained. The `oblidog-scheduled-run` wrapper provides a
non-blocking `flock`, so a second invocation of the same service is skipped if
the previous run is still active.

Cron does not replay jobs missed while the host was powered off. For hosts that
need catch-up behavior after downtime, use a systemd timer instead.

### Upgrade and rollback

To deploy another release, download that release's installer and run it against
the existing target directory:

```bash
export OBLIDOG_INTEGRATIONS_VERSION=vX.Y.Z
curl -fsSLO \
  "https://raw.githubusercontent.com/oblidog/oblidog-integrations/${OBLIDOG_INTEGRATIONS_VERSION}/install.sh"
sh install.sh "$OBLIDOG_INTEGRATIONS_VERSION" "$HOME/oblidog-integrations"
```

The installer preserves credentials and an existing `.env`. Update
`OBLIDOG_INTEGRATIONS_VERSION` in `~/oblidog-integrations/.env` to the new
immutable release tag, then pull it:

```bash
cd "$HOME/oblidog-integrations"
docker compose config --quiet
docker compose pull
```

There are no persistent application containers to recreate. The next cron run
uses the new image. Rollback is the same operation with an earlier release tag.

### NJU Mobile accounts

The `nju` integration reads invoices from one NJU Mobile account and uses only
invoices whose portal period matches the current `MM.RRRR`. It sums their full
amounts, updates the one matching current-month Oblidog obligation, and marks
it `ready` when any invoice is unpaid or `paid` when all are settled. Repeated
runs with unchanged data are no-ops for obligations already in the target
state. If an amount, due date, or paid state changes after `ready`/`paid`, the
integration deliberately reopens the obligation, updates it, then applies the
required `ready` and optional `paid` transitions.

Run every account in a separate one-shot Compose service with a separate
credential file and Oblidog category. The default deployment contains two NJU
account services. Create additional services and state volumes if more accounts
are needed.

## Releases

Releases use Conventional Commits and a reviewable release PR:

- `fix:` and `perf:` prepare a patch release;
- `feat:` prepares a minor release;
- `BREAKING CHANGE:` in the footer or `!` after the type prepares a major release.

Documentation, test, CI, and ordinary chore commits do not create a release.
On a qualifying push to `main`, Commitizen updates `pyproject.toml`, `uv.lock`,
and `CHANGELOG.md` on a `release/vX.Y.Z` branch and creates (or reuses) a
`bump: version X.Y.Z` PR. Merge that PR after CI approval. The finalizer then
creates the annotated tag and GitHub Release and explicitly dispatches the
multi-platform GHCR image publication.

The first release is intentional and manual: run **Bootstrap initial release**
from the Actions tab on `main` with `release_tag=v0.1.0`. It validates the
project version, creates the annotated tag and GitHub Release, then publishes
the initial image. Subsequent releases use the release-PR flow above.

Generate a local changelog or inspect the next version with:

```bash
uv run cz version -p
uv run cz bump --dry-run --yes --get-next
```

For a direct local invocation with credentials, use:

```bash
docker run --rm \
  --env-file demo.env \
  oblidog-integrations:local demo
```
