from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from .inspection import clean_text
from .probing import normalize_header
from .profiles import CollectionProfile, PageResponse, RecordItem

_PROJECT_CODE = re.compile(r"\d{4}-\d{6}-\d{2}-\d{2}-\d{6}")
_DATE = re.compile(r"20\d{2}[年/.-]\d{1,2}[月/.-]\d{1,2}日?")
_RESULTS = {"0": "不予通过", "1": "通过", "2": "未通过", "3": "需补正", "5": "撤销", "6": "退回申请人"}


def parse_record_date(value: str) -> date | None:
    match = _DATE.search(value)
    if not match:
        return None
    candidate = match.group(0)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def _cell_value(cell) -> str:
    visible = clean_text(cell)
    title = " ".join((cell.get("title") or "").split())
    return title if title and ("..." in visible or len(title) > len(visible)) else visible


def _semantic_value(field: str, cell) -> str:
    if field == "project_code":
        match = _PROJECT_CODE.search(_cell_value(cell))
        return match.group(0) if match else _cell_value(cell)
    if field == "title":
        named = cell.select_one(".full_name")
        if named:
            return clean_text(named)
        anchor = cell.select_one("a[title]")
        if anchor and anchor.get("title"):
            return " ".join(anchor["title"].split())
        value = _cell_value(cell)
        code = _PROJECT_CODE.search(value)
        return value[code.end():].strip(" []【】-/") if code and code.end() < len(value) else value
    value = _cell_value(cell)
    if field == "approval_result":
        return _RESULTS.get(value.strip(), value)
    return value


def stable_record_key(values: dict[str, str]) -> str:
    if values.get("approval_authority") or values.get("approval_document"):
        names = ("project_code", "approval_item", "approval_authority", "approval_document", "published_at")
    elif values.get("project_code"):
        names = ("project_code", "approval_item", "published_at", "approval_result")
    else:
        names = tuple(sorted(values))
    evidence = "\x1f".join(" ".join(values.get(name, "").casefold().split()) for name in names)
    return hashlib.sha256(evidence.encode("utf-8")).hexdigest()


class RecordExtractor:
    def extract(self, response: PageResponse, profile: CollectionProfile) -> list[RecordItem]:
        soup = BeautifulSoup(response.content, "html.parser")
        tables = soup.select("table")
        if profile.table_index >= len(tables):
            raise ValueError("已学习的数据表不存在")
        rows = tables[profile.table_index].select("tr")
        if not rows:
            return []
        header_index = next((index for index, row in enumerate(rows) if row.select("th")), 0)
        header_cells = rows[header_index].select("th,td")
        headers = [clean_text(cell) for cell in header_cells]
        positions = {normalize_header(header): index for index, header in enumerate(headers)}
        field_positions = {
            field: positions[normalize_header(header)]
            for field, header in profile.fields.items() if normalize_header(header) in positions
        }
        if len(field_positions) < max(3, len(profile.fields) - 1):
            raise ValueError("已学习的表头映射不再匹配页面")

        items: list[RecordItem] = []
        for row in rows[header_index + 1:]:
            cells = row.select("td,th")
            if len(cells) < len(headers):
                continue
            raw = {header: _cell_value(cells[index]) for index, header in enumerate(headers) if header}
            semantic = {field: _semantic_value(field, cells[index]) for field, index in field_positions.items()}
            display = dict(raw)
            header_use_count = {
                header: sum(mapped_header == header for mapped_header in profile.fields.values())
                for header in profile.fields.values()
            }
            for field, value in semantic.items():
                header = profile.fields[field]
                if header_use_count[header] == 1:
                    display[header] = value
            title = semantic.get("title", "").strip()
            if not title:
                continue
            published_text = semantic.get("published_at", "").strip() or None
            items.append(RecordItem(
                source_item_key=stable_record_key(semantic), canonical_url=profile.source_url,
                title=title[:500], published_at=parse_record_date(published_text or ""),
                published_text=(published_text or "")[:200] or None, fields=raw,
                standard_fields=semantic, display_fields=display,
            ))
        return items
