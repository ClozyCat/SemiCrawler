from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Callable, Iterator

from .article_discovery import DiscoveredUrl, discover_html_links, parse_feed, parse_sitemap
from .article_extractor import ArticleExtraction, ArticleExtractor
from .executors import Fetcher
from .inspection import PageInspector
from .profiles import ArticleItem, CollectionProfile, ProfileValidation


@dataclass(frozen=True)
class ArticleResult:
    url: str
    item: ArticleItem | None = None
    error: str | None = None


class ArticleCollectionExecutor:
    def __init__(self, fetcher: Fetcher, inspector: PageInspector | None = None,
                 extractor: ArticleExtractor | None = None,
                 llm_fallback: Callable[[dict], ArticleExtraction] | None = None):
        self.fetcher = fetcher
        self.inspector = inspector or PageInspector()
        self.extractor = extractor or ArticleExtractor()
        self.llm_fallback = llm_fallback

    def _listing_responses(self, profile: CollectionProfile, max_pages: int):
        pagination = profile.pagination
        page = pagination.start_page
        current = profile.entry
        for offset in range(max_pages):
            if pagination.kind == "url_template":
                current = (pagination.template or profile.entry).format(page=page)
                response = self.fetcher.fetch(current)
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
                response = self.fetcher.fetch(current)
            yield response
            if pagination.kind == "none":
                return
            if pagination.kind == "link":
                observation = self.inspector.inspect(response)
                link = next((item for item in observation.links if item.rel.casefold() == "next"
                             or re.search(r"下一页|下页|next", item.text, re.I)), None)
                if not link or link.url == current:
                    return
                current = link.url
            page += 1

    def discovered(self, profile: CollectionProfile, max_pages: int,
                   max_items: int) -> Iterator[DiscoveredUrl]:
        discovery = profile.article_discovery
        if not discovery:
            raise ValueError("文章规则缺少发现方式")
        seen: set[str] = set()

        def emit(values: list[DiscoveredUrl]):
            for value in values:
                if value.url not in seen and len(seen) < max_items:
                    seen.add(value.url)
                    yield value

        if discovery.kind == "direct":
            yield DiscoveredUrl(profile.entry)
            return
        if discovery.kind == "feed":
            yield from emit(parse_feed(self.fetcher.fetch(profile.entry)))
            return
        if discovery.kind == "sitemap":
            queue = [(profile.entry, 0)]
            pages = 0
            while queue and pages < max_pages and len(seen) < max_items:
                url, depth = queue.pop(0)
                urls, indexes = parse_sitemap(self.fetcher.fetch(url))
                pages += 1
                yield from emit(urls)
                if depth < discovery.max_sitemap_depth:
                    queue.extend((item, depth + 1) for item in indexes)
            return
        for response in self._listing_responses(profile, max_pages):
            values = discover_html_links(response, discovery.article_url_pattern)
            yield from emit(values)
            if len(seen) >= max_items:
                return

    def items(self, profile: CollectionProfile, max_pages: int, max_items: int,
              start_date: date | None = None) -> Iterator[ArticleResult]:
        for discovered in self.discovered(profile, max_pages, max_items):
            if start_date and discovered.published_at and discovered.published_at < start_date:
                if profile.date_order == "descending":
                    return
                continue
            try:
                item = self.extractor.extract(self.fetcher.fetch(discovered.url), self.llm_fallback)
                if not item.published_at and discovered.published_at:
                    item = item.model_copy(update={
                        "published_at": discovered.published_at,
                        "published_text": discovered.published_at.isoformat(),
                    })
                yield ArticleResult(url=discovered.url, item=item)
            except Exception as exc:
                yield ArticleResult(url=discovered.url, error=str(exc))


class ArticleProfileValidator:
    def __init__(self, executor: ArticleCollectionExecutor):
        self.executor = executor

    def validate(self, profile: CollectionProfile) -> CollectionProfile:
        required = 1 if profile.article_discovery and profile.article_discovery.kind == "direct" else 2
        results: list[ArticleItem] = []
        errors: list[str] = []
        for result in self.executor.items(profile, max_pages=2, max_items=required):
            if result.item:
                results.append(result.item)
            elif result.error:
                errors.append(result.error)
            if len(results) >= required:
                break
        if len(results) < required:
            raise ValueError(f"文章样本验证不足: {len(results)}/{required}；{'；'.join(errors[:2])}")
        dates = [item.published_at for item in results]
        parseable = sum(value is not None for value in dates) / len(dates) >= .5
        date_order = "unknown"
        known = [value for value in dates if value]
        if len(known) >= 2 and all(left >= right for left, right in zip(known, known[1:])):
            date_order = "descending"
        validation = ProfileValidation(
            pages_checked=min(2, required), item_count=len(results), field_completeness=1,
            dates_parseable=parseable, pagination_changes=True,
            stable_keys=len({item.source_item_key for item in results}) == len(results),
        )
        if not validation.stable_keys:
            raise ValueError("文章样本记录键不稳定")
        return profile.model_copy(update={
            "validation": validation, "date_order": date_order, "last_validated_at": datetime.now(UTC),
        })
