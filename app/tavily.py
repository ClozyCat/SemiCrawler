from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, Field, HttpUrl, ValidationError


class TavilyError(ValueError):
    """Raised when Tavily cannot return a usable response."""


class TavilySearchItem(BaseModel):
    title: str = Field(default="网页结果", max_length=500)
    url: HttpUrl
    content: str = ""
    published_date: str | None = None
    score: float | None = None


class TavilyPage(BaseModel):
    url: HttpUrl
    title: str = ""
    text: str = Field(min_length=1)


class TavilyClient:
    """Small REST client for Tavily search and extraction."""

    def __init__(self, api_key: str | None = None, *, timeout: float = 45):
        self.api_key = (api_key or os.getenv("TAVILY_API_KEY", "")).strip()
        self.base_url = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com").rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise TavilyError("未配置 Tavily API Key，请设置 TAVILY_API_KEY 环境变量")

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise TavilyError("Tavily 请求超时") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            raise TavilyError(f"Tavily 请求失败（HTTP {exc.response.status_code}）：{detail}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise TavilyError("无法连接 Tavily API") from exc
        if not isinstance(data, dict):
            raise TavilyError("Tavily 返回格式无效")
        return data

    def search(self, query: str, *, num: int = 10, start_date: date | None = None) -> list[TavilySearchItem]:
        payload: dict[str, Any] = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": min(max(num, 1), 20),
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        if start_date:
            payload["start_date"] = start_date.isoformat()
        data = self._post("search", payload)
        items: list[TavilySearchItem] = []
        for raw in data.get("results", []):
            if not isinstance(raw, dict) or not raw.get("url"):
                continue
            try:
                items.append(TavilySearchItem.model_validate(raw))
            except ValidationError:
                continue
        return items

    def extract(self, url: str) -> TavilyPage:
        data = self._post(
            "extract",
            {"api_key": self.api_key, "urls": [url], "format": "markdown", "extract_depth": "advanced"},
        )
        results = data.get("results") or []
        raw = results[0] if results else {}
        text = raw.get("raw_content") or raw.get("content") or ""
        if not text.strip():
            raise TavilyError("Tavily 未提取到有效正文")
        try:
            return TavilyPage(url=raw.get("url") or url, title=raw.get("title") or "", text=text.strip())
        except ValidationError as exc:
            raise TavilyError("Tavily 返回的网页正文无效") from exc
