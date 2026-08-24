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
    version: Literal[1] = 1
    entry_urls: list[str] = Field(min_length=1, max_length=20)
    article_url_pattern: str
    selectors: Selectors
    date_formats: list[str] = Field(default_factory=lambda: ["%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"])
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    request: RequestConfig = Field(default_factory=RequestConfig)

    @field_validator("article_url_pattern")
    @classmethod
    def valid_pattern(cls, value: str) -> str:
        re.compile(value)
        return value

    @model_validator(mode="after")
    def valid_selectors(self):
        sample = BeautifulSoup("<html><body><h1>x</h1><a href='/x'>x</a></body></html>", "html.parser")
        for selector in self.selectors.model_dump().values():
            sample.select(selector)
        if self.pagination.next_page_selector:
            sample.select(self.pagination.next_page_selector)
        return self


class SourceLimits(BaseModel):
    rate_limit_per_minute: int = Field(default=12, ge=1, le=120)
    timeout_seconds: float = Field(default=20, ge=3, le=60)
    max_pages: int = Field(default=100, ge=1, le=1000)
    max_items: int = Field(default=5000, ge=1, le=100_000)


class SourceConfigV2(BaseModel):
    version: Literal[2]
    entry_urls: list[str] = Field(min_length=1, max_length=20)
    mode: Literal["auto", "profile"] = "auto"
    allowed_hosts: list[str] = Field(default_factory=list, max_length=20)
    content_hint: str = Field(default="", max_length=1000)
    limits: SourceLimits = Field(default_factory=SourceLimits)
    learned_profile: dict[str, Any] | None = None

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, value: list[str]) -> list[str]:
        hosts: list[str] = []
        for item in value:
            host = item.strip().rstrip(".").lower()
            if not host or "://" in host or "/" in host or "@" in host:
                raise ValueError("允许主机必须是纯域名")
            hosts.append(host)
        return list(dict.fromkeys(hosts))


def validate_source_config(base_url: str, config: dict[str, Any]) -> SourceConfig | SourceConfigV2:
    version = config.get("version", 1) if isinstance(config, dict) else None
    if version not in {1, 2}:
        raise ValueError("仅支持来源配置版本 1 或 2")
    parsed = SourceConfigV2.model_validate(config) if version == 2 else SourceConfig.model_validate(config)
    base = urlparse(base_url)
    allowed_host = (base.hostname or "").rstrip(".").lower()
    if not allowed_host:
        raise ValueError("站点地址缺少有效域名")
    if base.scheme not in {"http", "https"} or base.username or base.password:
        raise ValueError("站点地址必须是不含用户名密码的 http(s) URL")
    allowed_hosts = {allowed_host}
    if isinstance(parsed, SourceConfigV2):
        allowed_hosts.update(parsed.allowed_hosts)
    for entry in parsed.entry_urls:
        url = urlparse(entry)
        entry_host = (url.hostname or "").rstrip(".").lower()
        if url.scheme not in {"http", "https"} or url.username or url.password or entry_host not in allowed_hosts:
            raise ValueError("入口地址必须使用不含用户名密码的 http(s) URL，且主机已获允许")
    return parsed
