#!/usr/bin/env bash
set -Eeuo pipefail

config_file="${SEMICRAWLER_ENV_FILE:-/etc/semicrawler/semicrawler.env}"
if [[ -r "$config_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$config_file"
  set +a
fi

dokobot_executable="${SEMICRAWLER_DOKOBOT_EXECUTABLE:-dokobot}"
dokobot_home="${SEMICRAWLER_DOKOBOT_HOME:-$HOME}"

echo "[1/3] FastAPI health"
curl --fail --silent --show-error http://127.0.0.1:8070/api/health
echo

echo "[2/3] Dokobot bridge"
HOME="$dokobot_home" "$dokobot_executable" doko list

echo "[3/3] Browser read"
HOME="$dokobot_home" "$dokobot_executable" read --local https://dokobot.ai --timeout 90 >/dev/null
echo "OK: API and Dokobot local browser bridge are available."
