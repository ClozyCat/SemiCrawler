# 芯闻采集台

实施计划的四个阶段均已完成：React/Vite 工作台、FastAPI/SQLite 持久化、两站真实采集、OpenAI 兼容 LLM 结构化、证据与置信度审核、JSON 来源试抓取/版本化，以及默认和完整审计 CSV/XLSX 导出。

## 本地运行

```powershell
uv sync --group dev
npm install
uv run uvicorn app.main:app --reload
npm run dev
```

打开 `http://127.0.0.1:5173`。SQLite 数据默认写入 `data/semi_crawler.db`，可用 `SEMICRAWLER_DATA_DIR` 或 `SEMICRAWLER_DATABASE_URL` 调整。

在“模型与导出”页面填写兼容 API 地址、模型名和 API Key 后启用自动结构化。密钥仅由后端保存，读取接口只返回掩码。未启用模型时，文章会保留为待结构化原文，不会丢失。

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
