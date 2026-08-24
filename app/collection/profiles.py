from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PageResponse(BaseModel):
    requested_url: str
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    encoding: str = "utf-8"
    redirect_chain: list[str] = Field(default_factory=list)
    robots_status: str = "not_checked"

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")


class ArticleItem(BaseModel):
    content_kind: Literal["article"] = "article"
    source_item_key: str = Field(min_length=1, max_length=1000)
    canonical_url: str
    title: str = Field(min_length=1, max_length=500)
    published_at: date | None = None
    published_text: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1)
    raw_payload: dict[str, str] = Field(default_factory=dict)


class RecordItem(BaseModel):
    content_kind: Literal["table_record"] = "table_record"
    source_item_key: str = Field(min_length=1, max_length=1000)
    canonical_url: str
    title: str = Field(min_length=1, max_length=500)
    published_at: date | None = None
    published_text: str | None = Field(default=None, max_length=200)
    fields: dict[str, str]
    standard_fields: dict[str, str] = Field(default_factory=dict)
    display_fields: dict[str, str] = Field(default_factory=dict)

    @property
    def body(self) -> str:
        values = self.display_fields or self.fields
        return "\n".join(f"{name}：{value}" for name, value in values.items())


class PaginationProfile(BaseModel):
    kind: Literal["none", "url_template", "link", "form_get", "form_post"] = "none"
    template: str | None = None
    next_url: str | None = None
    action: str | None = None
    page_field: str | None = None
    page_size_field: str | None = None
    page_size: int | None = None
    static_fields: dict[str, str] = Field(default_factory=dict)
    start_page: int = 1


class ProfileValidation(BaseModel):
    pages_checked: int = 0
    item_count: int = 0
    field_completeness: float = 0
    dates_parseable: bool = False
    pagination_changes: bool = False
    stable_keys: bool = False


class ArticleDiscoveryProfile(BaseModel):
    kind: Literal["feed", "sitemap", "html_links", "direct"]
    article_url_pattern: str | None = None
    max_sitemap_depth: int = Field(default=1, ge=0, le=2)

    @field_validator("article_url_pattern")
    @classmethod
    def valid_pattern(cls, value: str | None) -> str | None:
        if value:
            if len(value) > 500:
                raise ValueError("文章 URL 规则过长")
            import re
            re.compile(value)
        return value


class CollectionProfile(BaseModel):
    profile_version: int = 1
    content_kind: Literal["table_records", "articles"] = "table_records"
    transport: Literal["http"] = "http"
    source_url: str
    entry: str
    table_index: int = 0
    pagination: PaginationProfile = Field(default_factory=PaginationProfile)
    fields: dict[str, str] = Field(default_factory=dict)
    article_discovery: ArticleDiscoveryProfile | None = None
    date_order: Literal["descending", "ascending", "unknown"] = "unknown"
    detection_method: Literal["deterministic", "llm"] = "deterministic"
    confidence: float = Field(ge=0, le=1)
    fingerprint: str
    allowed_hosts: list[str] = Field(default_factory=list)
    validation: ProfileValidation | None = None
    model_name: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_validated_at: datetime | None = None
