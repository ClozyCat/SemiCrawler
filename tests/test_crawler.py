from datetime import date
import json

from sqlalchemy import func, select

from app.crawler import collect_source, discover_listing, is_low_value_event_promotion, keyword_values, parse_article
from app.database import SessionLocal
from app.models import CollectionTask, RawArticle, Source
from app.source_config import SourceConfig


def config(**selector_overrides):
    selectors = {"list_links": ".items a", "title": "h1", "date": ".info", "content": ".content"}
    selectors.update(selector_overrides)
    return SourceConfig.model_validate({"entry_urls": ["https://example.com/news"],
        "article_url_pattern": "/news/\\d{8}-\\d+\\.html$", "selectors": selectors,
        "pagination": {"next_page_selector": "a.next", "max_pages": 2}})


def test_listing_filters_categories_and_finds_next_page():
    html = """<div class='items'><a href='/news/memory/'>分类</a><a href='/news/20260819-12.html'>文章</a></div><a class='next' href='/news?page=2'>下一页</a>"""
    links, next_url = discover_listing(html, "https://example.com/news", config())
    assert links == ["https://example.com/news/20260819-12.html"]
    assert next_url == "https://example.com/news?page=2"


def test_article_css_parsing_and_cleanup():
    html = """<h1>芯片项目签约</h1><div class='info'>发布于 2026年08月19日</div><div class='content'><p>项目正式签约，计划建设先进封装生产线，投资金额为十亿元。</p><script>bad()</script><p>项目将于年内开工并形成量产能力，建成后服务高性能计算与汽车电子客户。</p></div>"""
    article = parse_article(html, "https://example.com/news/20260819-12.html", config())
    assert article["published_at"] == date(2026, 8, 19)
    assert "bad" not in article["body"]
    assert "先进封装" in article["body"]


def test_collection_counts_only_persisted_articles_as_fetched(monkeypatch):
    listing = """<div class='items'><a href='/news/20260819-1.html'>旧文</a><a href='/news/20260821-2.html'>新文</a></div>"""
    article_template = """<h1>{title}</h1><div class='info'>发布于 {published}</div><div class='content'><p>这是用于验证采集统计口径的半导体项目正文，项目计划建设先进封装生产线并引入多套生产设备，建成后将面向汽车电子和高性能计算客户提供长期稳定的芯片制造服务。</p></div>"""
    pages = {
        "https://example.com/news": listing,
        "https://example.com/news/20260819-1.html": article_template.format(title="旧项目", published="2026年08月19日"),
        "https://example.com/news/20260821-2.html": article_template.format(title="新项目", published="2026年08月21日"),
    }
    monkeypatch.setattr("app.crawler.fetch_html", lambda url, timeout=20: pages[url])
    monkeypatch.setattr("app.crawler.time.sleep", lambda _: None)
    raw_config = {
        "entry_urls": ["https://example.com/news"], "article_url_pattern": "/news/\\d{8}-\\d+\\.html$",
        "selectors": {"list_links": ".items a", "title": "h1", "date": ".info", "content": ".content"},
        "pagination": {"max_pages": 1},
    }

    with SessionLocal() as db:
        source = Source(name="统计测试来源", base_url="https://example.com", config_json=json.dumps(raw_config))
        db.add(source); db.flush()
        task = CollectionTask(status="running", start_date=date(2026, 8, 20), source_ids_json=f"[{source.id}]",
            source_snapshot_json="[]", started_at=None, completed_at=None)
        db.add(task); db.flush()
        result = collect_source(db, task, {"id": source.id, "name": source.name,
            "base_url": source.base_url, "config": raw_config})
        assert result == (1, 0, 0, 2, 1, 0)
        assert db.scalar(select(func.count(RawArticle.id)).where(RawArticle.task_id == task.id)) == 1


def test_keyword_values_use_cell_values_and_ignore_column_names():
    result = keyword_values([{"industry": "新型显示", "field": "Micro LED", "keywords": "AR设备、VR设备"}])
    assert result == ["新型显示", "micro led", "ar设备", "vr设备"]
    assert "industry" not in result


def test_event_preview_without_substantive_news_is_filtered():
    body = """听百家言、观行业风！答案尽在 CSEAC 同期论坛中。2026年8月31日至9月2日，
    第十四届半导体设备材料及核心部件展将在无锡开幕。展会规模7万平方米，联动1400+海内外展商，
    同期20+专业论坛、多场圆桌对话、新品发布，校企专区120+产业链企业集中亮相。
    论坛议题包括先进封装量产难点、三维闪存工艺设备协同创新。"""
    assert is_low_value_event_promotion("CSEAC 2026 即将开幕", body) is True


def test_event_report_with_concrete_industry_action_is_kept():
    body = "论坛现场，甲公司与无锡高新区正式签约先进封装项目，投资总额20亿元，项目计划年内开工。"
    assert is_low_value_event_promotion("半导体产业论坛在无锡召开", body) is False
