from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from .inspection import PageObservation
from .profiles import CollectionProfile, PaginationProfile


def normalize_header(value: str) -> str:
    return re.sub(r"[\s/／、()（）:：-]+", "", value).casefold()


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("项目名称", "工程名称", "事项名称", "标题"),
    "project_code": ("项目代码", "项目编号", "工程编号"),
    "approval_item": ("审批事项", "办理事项", "许可事项"),
    "approval_authority": ("审批部门", "审批机关", "办理部门"),
    "organization": ("部门区划", "所属地区", "行政区划", "区域"),
    "approval_result": ("审批结果", "办理结果", "许可结果"),
    "approval_document": ("批复文号", "批准文号", "文号"),
    "published_at": ("审批时间", "办理时间", "批复时间", "发布日期", "日期"),
}


def map_headers(headers: list[str]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    normalized = [(header, normalize_header(header)) for header in headers]
    for field, aliases in FIELD_ALIASES.items():
        for header, candidate in normalized:
            if any(normalize_header(alias) in candidate for alias in aliases):
                mapped[field] = header
                break
    return mapped


@dataclass(frozen=True)
class DetectionResult:
    profile: CollectionProfile | None
    inspect_urls: tuple[str, ...] = ()
    reason: str = ""


class DeterministicDetector:
    def detect(self, observation: PageObservation, source_url: str,
               allowed_hosts: list[str]) -> DetectionResult:
        candidates: list[tuple[int, int, dict[str, str]]] = []
        for table in observation.tables:
            fields = map_headers(table.headers)
            score = len(fields) + (2 if "title" in fields else 0) + (2 if "published_at" in fields else 0)
            candidates.append((score, table.index, fields))
        candidates.sort(reverse=True)
        if candidates and candidates[0][0] >= 7:
            score, table_index, fields = candidates[0]
            confidence = min(0.99, 0.55 + len(fields) * 0.055)
            return DetectionResult(profile=CollectionProfile(
                source_url=source_url, entry=observation.url, table_index=table_index,
                pagination=self.detect_pagination(observation), fields=fields,
                date_order="unknown", confidence=confidence, fingerprint=observation.fingerprint,
                allowed_hosts=allowed_hosts,
            ))
        if observation.iframes:
            return DetectionResult(profile=None, inspect_urls=tuple(observation.iframes), reason="发现 iframe 数据候选")
        return DetectionResult(profile=None, reason="未发现字段充分的 HTML 数据表")

    def detect_pagination(self, observation: PageObservation) -> PaginationProfile:
        for form in sorted(observation.forms, key=lambda item: len(item.fields), reverse=True):
            page_field = next((name for name in form.fields if re.search(r"(?:page.?no|page|pageno)$", name, re.I)), None)
            if not page_field:
                continue
            size_field = next((name for name in form.fields if re.search(r"page.?size", name, re.I)), None)
            static = {name: value for name, value in form.fields.items() if name not in {page_field, size_field}}
            page_size = int(form.fields[size_field]) if size_field and form.fields[size_field].isdigit() else None
            return PaginationProfile(
                kind="form_post" if form.method == "POST" else "form_get", action=form.action,
                page_field=page_field, page_size_field=size_field, page_size=page_size,
                static_fields=static, start_page=int(form.fields.get(page_field) or 1),
            )

        parsed = urlparse(observation.url)
        match = re.search(r"(?P<page>\d+)(?=\.(?:s?html?|htm)$)", parsed.path, re.I)
        total_pages = next((value for name, value in observation.numeric_controls.items()
                            if normalize_header(name) in {"totalpage", "pagecount", "totalpages"}), 0)
        if match and total_pages > 1:
            path = parsed.path[:match.start()] + "{page}" + parsed.path[match.end():]
            template = urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))
            return PaginationProfile(kind="url_template", template=template, start_page=int(match.group("page")))

        next_link = next((link for link in observation.links
                          if link.rel.casefold() == "next" or re.search(r"下一页|下页|next", link.text, re.I)), None)
        if next_link and next_link.url != observation.url:
            return PaginationProfile(kind="link", next_url=next_link.url)
        return PaginationProfile()
