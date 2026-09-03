from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, field_validator, model_validator


class Selectors(BaseModel):
    list_links: str = "a"
    title: str
    date: str
    content: str


class RequestConfig(BaseModel):
    rate_limit_per_minute: int = Field(default=20, ge=1, le=120)
    timeout_seconds: float = Field(default=20, ge=3, le=60)


class PaginationConfig(BaseModel):
    next_page_selector: str | None = None
    max_pages: int = Field(default=20, ge=1, le=100)


class SourceConfig(BaseModel):
    type: Literal["crawler"] = "crawler"
    entry_urls: list[str] = Field(min_length=1, max_length=20)
    article_url_pattern: str
    selectors: Selectors
    date_formats: list[str] = Field(
        default_factory=lambda: ["%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"]
    )
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    request: RequestConfig = Field(default_factory=RequestConfig)

    @field_validator("article_url_pattern")
    @classmethod
    def valid_pattern(cls, value: str) -> str:
        re.compile(value)
        return value

    @model_validator(mode="after")
    def valid_selectors(self):
        sample = BeautifulSoup(
            "<html><body><h1>x</h1><a href='/x'>x</a></body></html>", "html.parser"
        )
        for selector in self.selectors.model_dump().values():
            sample.select(selector)
        if self.pagination.next_page_selector:
            sample.select(self.pagination.next_page_selector)
        return self


class WebSearchSourceConfig(BaseModel):
    type: Literal["web_search"]
    query: str = Field(min_length=2, max_length=2000)
    source_hint: str = Field(default="", max_length=2000)
    max_results: int = Field(default=10, ge=1, le=20)

    @field_validator("query", "source_hint")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()


def source_type(config: dict[str, Any]) -> Literal["crawler", "web_search"]:
    return "web_search" if config.get("type") == "web_search" else "crawler"


def validate_source_config(
    base_url: str, config: dict[str, Any]
) -> SourceConfig | WebSearchSourceConfig:
    if source_type(config) == "web_search":
        return WebSearchSourceConfig.model_validate(config)
    parsed = SourceConfig.model_validate(config)
    allowed_host = (urlparse(base_url).hostname or "").lower()
    if not allowed_host:
        raise ValueError("站点地址缺少有效域名")
    for entry in parsed.entry_urls:
        url = urlparse(entry)
        if (
            url.scheme not in {"http", "https"}
            or (url.hostname or "").lower() != allowed_host
        ):
            raise ValueError("入口地址必须使用 http(s) 且与站点地址同域")
    return parsed
