from __future__ import annotations

import hashlib
import json
import warnings
from typing import Literal
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning
from pydantic import BaseModel, Field

from .profiles import PageResponse


def clean_text(node) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


class LinkObservation(BaseModel):
    text: str
    url: str
    rel: str = ""
    onclick: str = ""


class FormObservation(BaseModel):
    method: Literal["GET", "POST"]
    action: str
    fields: dict[str, str] = Field(default_factory=dict)


class TableObservation(BaseModel):
    index: int
    headers: list[str]
    row_count: int


class PageObservation(BaseModel):
    url: str
    title: str = ""
    iframes: list[str] = Field(default_factory=list)
    forms: list[FormObservation] = Field(default_factory=list)
    tables: list[TableObservation] = Field(default_factory=list)
    links: list[LinkObservation] = Field(default_factory=list)
    numeric_controls: dict[str, int] = Field(default_factory=dict)
    fingerprint: str


class PageInspector:
    def inspect(self, response: PageResponse) -> PageObservation:
        content_type = response.headers.get("content-type", "").lower()
        if "xml" in content_type:
            try:
                soup = BeautifulSoup(response.content, "xml")
            except Exception:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
                    soup = BeautifulSoup(response.content, "html.parser")
        else:
            soup = BeautifulSoup(response.content, "html.parser")
        iframes = list(dict.fromkeys(
            urljoin(response.url, node["src"])
            for node in soup.select("iframe[src]") if node.get("src", "").strip()
        ))
        forms: list[FormObservation] = []
        for form in soup.select("form"):
            inputs = form.select("input[name], select[name], textarea[name]")
            if any((node.get("type") or "").lower() in {"password", "file"} for node in inputs):
                continue
            method = (form.get("method") or "GET").upper()
            if method not in {"GET", "POST"}:
                continue
            fields: dict[str, str] = {}
            for node in inputs:
                if (node.get("type") or "").lower() in {"submit", "button", "image", "reset"}:
                    continue
                value = node.get("value", "")
                if node.name == "select":
                    option = node.select_one("option[selected]") or node.select_one("option")
                    value = option.get("value", "") if option else ""
                fields[node["name"]] = str(value)
            forms.append(FormObservation(
                method=method, action=urljoin(response.url, form.get("action") or response.url), fields=fields,
            ))

        tables: list[TableObservation] = []
        for index, table in enumerate(soup.select("table")):
            rows = table.select("tr")
            header_row = next((row for row in rows if row.select("th")), rows[0] if rows else None)
            headers = [clean_text(cell) for cell in header_row.select("th,td")] if header_row else []
            if headers:
                tables.append(TableObservation(index=index, headers=headers, row_count=max(0, len(rows) - 1)))

        links: list[LinkObservation] = []
        for node in soup.select("a")[:500]:
            href = (node.get("href") or "").strip()
            if not href and not node.get("onclick"):
                continue
            links.append(LinkObservation(
                text=clean_text(node)[:200], url=urljoin(response.url, href) if href else response.url,
                rel=" ".join(node.get("rel") or []), onclick=(node.get("onclick") or "")[:500],
            ))

        numeric_controls: dict[str, int] = {}
        for node in soup.select("input"):
            name = node.get("name") or node.get("id")
            value = (node.get("value") or "").strip()
            if name and value.isdigit():
                numeric_controls[name] = int(value)

        structure = {
            "iframes": iframes,
            "forms": [{"method": item.method, "action": item.action, "fields": sorted(item.fields)} for item in forms],
            "tables": [{"headers": item.headers, "columns": len(item.headers)} for item in tables],
        }
        fingerprint = hashlib.sha256(
            json.dumps(structure, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return PageObservation(
            url=response.url, title=clean_text(soup.title), iframes=iframes, forms=forms, tables=tables,
            links=links, numeric_controls=numeric_controls, fingerprint=fingerprint,
        )
