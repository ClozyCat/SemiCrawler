from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .inspection import clean_text
from .profiles import ArticleDiscoveryProfile, CollectionProfile, PageResponse
from .record_extractor import parse_record_date


@dataclass(frozen=True)
class DiscoveredUrl:
    url: str
    published_at: date | None = None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def parse_discovery_date(value: str) -> date | None:
    parsed = parse_record_date(value)
    if parsed:
        return parsed
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, OverflowError):
        return None


def xml_document_kind(content: bytes) -> str | None:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    name = _local_name(root.tag)
    if name in {"rss", "feed", "rdf"}:
        return "feed"
    if name in {"urlset", "sitemapindex"}:
        return "sitemap"
    return None


def parse_feed(response: PageResponse) -> list[DiscoveredUrl]:
    root = ET.fromstring(response.content)
    result: list[DiscoveredUrl] = []
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        link = ""
        published = ""
        for child in list(node):
            name = _local_name(child.tag)
            if name == "link":
                link = child.get("href") or (child.text or "")
                if child.get("rel") not in {None, "alternate"}:
                    link = ""
            elif name in {"pubdate", "published", "updated", "date"}:
                published = child.text or ""
        if link:
            result.append(DiscoveredUrl(urljoin(response.url, link.strip()), parse_discovery_date(published)))
    return list(dict.fromkeys(result))


def parse_sitemap(response: PageResponse) -> tuple[list[DiscoveredUrl], list[str]]:
    root = ET.fromstring(response.content)
    urls: list[DiscoveredUrl] = []
    indexes: list[str] = []
    root_kind = _local_name(root.tag)
    for node in list(root):
        location = next((child.text or "" for child in list(node) if _local_name(child.tag) == "loc"), "").strip()
        modified = next((child.text or "" for child in list(node) if _local_name(child.tag) == "lastmod"), "").strip()
        if not location:
            continue
        if root_kind == "sitemapindex":
            indexes.append(urljoin(response.url, location))
        else:
            urls.append(DiscoveredUrl(urljoin(response.url, location), parse_discovery_date(modified)))
    return urls, indexes


def discover_html_links(response: PageResponse, pattern: str | None = None) -> list[DiscoveredUrl]:
    soup = BeautifulSoup(response.content, "html.parser")
    result: list[DiscoveredUrl] = []
    compiled = re.compile(pattern) if pattern else None
    for anchor in soup.select("article a[href], main a[href], li a[href], h2 a[href], h3 a[href]"):
        text = clean_text(anchor)
        url = urljoin(response.url, anchor.get("href", ""))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or len(text) < 6:
            continue
        if compiled and not compiled.search(parsed.path):
            continue
        if re.search(r"(?:/tag/|/category/|/author/|javascript:|#)$", url, re.I):
            continue
        result.append(DiscoveredUrl(url))
    return list(dict.fromkeys(result))


def infer_article_pattern(urls: list[str]) -> str | None:
    paths = [urlparse(url).path for url in urls]
    if len(paths) < 2:
        return None
    numeric = sum(bool(re.search(r"\d{4,}", path)) for path in paths)
    if numeric / len(paths) >= 0.6:
        return r"\d{4,}"
    suffixes = {path.rsplit(".", 1)[-1].casefold() for path in paths if "." in path.rsplit("/", 1)[-1]}
    if len(suffixes) == 1:
        return rf"\.{re.escape(next(iter(suffixes)))}$"
    return None


class ArticleProfileDetector:
    def detect(self, response: PageResponse, source_url: str, fingerprint: str,
               allowed_hosts: list[str], pagination=None) -> CollectionProfile | None:
        kind = xml_document_kind(response.content)
        if kind:
            return CollectionProfile(
                content_kind="articles", source_url=source_url, entry=response.url,
                article_discovery=ArticleDiscoveryProfile(kind=kind), confidence=.98,
                fingerprint=fingerprint, allowed_hosts=allowed_hosts,
            )
        soup = BeautifulSoup(response.content, "html.parser")
        article_marker = soup.select_one('article, script[type="application/ld+json"]')
        links = discover_html_links(response)
        if len(links) >= 2:
            pattern = infer_article_pattern([item.url for item in links])
            return CollectionProfile(
                content_kind="articles", source_url=source_url, entry=response.url,
                article_discovery=ArticleDiscoveryProfile(kind="html_links", article_url_pattern=pattern),
                pagination=pagination or {}, confidence=.82 if pattern else .72,
                fingerprint=fingerprint, allowed_hosts=allowed_hosts,
            )
        if article_marker:
            return CollectionProfile(
                content_kind="articles", source_url=source_url, entry=response.url,
                article_discovery=ArticleDiscoveryProfile(kind="direct"), confidence=.8,
                fingerprint=fingerprint, allowed_hosts=allowed_hosts,
            )
        return None
