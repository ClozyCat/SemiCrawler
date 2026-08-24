from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


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

    @property
    def body(self) -> str:
        return "\n".join(f"{name}：{value}" for name, value in self.fields.items())
