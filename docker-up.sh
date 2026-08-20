#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

command -v docker >/dev/null 2>&1 || {
  echo "错误：未找到 docker，请先在 Docker Desktop 中启用 WSL 2 集成。" >&2
  exit 1
}

compose=(docker compose)
if ! docker compose version >/dev/null 2>&1; then
  command -v docker-compose >/dev/null 2>&1 || {
    echo "错误：未找到 Docker Compose。" >&2
    exit 1
  }
  compose=(docker-compose)
fi

mkdir -p data
echo "正在构建并启动 SemiCrawler..."
"${compose[@]}" up -d --build
echo
echo "服务已启动："
echo "  前端: http://localhost:${SEMICRAWLER_PORT:-5173}"
echo "  API:  http://localhost:${SEMICRAWLER_PORT:-5173}/docs"
echo
echo "查看日志: ${compose[*]} logs -f"
