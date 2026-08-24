from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterator, Protocol

from .inspection import PageInspector
from .profiles import CollectionProfile, PageResponse, RecordItem
from .record_extractor import RecordExtractor


class Fetcher(Protocol):
    def fetch(self, url: str, method: str = "GET", form: dict[str, str] | None = None) -> PageResponse: ...


@dataclass(frozen=True)
class RecordPage:
    number: int
    response: PageResponse
    items: tuple[RecordItem, ...]


class CollectionExecutor:
    def __init__(self, fetcher: Fetcher, inspector: PageInspector | None = None,
                 extractor: RecordExtractor | None = None):
        self.fetcher = fetcher
        self.inspector = inspector or PageInspector()
        self.extractor = extractor or RecordExtractor()
        self.last_stop_reason: str | None = None

    def pages(self, profile: CollectionProfile, max_pages: int, max_items: int,
              start_date: date | None = None) -> Iterator[RecordPage]:
        pagination = profile.pagination
        self.last_stop_reason = None
        page = pagination.start_page
        current_url = profile.entry
        total_items = 0
        for offset in range(max_pages):
            if pagination.kind == "url_template":
                current_url = (pagination.template or profile.entry).format(page=page)
                response = self.fetcher.fetch(current_url)
            elif pagination.kind in {"form_get", "form_post"} and offset > 0:
                form = dict(pagination.static_fields)
                form[pagination.page_field or "page"] = str(page)
                if pagination.page_size_field and pagination.page_size:
                    form[pagination.page_size_field] = str(pagination.page_size)
                response = self.fetcher.fetch(
                    pagination.action or profile.entry,
                    method="POST" if pagination.kind == "form_post" else "GET", form=form,
                )
            else:
                response = self.fetcher.fetch(current_url)

            remaining = max_items - total_items
            items = tuple(self.extractor.extract(response, profile)[:remaining])
            yield RecordPage(number=page, response=response, items=items)
            total_items += len(items)
            if total_items >= max_items or pagination.kind == "none" or not items:
                self.last_stop_reason = (
                    "达到条目上限" if total_items >= max_items else
                    "规则无分页" if pagination.kind == "none" else "页面没有解析出记录"
                )
                return
            dates = [item.published_at for item in items if item.published_at]
            if start_date and profile.date_order == "descending" and dates and min(dates) < start_date:
                self.last_stop_reason = "遇到早于起始日期的页面"
                return
            if pagination.kind == "link":
                observation = self.inspector.inspect(response)
                next_link = next((link for link in observation.links
                                  if link.rel.casefold() == "next" or re.search(r"下一页|下页|next", link.text, re.I)), None)
                if not next_link or next_link.url == current_url:
                    self.last_stop_reason = "没有下一页链接"
                    return
                current_url = next_link.url
            page += 1
