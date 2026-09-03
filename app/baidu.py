from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, Field, HttpUrl, ValidationError


class BaiduError(ValueError):
    """Raised when Baidu Search cannot return a usable response."""


class BaiduSearchItem(BaseModel):
    title: str = Field(default="网页结果", max_length=500)
    url: HttpUrl
    content: str = ""
    published_date: str | None = None
    score: float | None = None


def _truncate_query(value: str, limit: int = 72) -> str:
    """Baidu counts ASCII as one unit and Chinese characters as two units."""
    result: list[str] = []
    used = 0
    for character in value.strip():
        size = 1 if ord(character) < 128 else 2
        if used + size > limit:
            break
        result.append(character)
        used += size
    return "".join(result)


class BaiduClient:
    """REST client for Qianfan's Baidu AI Search endpoint."""

    def __init__(self, api_key: str | None = None, *, timeout: float = 45):
        self.api_key = (
            api_key
            or os.getenv("BAIDU_SEARCH_API_KEY", "")
            or os.getenv("BAIDU_API_KEY", "")
        ).strip()
        self.base_url = os.getenv(
            "BAIDU_SEARCH_BASE_URL", "https://qianfan.baidubce.com"
        ).rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise BaiduError(
                "未配置百度搜索 API Key，请在 API配置 中保存密钥或设置 "
                "BAIDU_SEARCH_API_KEY 环境变量"
            )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.base_url}/v2/ai_search/web_search",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise BaiduError("百度搜索请求超时") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            raise BaiduError(
                f"百度搜索请求失败（HTTP {exc.response.status_code}）：{detail}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise BaiduError("无法连接百度搜索 API") from exc
        if not isinstance(data, dict):
            raise BaiduError("百度搜索返回格式无效")
        if data.get("code"):
            raise BaiduError(
                f"百度搜索请求失败（{data['code']}）：{data.get('message') or '未知错误'}"
            )
        return data

    def search(
        self,
        query: str,
        *,
        num: int = 10,
        topic: str = "general",
        start_date: date | None = None,
        end_date: date | None = None,
        domains: list[str] | None = None,
    ) -> list[BaiduSearchItem]:
        del topic  # Kept for the shared search-client interface.
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": _truncate_query(query)}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [
                {"type": "web", "top_k": min(max(num, 1), 20)}
            ],
        }
        search_filter: dict[str, Any] = {}
        if domains:
            search_filter["match"] = {
                "site": list(dict.fromkeys(domain.lower() for domain in domains))[:100]
            }
        if start_date and end_date:
            search_filter["range"] = {
                "page_time": {
                    "gte": start_date.isoformat(),
                    "lte": end_date.isoformat(),
                }
            }
        if search_filter:
            payload["search_filter"] = search_filter

        data = self._post(payload)
        references = data.get("references") or []
        if not isinstance(references, list):
            raise BaiduError("百度搜索返回的 references 格式无效")
        items: list[BaiduSearchItem] = []
        for raw in references:
            if (
                not isinstance(raw, dict)
                or raw.get("type", "web") != "web"
                or not raw.get("url")
            ):
                continue
            normalized = {
                "title": raw.get("title") or raw.get("web_anchor") or "网页结果",
                "url": raw["url"],
                "content": raw.get("content") or raw.get("snippet") or "",
                "published_date": raw.get("date"),
            }
            try:
                items.append(BaiduSearchItem.model_validate(normalized))
            except ValidationError:
                continue
        return items
