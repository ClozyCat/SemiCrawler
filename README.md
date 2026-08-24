## 本地运行

```powershell
uv sync --group dev
npm install
uv run uvicorn app.main:app --reload
npm run dev
```

打开 `http://127.0.0.1:5173`。SQLite 数据默认写入 `data/semi_crawler.db`，可用 `SEMICRAWLER_DATA_DIR` 或 `SEMICRAWLER_DATABASE_URL` 调整。

在“模型与导出”页面填写兼容 API 地址、模型名和 API Key 后启用自动结构化。密钥仅由后端保存，读取接口只返回掩码。未启用模型时，文章会保留为待结构化原文，不会丢失。

## 验证

```powershell
uv run pytest
npm run build
```

API 文档位于 `http://127.0.0.1:8000/docs`。

