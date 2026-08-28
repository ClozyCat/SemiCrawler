import json
from datetime import date

from sqlalchemy import func, select

from app.crawler import (
    collect_source,
    discover_listing,
    is_low_value_event_promotion,
    keyword_values,
    merge_ranked_search_results,
    parse_article,
    run_task,
)
from app.database import SessionLocal
from app.dokobot import DokobotError, DokobotPage, DokobotSearchItem
from app.models import CollectionTask, ModelSetting, RawArticle, Source, TaskLog
from app.source_config import SourceConfig


def config(**selector_overrides):
    selectors = {
        "list_links": ".items a",
        "title": "h1",
        "date": ".info",
        "content": ".content",
    }
    selectors.update(selector_overrides)
    return SourceConfig.model_validate(
        {
            "entry_urls": ["https://example.com/news"],
            "article_url_pattern": "/news/\\d{8}-\\d+\\.html$",
            "selectors": selectors,
            "pagination": {"next_page_selector": "a.next", "max_pages": 2},
        }
    )


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
        "https://example.com/news/20260819-1.html": article_template.format(
            title="旧项目", published="2026年08月19日"
        ),
        "https://example.com/news/20260821-2.html": article_template.format(
            title="新项目", published="2026年08月21日"
        ),
    }
    monkeypatch.setattr("app.crawler.fetch_html", lambda url, timeout=20: pages[url])
    monkeypatch.setattr("app.crawler.time.sleep", lambda _: None)
    raw_config = {
        "entry_urls": ["https://example.com/news"],
        "article_url_pattern": "/news/\\d{8}-\\d+\\.html$",
        "selectors": {
            "list_links": ".items a",
            "title": "h1",
            "date": ".info",
            "content": ".content",
        },
        "pagination": {"max_pages": 1},
    }

    with SessionLocal() as db:
        source = Source(
            name="统计测试来源",
            base_url="https://example.com",
            config_json=json.dumps(raw_config),
        )
        db.add(source)
        db.flush()
        task = CollectionTask(
            status="running",
            start_date=date(2026, 8, 20),
            source_ids_json=f"[{source.id}]",
            source_snapshot_json="[]",
            started_at=None,
            completed_at=None,
        )
        db.add(task)
        db.flush()
        result = collect_source(
            db,
            task,
            {
                "id": source.id,
                "name": source.name,
                "base_url": source.base_url,
                "config": raw_config,
            },
        )
        assert result == (1, 0, 0, 2, 1, 0)
        assert task.fetched_count == 1
        assert task.deduplicated_count == 0
        assert task.failed_count == 0
        assert (
            db.scalar(
                select(func.count(RawArticle.id)).where(RawArticle.task_id == task.id)
            )
            == 1
        )
        stored = db.scalar(select(RawArticle).where(RawArticle.task_id == task.id))
        assert stored.source_item_key == stored.canonical_url
        assert stored.content_kind == "article"
        assert stored.raw_payload_json == "{}"


def test_web_search_uses_dokobot_pages_then_structures_them(monkeypatch):
    class FakeDokobotClient:
        def select_search_engine(self):
            return "google"

        def search(self, query, *, num):
            assert "after:2026-08-20" in query
            assert num == 10
            return [
                DokobotSearchItem(
                    title="搜索结果标题", link="https://news.example.com/project"
                )
            ]

        def read(self, url):
            assert url == "https://news.example.com/project"
            return DokobotPage(
                title="先进封装项目开工",
                url=url,
                text="2026年8月21日，某公司先进封装项目正式开工，计划建设多条生产线。"
                * 5,
            )

    captured = {}

    def fake_structure(db, article, setting, *, source_name=None):
        captured.update(
            article=article, source_name=source_name, model=setting.model_name
        )
        article.status = "completed"
        article.model_name = setting.model_name
        return 1

    monkeypatch.setattr("app.crawler.DokobotClient", FakeDokobotClient)
    monkeypatch.setattr(
        "app.crawler.plan_search_queries",
        lambda setting, topic, **kwargs: [topic],
    )
    monkeypatch.setattr("app.crawler.structure_article", fake_structure)
    raw_config = {
        "type": "web_search",
        "query": "先进封装开工",
        "source_hint": "",
        "max_results": 10,
    }

    with SessionLocal() as db:
        source = Source(
            name="Dokobot测试",
            base_url="https://dokobot.ai",
            config_json=json.dumps(raw_config),
        )
        db.add(source)
        db.add(
            ModelSetting(
                id=1,
                base_url="https://api.example.com",
                model_name="test-model",
                api_key="secret",
            )
        )
        db.flush()
        task = CollectionTask(
            status="running",
            start_date=date(2026, 8, 20),
            source_ids_json=f"[{source.id}]",
            source_snapshot_json="[]",
            keyword_config_json="[]",
        )
        db.add(task)
        db.flush()

        result = collect_source(
            db,
            task,
            {
                "id": source.id,
                "name": source.name,
                "base_url": source.base_url,
                "config": raw_config,
            },
        )

        assert result == (1, 0, 0, 1, 0, 0)
        assert captured["article"].body.startswith("2026年8月21日")
        assert captured["source_name"] == "news.example.com"
        assert captured["model"] == "test-model"


def test_web_search_executes_five_planned_queries_plus_original_and_merges_results(
    monkeypatch,
):
    searched = []

    class FakeDokobotClient:
        def select_search_engine(self):
            return "google"

        def search(self, query, *, num):
            searched.append(query)
            suffix = "shared" if len(searched) == 1 else "second"
            return [
                DokobotSearchItem(
                    title=f"结果 {suffix}",
                    link=f"https://news.example.com/{suffix}",
                ),
                DokobotSearchItem(
                    title="公共结果",
                    link="https://news.example.com/shared",
                ),
            ]

        def read(self, url):
            return DokobotPage(
                title="先进封装项目正式开工",
                url=url,
                text=(f"2026年8月21日，{url} 对应的先进封装项目正式开工并建设生产线。" * 5),
            )

    monkeypatch.setattr("app.crawler.DokobotClient", FakeDokobotClient)
    monkeypatch.setattr(
        "app.crawler.plan_search_queries",
        lambda setting, topic, **kwargs: [
            "先进封装 开工",
            "Chiplet 扩产",
            "半导体 项目 签约",
            "先进封装 项目 投产",
            "Chiplet 项目 建设",
        ],
    )
    monkeypatch.setattr(
        "app.crawler.structure_article",
        lambda db, article, setting, **kwargs: 1,
    )
    raw_config = {
        "type": "web_search",
        "query": "先进封装和 Chiplet 项目动态",
        "source_hint": "",
        "max_results": 10,
    }

    with SessionLocal() as db:
        source = Source(
            name="多查询测试",
            base_url="https://dokobot.ai",
            config_json=json.dumps(raw_config),
        )
        db.add(source)
        db.add(
            ModelSetting(
                id=1,
                base_url="https://api.example.com",
                model_name="test-model",
                api_key="secret",
            )
        )
        db.flush()
        task = CollectionTask(
            status="running",
            start_date=date(2026, 8, 20),
            source_ids_json=f"[{source.id}]",
            source_snapshot_json="[]",
            keyword_config_json="[]",
        )
        db.add(task)
        db.flush()

        result = collect_source(
            db,
            task,
            {
                "id": source.id,
                "name": source.name,
                "base_url": source.base_url,
                "config": raw_config,
            },
        )

        assert result == (2, 0, 0, 2, 0, 0)
        assert len(searched) == 6
        assert searched[-1] == "先进封装和 Chiplet 项目动态 after:2026-08-20"
        assert all("after:2026-08-20" in query for query in searched)
        logs = db.scalars(select(TaskLog).where(TaskLog.task_id == task.id)).all()
        assert any(
            "本次将执行 6 条搜索查询（5 条 LLM 规划查询 + 1 条原始查询）"
            in log.message
            for log in logs
        )


def test_web_search_groups_source_urls_per_query_without_total_limit(
    monkeypatch,
):
    searched = []

    class FakeDokobotClient:
        def select_search_engine(self):
            return "bing"

        def search(self, query, *, num):
            searched.append((query, num))
            return [
                DokobotSearchItem(
                    title=query,
                    link=f"https://news.example.com/{len(searched)}",
                )
            ]

        def read(self, url):
            return DokobotPage(
                title="项目动态",
                url=url,
                text="2026年8月21日，先进封装项目正式开工并建设生产线。" * 5,
            )

    monkeypatch.setattr("app.crawler.DokobotClient", FakeDokobotClient)
    monkeypatch.setattr(
        "app.crawler.plan_search_queries",
        lambda setting, topic, **kwargs: ["关键词一", "关键词二"],
    )
    monkeypatch.setattr(
        "app.crawler.structure_article",
        lambda db, article, setting, **kwargs: 1,
    )
    raw_config = {
        "type": "web_search",
        "query": "原始关键词",
        "source_hint": "https://one.example/news\nhttps://two.example/projects",
        "max_results": 100,
    }

    with SessionLocal() as db:
        source = Source(
            name="逐网址测试",
            base_url="https://dokobot.ai",
            config_json=json.dumps(raw_config),
        )
        db.add(source)
        db.add(
            ModelSetting(
                id=1,
                base_url="https://api.example.com",
                model_name="test-model",
                api_key="secret",
            )
        )
        db.flush()
        task = CollectionTask(
            status="running",
            start_date=date(2026, 8, 20),
            source_ids_json=f"[{source.id}]",
            source_snapshot_json="[]",
            keyword_config_json="[]",
        )
        db.add(task)
        db.flush()

        result = collect_source(
            db,
            task,
            {
                "id": source.id,
                "name": source.name,
                "base_url": source.base_url,
                "config": raw_config,
            },
        )

    assert len(searched) == 3
    assert all(num == 100 for _, num in searched)
    assert all("site:one.example OR site:two.example" in query for query, _ in searched)
    assert result[3] == 3
    assert result[0] == 1
    assert result[1] == 2


def test_merge_ranked_search_results_round_robins_and_deduplicates():
    def item(path):
        return DokobotSearchItem(title=path, link=f"https://example.com/{path}")

    shared = item("shared")
    merged = merge_ranked_search_results(
        [[item("a1"), shared, item("a3")], [item("b1"), shared, item("b3")]],
        4,
    )

    assert [entry.title for entry in merged] == ["a1", "b1", "shared", "a3"]


def test_web_search_falls_back_to_original_query_when_planning_fails(monkeypatch):
    searched = []

    class FakeDokobotClient:
        def select_search_engine(self):
            return "google"

        def search(self, query, *, num):
            searched.append(query)
            return []

    def fail_planning(setting, topic, **kwargs):
        raise ValueError("invalid plan")

    monkeypatch.setattr("app.crawler.DokobotClient", FakeDokobotClient)
    monkeypatch.setattr("app.crawler.plan_search_queries", fail_planning)
    raw_config = {
        "type": "web_search",
        "query": "先进封装开工",
        "source_hint": "",
        "max_results": 10,
    }

    with SessionLocal() as db:
        source = Source(
            name="规划回退测试",
            base_url="https://dokobot.ai",
            config_json=json.dumps(raw_config),
        )
        db.add(source)
        db.add(
            ModelSetting(
                id=1,
                base_url="https://api.example.com",
                model_name="test-model",
                api_key="secret",
            )
        )
        db.flush()
        task = CollectionTask(
            status="running",
            start_date=date(2026, 8, 20),
            source_ids_json=f"[{source.id}]",
            source_snapshot_json="[]",
            keyword_config_json="[]",
        )
        db.add(task)
        db.flush()

        result = collect_source(
            db,
            task,
            {
                "id": source.id,
                "name": source.name,
                "base_url": source.base_url,
                "config": raw_config,
            },
        )

        assert result == (0, 0, 0, 0, 0, 0)
        assert searched == ["先进封装开工 after:2026-08-20"]
        logs = db.scalars(select(TaskLog).where(TaskLog.task_id == task.id)).all()
        assert any("已回退到原始查询" in log.message for log in logs)


def test_web_search_skips_planning_and_search_when_engines_are_unreachable(
    monkeypatch,
):
    calls = {"plan": 0, "search": 0}

    class FakeDokobotClient:
        def select_search_engine(self):
            raise DokobotError("Google 和 Bing 均无法连接，已跳过联网搜索任务")

        def search(self, query, *, num):
            calls["search"] += 1
            return []

    def fake_plan(*args, **kwargs):
        calls["plan"] += 1
        return ["不应执行"]

    monkeypatch.setattr("app.crawler.DokobotClient", FakeDokobotClient)
    monkeypatch.setattr("app.crawler.plan_search_queries", fake_plan)
    raw_config = {
        "type": "web_search",
        "query": "先进封装开工",
        "source_hint": "",
        "max_results": 10,
    }

    with SessionLocal() as db:
        source = Source(
            name="连通性失败测试",
            base_url="https://dokobot.ai",
            config_json=json.dumps(raw_config),
        )
        db.add(source)
        db.add(
            ModelSetting(
                id=1,
                base_url="https://api.example.com",
                model_name="test-model",
                api_key="secret",
            )
        )
        db.flush()
        task = CollectionTask(
            status="running",
            start_date=date(2026, 8, 20),
            source_ids_json=f"[{source.id}]",
            source_snapshot_json="[]",
            keyword_config_json="[]",
        )
        db.add(task)
        db.flush()

        try:
            collect_source(
                db,
                task,
                {
                    "id": source.id,
                    "name": source.name,
                    "base_url": source.base_url,
                    "config": raw_config,
                },
            )
        except DokobotError as exc:
            assert "已跳过联网搜索任务" in str(exc)
        else:
            raise AssertionError("expected connectivity error")

    assert calls == {"plan": 0, "search": 0}


def test_keyword_values_use_cell_values_and_ignore_column_names():
    result = keyword_values(
        [{"industry": "新型显示", "field": "Micro LED", "keywords": "AR设备、VR设备"}]
    )
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


def test_run_task_honors_termination_requested_during_collection(monkeypatch):
    snapshot = {
        "id": 1,
        "name": "终止测试来源",
        "base_url": "https://example.com",
        "config": {},
    }
    with SessionLocal() as db:
        task = CollectionTask(
            status="queued",
            start_date=date(2026, 8, 20),
            source_ids_json="[1]",
            source_snapshot_json=json.dumps([snapshot]),
        )
        db.add(task)
        db.commit()
        task_id = task.id

        def request_termination(*_):
            with SessionLocal() as other_db:
                other_task = other_db.get(CollectionTask, task_id)
                other_task.status = "terminating"
                other_db.commit()
            return 0, 0, 0, 0, 0, 0

        monkeypatch.setattr("app.crawler.collect_source", request_termination)
        run_task(db, task)

        db.refresh(task)
        assert task.status == "terminated"
        assert task.completed_at is not None
        logs = db.scalars(select(TaskLog).where(TaskLog.task_id == task_id)).all()
        assert logs[-1].message == "任务已终止"
