from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, Field, HttpUrl, ValidationError


class AnySearchError(ValueError):
    """Raised when AnySearch cannot return a usable response."""


class AnySearchSearchItem(BaseModel):
    title: str = Field(default="网页结果", max_length=500)
    url: HttpUrl
    content: str = ""
    published_date: str | None = None
    score: float | None = None


class AnySearchPage(BaseModel):
    url: HttpUrl
    title: str = ""
    text: str = Field(min_length=1)


class AnySearchClient:
    """REST client for AnySearch unified search and page extraction."""

    def __init__(self, api_key: str | None = None, *, timeout: float = 45):
        self.api_key = (api_key or os.getenv("ANYSEARCH_API_KEY", "")).strip()
        self.base_url = os.getenv(
            "ANYSEARCH_API_BASE_URL", "https://api.anysearch.com"
        ).rstrip("/")
        self.timeout = timeout

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "X-Anysearch-Client": "SemiCrawler/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = httpx.post(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise AnySearchError("AnySearch 请求超时") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            raise AnySearchError(
                f"AnySearch 请求失败（HTTP {exc.response.status_code}）：{detail}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AnySearchError("无法连接 AnySearch API") from exc
        if not isinstance(data, dict):
            raise AnySearchError("AnySearch 返回格式无效")
        if data.get("code", 0) != 0:
            raise AnySearchError(
                f"AnySearch 请求失败（{data.get('code')}）：{data.get('message') or '未知错误'}"
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
    ) -> list[AnySearchSearchItem]:
        del topic, start_date, end_date
        query_text = query.strip()
        if domains:
            normalized_domains = list(dict.fromkeys(domain.lower() for domain in domains))
            site_terms = " ".join(f"site:{domain}" for domain in normalized_domains)
            query_text = f"{query_text} {site_terms}".strip()
        data = self._post(
            "v1/search",
            {
                "query": query_text,
                "max_results": min(max(num, 1), 100),
                "content_types": ["web", "news"],
            },
        )
        payload = data.get("data") or {}
        raw_results = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(raw_results, list):
            raise AnySearchError("AnySearch 返回的 results 格式无效")
        items: list[AnySearchSearchItem] = []
        for raw in raw_results:
            if not isinstance(raw, dict) or not raw.get("url"):
                continue
            normalized = {
                "title": raw.get("title") or "网页结果",
                "url": raw["url"],
                "content": raw.get("content") or raw.get("snippet") or "",
                "published_date": raw.get("published_date") or raw.get("published_at") or raw.get("date"),
                "score": raw.get("score"),
            }
            try:
                items.append(AnySearchSearchItem.model_validate(normalized))
            except ValidationError:
                continue
        return items

    def extract(self, url: str) -> AnySearchPage:
        data = self._post("v1/extract", {"url": url})
        raw = data.get("data") or {}
        text = raw.get("content") or raw.get("raw_content") or ""
        if not isinstance(text, str) or not text.strip():
            raise AnySearchError("AnySearch 未提取到有效正文")
        try:
            return AnySearchPage(
                url=raw.get("url") or url,
                title=raw.get("title") or "",
                text=text.strip(),
            )
        except ValidationError as exc:
            raise AnySearchError("AnySearch 返回的网页正文无效") from exc
