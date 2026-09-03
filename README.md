# 芯闻采集台

实施计划的四个阶段均已完成：React/Vite 工作台、FastAPI/SQLite 持久化、两站真实采集、Tavily 联网检索与索引审阅、OpenAI 兼容 LLM 结构化、证据与置信度审核、JSON 来源试抓取/版本化，以及默认和完整审计 CSV/XLSX 导出。

## 本地运行

```powershell
uv sync --group dev
npm install
uv run uvicorn app.main:app --reload
npm run dev
```

打开 `http://127.0.0.1:5173`。SQLite 数据默认写入 `data/semi_crawler.db`，可用 `SEMICRAWLER_DATA_DIR` 或 `SEMICRAWLER_DATABASE_URL` 调整。

在“API配置”页面填写兼容 API 地址、模型名和 API Key 后启用自动结构化。密钥仅由后端保存，读取接口只返回掩码。未启用模型时，普通网站文章会保留为待结构化原文，不会丢失。

联网信息源使用 Tavily Search/Extract API。请在“API配置”中保存 Tavily API Key，或设置 `TAVILY_API_KEY` 环境变量。

添加信息源时选择“联网搜索”，填写自然语言检索主题及可选域名。系统先让 LLM 拆分查询词，再以 `site:` 和任务起始日期调用 Tavily，交给 LLM 审阅索引后使用 Tavily Extract（失败时回退普通 HTTP）读取正文，最后沿用关键词过滤和结构化流程。

Tavily API 可直接在 Docker 或本地后端使用；普通网站采集和已保存数据仍可使用 Docker。

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

## Debian 部署

Debian 服务器可直接运行 Tavily API 后端与普通网站采集服务。仓库提供了 systemd、Nginx、环境变量模板以及联合检查脚本；Nginx 默认监听 `8071`，不会占用 80 端口。

完整的部署、验证、更新和备份说明见 `deploy/debian/` 下的部署文件。
