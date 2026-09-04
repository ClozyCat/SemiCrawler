#!/usr/bin/env bash
set -Eeuo pipefail

config_file="${SEMICRAWLER_ENV_FILE:-/etc/semicrawler/semicrawler.env}"
if [[ -r "$config_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$config_file"
  set +a
fi


echo "[1/4] FastAPI health"
curl --fail --silent --show-error http://127.0.0.1:8070/api/health
echo

echo "[2/4] Crawl4AI health"
if [[ "${CRAWL4AI_ENABLED:-false}" == "true" ]]; then
  if [[ -z "${CRAWL4AI_API_TOKEN:-}" ]]; then
    echo "ERROR: CRAWL4AI_API_TOKEN is required when Crawl4AI is enabled."
    exit 1
  fi
  curl --fail --silent --show-error \
    -H "Authorization: Bearer ${CRAWL4AI_API_TOKEN}" \
    "${CRAWL4AI_BASE_URL:-http://127.0.0.1:11235}/health"
  echo
else
  echo "WARN: Crawl4AI is disabled; browser-rendered extraction is unavailable."
fi

echo "[3/4] Search API configuration"
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
echo "[4/4] Complete"
echo "OK: API, Crawl4AI, and search configuration checks completed."
