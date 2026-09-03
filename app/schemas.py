from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_serializer,
    field_validator,
)

from .constants import DEFAULT_START_DATE, INFO_TYPES

SHANGHAI_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


def _as_shanghai_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(SHANGHAI_TIMEZONE)


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: HttpUrl
    enabled: bool = True
    config: dict[str, Any]


class SourceTest(BaseModel):
    base_url: HttpUrl
    config: dict[str, Any]


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: HttpUrl | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    base_url: str
    enabled: bool
    builtin: bool
    source_type: str
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TaskCreate(BaseModel):
    source_ids: list[int] = Field(min_length=1)
    start_date: date = date.fromisoformat(DEFAULT_START_DATE)
    keyword_filter_enabled: bool = False
    auto_structure_enabled: bool = False
    keyword_config: Any | None = None

    @field_validator("source_ids")
    @classmethod
    def unique_sources(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class DeleteIds(BaseModel):
    ids: list[int] = Field(min_length=1)

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class TaskRead(BaseModel):
    id: int
    status: str
    start_date: date
    source_ids: list[int]
    source_snapshot: list[dict[str, Any]]
    fetched_count: int
    deduplicated_count: int
    structured_count: int
    failed_count: int
    progress: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    keyword_filter_enabled: bool = False
    auto_structure_enabled: bool = False
    keyword_config: Any = Field(default_factory=list)

    @field_serializer("created_at", "started_at", "completed_at")
    def serialize_task_time(self, value: datetime | None) -> datetime | None:
        return _as_shanghai_time(value)


class LogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    level: str
    message: str
    created_at: datetime

    @field_serializer("created_at")
    def serialize_log_time(self, value: datetime) -> datetime:
        return _as_shanghai_time(value)


class RecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    article_id: int | None
    task_id: int | None
    region: str
    organization: str
    company_name: str
    event_date: date | None
    info_type: str
    investment_amount: str
    project_name: str
    source_name: str
    original_url: str
    details: str
    status: str
    created_at: datetime
    updated_at: datetime


class RecordDetail(RecordRead):
    evidence: dict[str, str]
    confidence: dict[str, float]
    article: dict[str, Any] | None


class RecordList(BaseModel):
    items: list[RecordRead]
    total: int


class AnalyticsOverview(BaseModel):
    summary: dict[str, int]
    keywords: list[dict[str, Any]]
    graph: dict[str, list[dict[str, Any]]]
    info_types: list[dict[str, Any]]


class ArticleRead(BaseModel):
    id: int
    source_id: int
    source_name: str
    task_id: int | None
    canonical_url: str
    title: str
    published_at: date | None
    published_text: str | None
    body: str
    status: str
    error_message: str | None
    model_name: str | None
    collected_at: datetime
    record_count: int

    @field_serializer("collected_at")
    def serialize_collected_at(self, value: datetime) -> datetime:
        return _as_shanghai_time(value)


class ArticleList(BaseModel):
    items: list[ArticleRead]
    total: int


class StructureResult(BaseModel):
    article_id: int
    created_count: int
    status: str


class RecordUpdate(BaseModel):
    region: str | None = None
    organization: str | None = None
    company_name: str | None = None
    event_date: date | None = None
    info_type: str | None = None
    investment_amount: str | None = None
    project_name: str | None = None
    source_name: str | None = None
    original_url: str | None = None
    details: str | None = None

    @field_validator("info_type")
    @classmethod
    def known_info_type(cls, value: str | None) -> str | None:
        if value is not None and value not in INFO_TYPES:
            raise ValueError("未知的资讯类型")
        return value


class AppMeta(BaseModel):
    default_start_date: str = DEFAULT_START_DATE
    info_types: list[str] = INFO_TYPES
    phase: int = 4


class RequestHeaderSetting(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=4000)

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        key = value.strip()
        if not key or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key):
            raise ValueError("请求头名称格式无效")
        return key

    @field_validator("value")
    @classmethod
    def valid_value(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("请求头值不能包含换行符")
        return value


class ModelSettingUpdate(BaseModel):
    base_url: HttpUrl
    model_name: str = Field(min_length=1, max_length=200)
    api_key: str | None = None
    baidu_api_key: str | None = None
    tavily_api_key: str | None = None
    anysearch_api_key: str | None = None
    request_headers: list[RequestHeaderSetting] | None = None
    enabled: bool = False
    keyword_config: Any = Field(default_factory=list)
    keyword_filter_enabled: bool = False

    @field_validator("request_headers")
    @classmethod
    def unique_request_headers(
        cls, value: list[RequestHeaderSetting] | None
    ) -> list[RequestHeaderSetting] | None:
        if value is None:
            return value
        keys = [header.key.casefold() for header in value]
        if len(keys) != len(set(keys)):
            raise ValueError("请求头名称不能重复")
        return value


class ModelSettingRead(BaseModel):
    base_url: str
    model_name: str
    enabled: bool
    has_api_key: bool
    api_key_hint: str
    has_baidu_api_key: bool = False
    baidu_api_key_hint: str = ""
    has_tavily_api_key: bool = False
    tavily_api_key_hint: str = ""
    has_anysearch_api_key: bool = False
    anysearch_api_key_hint: str = ""
    request_headers: list[RequestHeaderSetting] = Field(default_factory=list)
    keyword_config: Any = Field(default_factory=list)
    keyword_filter_enabled: bool = False
