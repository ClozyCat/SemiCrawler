#!/usr/bin/env bash
set -Eeuo pipefail

config_file="${SEMICRAWLER_ENV_FILE:-/etc/semicrawler/semicrawler.env}"
if [[ -r "$config_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$config_file"
  set +a
fi


echo "[1/3] FastAPI health"
curl --fail --silent --show-error http://127.0.0.1:8070/api/health
echo

echo "[2/3] Search API configuration"
if [[ -z "${BAIDU_SEARCH_API_KEY:-${BAIDU_API_KEY:-}}" ]]; then
  echo "WARN: Baidu Search API key is not set; configure it in API settings."
else
  echo "Baidu Search key is configured."
fi
if [[ -z "${TAVILY_API_KEY:-}" ]]; then
  echo "WARN: TAVILY_API_KEY is not set; configure it in API settings."
else
  echo "Tavily key is configured."
fi
if [[ -z "${ANYSEARCH_API_KEY:-}" ]]; then
  echo "WARN: ANYSEARCH_API_KEY is not set; configure it in API settings."
else
  echo "Anysearch key is configured."
fi
echo "[3/3] Complete"
echo "OK: API and search configuration checks completed."
