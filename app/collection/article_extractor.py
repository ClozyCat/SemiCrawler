from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Callable

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from .profiles import ArticleItem, PageResponse
from .record_extractor import parse_record_date

_ARTICLE_TYPES = {"article", "newsarticle", "blogposting", "reportagenewsarticle"}
_UNWANTED = "script,style,noscript,nav,aside,footer,form,.advertisement,.ads,.related,.recommend,.share,.copyright"


class ArticleExtraction(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    published_text: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=50)


def _text(node) -> str:
    return "\n".join(line.strip() for line in node.get_text("\n").splitlines() if line.strip()) if node else ""


def _jsonld_objects(soup: BeautifulSoup) -> list[dict]:
    objects: list[dict] = []
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(node.string or node.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        pending = value if isinstance(value, list) else [value]
        for item in pending:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                objects.extend(child for child in graph if isinstance(child, dict))
            objects.append(item)
    return objects


def _article_jsonld(soup: BeautifulSoup) -> dict | None:
    for item in _jsonld_objects(soup):
        kind = item.get("@type", "")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(str(value).casefold() in _ARTICLE_TYPES for value in kinds):
            return item
    return None


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        node = soup.select_one(f'meta[property="{name}"], meta[name="{name}"]')
        if node and node.get("content"):
            return " ".join(node["content"].split())
    return ""


def _best_body_node(soup: BeautifulSoup):
    semantic = soup.select_one("article") or soup.select_one("main")
    if semantic and len(_text(semantic)) >= 50:
        return semantic
    best = None
    best_score = 0.0
    for node in soup.select("section,div"):
        paragraphs = node.find_all("p", recursive=True)
        paragraph_text = "\n".join(_text(item) for item in paragraphs)
        length = len(paragraph_text)
        if length < 50:
            continue
        links = sum(len(_text(link)) for link in node.select("a"))
        hint = " ".join(node.get("class") or []) + " " + (node.get("id") or "")
        bonus = 300 if re.search(r"article|content|detail|正文|内容", hint, re.I) else 0
        penalty = 500 if re.search(r"comment|footer|related|recommend|sidebar|列表", hint, re.I) else 0
        score = length - links * 1.5 + len(paragraphs) * 30 + bonus - penalty
        if score > best_score:
            best, best_score = node, score
    return best


class ArticleExtractor:
    def extract(self, response: PageResponse,
                llm_fallback: Callable[[dict], ArticleExtraction] | None = None) -> ArticleItem:
        soup = BeautifulSoup(response.content, "html.parser")
        jsonld = _article_jsonld(soup) or {}
        title = str(jsonld.get("headline") or _meta(soup, "og:title", "twitter:title")
                    or _text(soup.select_one("h1")) or _text(soup.title)).strip()
        published_text = str(jsonld.get("datePublished") or _meta(
            soup, "article:published_time", "date", "pubdate", "publishdate"
        ) or _text(soup.select_one("time[datetime], time, .date, .publish-time, .pubtime"))).strip()
        body = str(jsonld.get("articleBody") or "").strip()
        if len(body) < 50:
            candidate = _best_body_node(soup)
            if candidate:
                for unwanted in candidate.select(_UNWANTED):
                    unwanted.decompose()
                body = _text(candidate)
        if (not title or len(body) < 50) and llm_fallback:
            result = llm_fallback({
                "url": response.url, "title_candidates": [title, _text(soup.title)],
                "text": _text(soup.body)[:24_000], "schema": ArticleExtraction.model_json_schema(),
            })
            title, published_text, body = result.title, result.published_text or "", result.body
        if not title or len(body) < 50:
            raise ValueError(f"通用正文抽取失败（标题 {bool(title)}，正文 {len(body)} 字）")
        published_at = parse_record_date(published_text)
        return ArticleItem(
            source_item_key=response.url, canonical_url=response.url, title=title[:500],
            published_at=published_at, published_text=published_text[:200] or None,
            body=body, raw_payload={"extraction": "jsonld" if jsonld.get("articleBody") else "semantic"},
        )
