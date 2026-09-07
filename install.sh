#!/bin/sh
set -eu

usage() {
    cat <<'EOF'
Usage: ./install.sh VERSION [TARGET_DIR]

Example:
  ./install.sh v0.3.0 /opt/oblidog-integrations

Downloads deployment files for an immutable release tag and creates local
configuration files without overwriting existing credentials.
EOF
}

version="${1:-}"
target_dir="${2:-./oblidog-integrations}"

if [ -z "$version" ]; then
    usage >&2
    exit 2
fi

case "$version" in
    v[0-9]*.[0-9]*.[0-9]*) ;;
    *)
        echo "VERSION must look like vX.Y.Z" >&2
        exit 2
        ;;
esac

if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required" >&2
    exit 1
fi

base_url="https://raw.githubusercontent.com/oblidog/oblidog-integrations/${version}"
mkdir -p "$target_dir"

fetch() {
    remote_path="$1"
    local_path="$2"
    echo "Downloading ${remote_path}"
    curl -fsSL "${base_url}/${remote_path}" -o "${target_dir}/${local_path}"
}

copy_if_missing() {
    source_path="$1"
    destination_path="$2"

    if [ -e "${target_dir}/${destination_path}" ]; then
        echo "Keeping existing ${destination_path}"
        return
    fi

    cp "${target_dir}/${source_path}" "${target_dir}/${destination_path}"
    echo "Created ${destination_path}"
}

fetch compose.yaml compose.yaml
fetch .env.deploy.example .env.deploy.example
fetch .env.ekartoteka.example .env.ekartoteka.example
fetch .env.iprzedszkole.example .env.iprzedszkole.example
fetch .env.nju.example .env.nju.example

if [ ! -e "${target_dir}/.env" ]; then
    printf 'OBLIDOG_INTEGRATIONS_VERSION=%s\n' "$version" > "${target_dir}/.env"
    echo "Created .env"
else
    echo "Keeping existing .env"
fi

copy_if_missing .env.ekartoteka.example .env.ekartoteka
copy_if_missing .env.iprzedszkole.example .env.iprzedszkole
copy_if_missing .env.nju.example .env.nju.account-one
copy_if_missing .env.nju.example .env.nju.account-two
chmod 600 \
    "${target_dir}/.env" \
    "${target_dir}/.env.ekartoteka" \
    "${target_dir}/.env.iprzedszkole" \
    "${target_dir}/.env.nju.account-one" \
    "${target_dir}/.env.nju.account-two"

cat <<EOF

Deployment files installed in ${target_dir}

Next steps:
  1. Edit .env.ekartoteka, .env.iprzedszkole, .env.nju.account-one and .env.nju.account-two.
  2. Run: cd ${target_dir} && docker compose config --quiet
  3. Run: cd ${target_dir} && docker compose pull
  4. Test each integration with docker compose run --rm <service>.
  5. Add the cron entries from README.md on the host.
EOF
