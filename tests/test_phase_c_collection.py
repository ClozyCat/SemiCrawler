from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import func, select

from app.collection.adaptive import detect_and_validate
from app.collection.article_discovery import parse_sitemap
from app.collection.article_executor import ArticleCollectionExecutor
from app.collection.article_extractor import ArticleExtractor
from app.collection.probe_agent import ProbeAgent, ProbeAgentError
from app.collection.profiles import PageResponse
from app.database import SessionLocal
from app.models import CollectionTask, RawArticle, Source
from app.crawler import collect_source


def response(url: str, body: str, content_type: str = "text/html") -> PageResponse:
    return PageResponse(
        requested_url=url, url=url, status_code=200, headers={"content-type": content_type},
        content=body.encode("utf-8"), encoding="utf-8",
    )


def article_html(title: str, published: str, body: str) -> str:
    return f"""<!doctype html><html><head><script type="application/ld+json">{{
    "@type":"NewsArticle","headline":{json.dumps(title)},"datePublished":"{published}",
    "articleBody":{json.dumps(body)}
    }}</script></head><body><article><h1>{title}</h1><p>{body}</p></article></body></html>"""


class MappingFetcher:
    def __init__(self, pages: dict[str, PageResponse]):
        self.pages = pages
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def fetch(self, url: str, method: str = "GET", form: dict[str, str] | None = None) -> PageResponse:
        self.calls.append(url)
        return self.pages[url]


def test_jsonld_article_extraction():
    url = "https://news.example.org/2026/10001.html"
    body = "某公司宣布建设先进封装生产线，项目总投资十亿元，计划于年内开工并在两年内形成量产能力。"
    item = ArticleExtractor().extract(response(url, article_html("先进封装项目开工", "2026-08-24", body)))
    assert item.title == "先进封装项目开工"
    assert item.published_at == date(2026, 8, 24)
    assert "投资十亿元" in item.body
    assert item.raw_payload["extraction"] == "jsonld"


def test_feed_is_detected_validated_and_executed_without_model():
    feed_url = "https://news.example.org/feed.xml"
    first = "https://news.example.org/2026/10001.html"
    second = "https://news.example.org/2026/10002.html"
    feed = f"""<?xml version="1.0"?><rss><channel>
    <item><link>{first}</link><pubDate>Mon, 24 Aug 2026 08:00:00 GMT</pubDate></item>
    <item><link>{second}</link><pubDate>2026-08-23</pubDate></item></channel></rss>"""
    long_body = "这是半导体产业新闻的正文内容，包含项目建设、设备采购、技术研发和未来量产安排等可核验事实。" * 2
    fetcher = MappingFetcher({
        feed_url: response(feed_url, feed, "application/rss+xml"),
        first: response(first, article_html("第一篇产业新闻", "2026-08-24", long_body)),
        second: response(second, article_html("第二篇产业新闻", "2026-08-23", long_body)),
    })
    profile = detect_and_validate(fetcher, feed_url, ["news.example.org"])
    assert profile.content_kind == "articles"
    assert profile.article_discovery and profile.article_discovery.kind == "feed"
    assert profile.date_order == "descending"
    assert profile.validation and profile.validation.item_count == 2
    items = list(ArticleCollectionExecutor(fetcher).items(profile, max_pages=2, max_items=10))
    assert [item.item.title for item in items if item.item] == ["第一篇产业新闻", "第二篇产业新闻"]


def test_sitemap_parser_returns_urls_and_nested_indexes():
    index_url = "https://news.example.org/sitemap.xml"
    index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap><loc>https://news.example.org/news-1.xml</loc></sitemap></sitemapindex>"""
    urls, indexes = parse_sitemap(response(index_url, index, "application/xml"))
    assert urls == []
    assert indexes == ["https://news.example.org/news-1.xml"]


def test_probe_agent_rejects_unapproved_host_before_access():
    url = "https://news.example.org/"
    fetcher = MappingFetcher({url: response(url, "<html><title>News</title><body>empty</body></html>")})
    model = lambda _messages: json.dumps({"action": "inspect_url", "url": "https://internal.example/private"})
    with pytest.raises(ProbeAgentError, match="主机未获允许"):
        ProbeAgent(fetcher, model, url, ["news.example.org"]).run()
    assert fetcher.calls == [url]


def test_probe_agent_repairs_invalid_action_once_then_stops():
    url = "https://news.example.org/"
    fetcher = MappingFetcher({url: response(url, "<html><title>News</title><body>empty</body></html>")})
    outputs = iter(["{}", json.dumps({"action": "stop", "reason": "无法稳定识别"})])
    calls = []

    def model(messages):
        calls.append(messages)
        return next(outputs)

    with pytest.raises(ProbeAgentError, match="无法稳定识别"):
        ProbeAgent(fetcher, model, url, ["news.example.org"]).run()
    assert len(calls) == 2
    assert "上一动作不合法" in calls[1][-1]["content"]


def test_probe_agent_validates_direct_article_profile():
    url = "https://news.example.org/story"
    body = "该企业完成新一轮融资并启动先进制程设备研发，相关资金将用于建设实验线和扩大研发团队。" * 2
    fetcher = MappingFetcher({url: response(url, article_html("企业启动设备研发", "2026-08-24", body))})
    profile = {
        "content_kind": "articles", "source_url": url, "entry": url,
        "article_discovery": {"kind": "direct"}, "confidence": .75,
        "fingerprint": "model-proposed", "allowed_hosts": ["news.example.org"],
    }
    model = lambda _messages: json.dumps({"action": "propose_profile", "profile": profile})
    result = ProbeAgent(fetcher, model, url, ["news.example.org"]).run()
    assert result.detection_method == "llm"
    assert result.validation and result.validation.item_count == 1


def test_article_source_is_persisted_and_reused_without_probe_model(monkeypatch):
    feed_url = "https://news.example.org/feed.xml"
    first = "https://news.example.org/2026/10001.html"
    second = "https://news.example.org/2026/10002.html"
    feed = f"<rss><channel><item><link>{first}</link><pubDate>2026-08-24</pubDate></item><item><link>{second}</link><pubDate>2026-08-23</pubDate></item></channel></rss>"
    body = "项目完成先进封装设备研发并启动产线建设，相关计划包括设备采购、工艺验证和后续量产。" * 2
    fetcher = MappingFetcher({
        feed_url: response(feed_url, feed, "application/rss+xml"),
        first: response(first, article_html("第一条新闻", "2026-08-24", body)),
        second: response(second, article_html("第二条新闻", "2026-08-23", body)),
    })
    monkeypatch.setattr("app.crawler._adaptive_fetcher", lambda *_args: fetcher)
    config = {"version": 2, "entry_urls": [feed_url], "mode": "auto", "allowed_hosts": ["news.example.org"],
              "limits": {"rate_limit_per_minute": 120, "timeout_seconds": 20, "max_pages": 2, "max_items": 10}}
    with SessionLocal() as db:
        source = Source(name="文章来源测试", base_url="https://news.example.org", config_json=json.dumps(config))
        db.add(source); db.flush()
        task = CollectionTask(status="running", start_date=date(2026, 8, 24), source_ids_json=f"[{source.id}]",
                              source_snapshot_json="[]", keyword_config_json="[]")
        db.add(task); db.flush()
        snapshot = {"id": source.id, "name": source.name, "base_url": source.base_url, "config": config}
        assert collect_source(db, task, snapshot)[:4] == (1, 0, 0, 1)
        assert db.scalar(select(func.count(RawArticle.id))) == 1
        article = db.scalar(select(RawArticle))
        assert article.content_kind == "article"
        assert article.source_item_key == first
        assert "先进封装" in article.body
