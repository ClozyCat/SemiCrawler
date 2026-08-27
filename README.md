# 芯闻采集台

实施计划的四个阶段均已完成：React/Vite 工作台、FastAPI/SQLite 持久化、两站真实采集、Dokobot 本地联网检索、OpenAI 兼容 LLM 结构化、证据与置信度审核、JSON 来源试抓取/版本化，以及默认和完整审计 CSV/XLSX 导出。

## 本地运行

```powershell
uv sync --group dev
npm install
uv run uvicorn app.main:app --reload
npm run dev
```

打开 `http://127.0.0.1:5173`。SQLite 数据默认写入 `data/semi_crawler.db`，可用 `SEMICRAWLER_DATA_DIR` 或 `SEMICRAWLER_DATABASE_URL` 调整。

在“API配置”页面填写兼容 API 地址、模型名和 API Key 后启用自动结构化。密钥仅由后端保存，读取接口只返回掩码。未启用模型时，普通网站文章会保留为待结构化原文，不会丢失。

联网信息源使用 Dokobot 免费本地模式，通过真实浏览器读取搜索结果页和原始网页，不使用 Dokobot Remote，也不需要 Dokobot API Key。先安装 [Dokobot 浏览器扩展](https://chromewebstore.google.com/detail/dokobot/dlbiigchkpmpijahmlofleeemiomaneo)，然后完成一次本地 CLI 配置：

```powershell
npm i -g @dokobot/cli@latest
dokobot install-bridge
```

重启浏览器或重新加载扩展后，运行 `dokobot read --local https://dokobot.ai` 可验证本地桥接。添加信息源时选择“联网搜索”，填写自然语言检索主题及可选来源提示；任务会先用 `dokobot read --local` 搜索和读取原文，再用“API配置”中的 OpenAI 兼容模型生成结构化记录。来源提示中的每个 URL 会作为独立的 `site:` 搜索范围；每个规划关键词在每个来源范围内最多读取“查看数量上限”条结果，不设置跨关键词和跨来源的总数上限。任务执行全局串行，Dokobot 接管标签页上限为 5 个，并会复用和清理任务使用的标签页。

Dokobot 本地桥接需要后端进程与浏览器运行在同一台主机。默认 Docker 后端容器无法直接访问宿主机的本地桥接，因此联网搜索请使用上方的本地运行方式；普通网站采集和已保存数据仍可使用 Docker。

采集器在每次请求前遵守目标站点 `robots.txt`，使用可识别的 `SemiCrawler/1.0` User-Agent，并按来源配置限速。工具仅用于公开页面和本地事实审核；导出始终保留来源归属及原文链接，使用者仍需遵守站点条款和版权要求。

## 验证

```powershell
uv run pytest
npm run build
```

API 文档位于 `http://127.0.0.1:8000/docs`。

## Docker 部署（Windows WSL 2）

请先启动 Docker Desktop，并在 Settings -> Resources -> WSL Integration 中启用当前 WSL 发行版。在 WSL 终端进入项目目录后执行：

```bash
chmod +x docker-up.sh
./docker-up.sh
```

也可以直接运行 `docker compose up -d --build`。启动后访问 `http://localhost:5173`，API 文档为 `http://localhost:5173/docs`。SQLite 数据保存在项目下的 `data/semi_crawler.db`，停止服务使用 `docker compose down`，查看日志使用 `docker compose logs -f`。如需更换端口，可执行 `SEMICRAWLER_PORT=8080 ./docker-up.sh`。

Docker 构建默认通过腾讯云 PyPI 镜像安装 `uv` 和 Python 依赖，以便在中国大陆网络环境中使用。需要切换其他镜像或官方源时，可设置 `PYPI_INDEX_URL`：

```bash
PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple docker compose build backend
docker compose up -d
```

直接构建后端镜像时可使用 `--build-arg PYPI_INDEX_URL=<镜像地址>` 覆盖默认值。

## Debian 部署（Dokobot + Xvfb）

Debian 服务器可以通过 Xvfb 持续运行带 Dokobot 扩展的 Chrome，并让 FastAPI 以同一系统用户访问本地 Native Messaging Bridge。仓库提供了 systemd、Nginx、环境变量模板以及联合检查脚本；Nginx 默认监听 `8071`，不会占用 80 端口。

完整的逐步部署、首次 VNC 安装扩展、验证、更新、备份和故障排查说明见 [Debian + Xvfb + Dokobot 手动部署](docs/debian-dokobot-deployment.md)。
