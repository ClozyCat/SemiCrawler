from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_source_item_key(context) -> str:
    """Use the canonical URL as the stable identity for ordinary articles."""
    return str(context.get_current_parameters().get("canonical_url") or "")


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    base_url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SourceVersion(Base):
    __tablename__ = "source_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class ModelSetting(Base):
    __tablename__ = "model_settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    base_url: Mapped[str] = mapped_column(
        String(500), default="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    model_name: Mapped[str] = mapped_column(String(200), default="qwen3-max")
    api_key: Mapped[str] = mapped_column(Text, default="")
    baidu_api_key: Mapped[str] = mapped_column(Text, default="")
    tavily_api_key: Mapped[str] = mapped_column(Text, default="")
    anysearch_api_key: Mapped[str] = mapped_column(Text, default="")
    request_headers_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    keyword_config_json: Mapped[str] = mapped_column(Text, default="[]")
    keyword_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CollectionTask(Base):
    __tablename__ = "collection_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    start_date: Mapped[date] = mapped_column(Date)
    source_ids_json: Mapped[str] = mapped_column(Text)
    source_snapshot_json: Mapped[str] = mapped_column(Text)
    keyword_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_structure_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    keyword_config_json: Mapped[str] = mapped_column(Text, default="[]")
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    deduplicated_count: Mapped[int] = mapped_column(Integer, default=0)
    structured_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    frequency: Mapped[str] = mapped_column(String(20))
    hour: Mapped[int] = mapped_column(Integer, default=0)
    weekday: Mapped[int | None] = mapped_column(Integer)
    monthday: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    source_ids_json: Mapped[str] = mapped_column(Text)
    keyword_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_structure_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TaskLog(Base):
    __tablename__ = "task_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("collection_tasks.id"), index=True)
    level: Mapped[str] = mapped_column(String(20), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class RawArticle(Base):
    __tablename__ = "raw_articles"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_tasks.id"), index=True
    )
    canonical_url: Mapped[str] = mapped_column(String(1000), unique=True)
    source_item_key: Mapped[str] = mapped_column(
        String(1000), default=default_source_item_key
    )
    content_kind: Mapped[str] = mapped_column(String(30), default="article")
    raw_payload_json: Mapped[str] = mapped_column(Text, default="{}")
    title: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[date | None] = mapped_column(Date)
    published_text: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(200))
    llm_output: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class StructuredRecord(Base):
    __tablename__ = "structured_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_articles.id"), index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_tasks.id"), index=True
    )
    region: Mapped[str] = mapped_column(String(120), default="")
    organization: Mapped[str] = mapped_column(String(300), default="")
    company_name: Mapped[str] = mapped_column(String(500), default="")
    event_date: Mapped[date | None] = mapped_column(Date)
    info_type: Mapped[str] = mapped_column(String(50), index=True)
    investment_amount: Mapped[str] = mapped_column(String(200), default="未披露")
    project_name: Mapped[str] = mapped_column(String(500), default="")
    source_name: Mapped[str] = mapped_column(String(120), index=True)
    original_url: Mapped[str] = mapped_column(String(1000), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    confidence_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="completed")
    amount_value: Mapped[str | None] = mapped_column(String(100))
    amount_currency: Mapped[str | None] = mapped_column(String(30))
    amount_unit: Mapped[str | None] = mapped_column(String(30))
    amount_note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ExportRecord(Base):
    __tablename__ = "export_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    format: Mapped[str] = mapped_column(String(10))
    row_count: Mapped[int] = mapped_column(Integer)
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    filename: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
