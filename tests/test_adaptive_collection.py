from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.collection.adaptive import detect_and_validate
from app.collection.executors import CollectionExecutor
from app.collection.inspection import PageInspector
from app.collection.profiles import PageResponse
from app.collection.probing import DeterministicDetector
from app.collection.record_extractor import stable_record_key
from app.crawler import collect_source
from app.database import SessionLocal
from app.models import CollectionTask, RawArticle, Source, SourceVersion

FIXTURES = Path(__file__).parent / "fixtures" / "adaptive"
NATIONAL_OUTER = "https://new.tzxm.gov.cn/bsdt/"
NATIONAL_PAGE = "https://new.tzxm.gov.cn/tzpt/statics/html/announce/{page}.shtml"
JIANGSU = "https://tzxm.fzggw.jiangsu.gov.cn/portalopenPublicInformation.do?method=queryExamineAll"


class FixtureFetcher:
    def __init__(self, site: str):
        self.site = site
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def fetch(self, url: str, method: str = "GET", form: dict[str, str] | None = None) -> PageResponse:
        form = form or {}
        self.calls.append((method, url, form))
        if self.site == "national":
            if url == NATIONAL_OUTER:
                name = "national_outer.html"
            else:
                page = url.rsplit("/", 1)[-1].split(".", 1)[0]
                name = f"national_{page}.html"
        else:
            page = form.get("pageNo", "1")
            name = f"jiangsu_{page}.html"
        content = (FIXTURES / name).read_bytes()
        return PageResponse(
            requested_url=url, url=url, status_code=200, headers={"content-type": "text/html; charset=utf-8"},
            content=content, encoding="utf-8", robots_status="missing",
        )


def test_national_iframe_url_template_and_record_extraction():
    fetcher = FixtureFetcher("national")
    profile = detect_and_validate(fetcher, NATIONAL_OUTER, ["new.tzxm.gov.cn"])
    assert profile.entry == NATIONAL_PAGE.format(page=1)
    assert profile.pagination.kind == "url_template"
    assert profile.pagination.template == NATIONAL_PAGE.format(page="{page}")
    assert profile.fields["project_code"] == "项目代码"
    assert profile.date_order == "descending"
    assert profile.validation and profile.validation.pages_checked == 2
    assert profile.validation.field_completeness == 1

    first_page = next(CollectionExecutor(fetcher).pages(profile, max_pages=1, max_items=10))
    assert first_page.items[0].title == "先进封装生产基地项目"
    assert first_page.items[0].fields["审批结果"] == "1"
    assert "审批结果：通过" in first_page.items[0].body
    assert first_page.items[0].published_at == date(2026, 8, 24)


def test_jiangsu_post_form_and_composite_project_cell():
    fetcher = FixtureFetcher("jiangsu")
    profile = detect_and_validate(fetcher, JIANGSU, ["tzxm.fzggw.jiangsu.gov.cn"])
    assert profile.pagination.kind == "form_post"
    assert profile.pagination.page_field == "pageNo"
    assert profile.pagination.page_size == 20
    assert "projectInfo.areaDetialCode" in profile.pagination.static_fields
    assert profile.fields["title"] == "项目代码/项目名称"
    assert profile.validation and profile.validation.item_count == 4

    pages = list(CollectionExecutor(fetcher).pages(profile, max_pages=2, max_items=40))
    assert pages[0].items[0].title == "芯片制造设备生产线技改项目"
    assert pages[0].items[0].standard_fields["project_code"] == "2608-320572-89-02-416892"
    assert pages[0].items[0].fields["审批事项"] == "企业投资技术改造项目备案"
    assert pages[1].items[0].published_at == date(2026, 8, 22)
    assert fetcher.calls[-1][0] == "POST"
    assert fetcher.calls[-1][2]["pageNo"] == "2"


def test_descending_cutoff_stops_before_second_history_page():
    fetcher = FixtureFetcher("national")
    profile = detect_and_validate(fetcher, NATIONAL_OUTER, ["new.tzxm.gov.cn"])
    fetcher.calls.clear()
    pages = list(CollectionExecutor(fetcher).pages(
        profile, max_pages=100, max_items=5000, start_date=date(2026, 8, 24),
    ))
    assert len(pages) == 1
    assert all("/2.shtml" not in call[1] for call in fetcher.calls)


def test_link_and_get_form_pagination_detection():
    table = """<table><tr><th>项目名称</th><th>项目代码</th><th>审批事项</th><th>审批时间</th><th>审批结果</th></tr>
    <tr><td>测试项目</td><td>2608-320100-04-01-900001</td><td>项目备案</td><td>2026-08-24</td><td>通过</td></tr></table>"""
    inspector = PageInspector()
    detector = DeterministicDetector()
    link_response = PageResponse(
        requested_url="https://example.org/list", url="https://example.org/list", status_code=200,
        headers={}, content=(table + '<a rel="next" href="?page=2">Next</a>').encode(),
    )
    link_profile = detector.detect(
        inspector.inspect(link_response), "https://example.org/list", ["example.org"]
    ).profile
    assert link_profile and link_profile.pagination.kind == "link"
    assert link_profile.pagination.next_url == "https://example.org/list?page=2"

    form_response = PageResponse(
        requested_url="https://example.org/list", url="https://example.org/list", status_code=200,
        headers={}, content=(
            '<form method="get"><input name="page" value="1"><input name="pageSize" value="20">'
            '<input name="region" value="320000"></form>' + table
        ).encode(),
    )
    form_profile = detector.detect(
        inspector.inspect(form_response), "https://example.org/list", ["example.org"]
    ).profile
    assert form_profile and form_profile.pagination.kind == "form_get"
    assert form_profile.pagination.static_fields == {"region": "320000"}


def test_record_key_changes_with_approval_business_identity():
    base = {
        "project_code": "2608-320100-04-01-100001", "approval_item": "项目备案",
        "published_at": "2026-08-24", "approval_result": "通过",
    }
    assert stable_record_key(base) == stable_record_key(dict(base))
    assert stable_record_key(base) != stable_record_key({**base, "approval_item": "节能审查"})


def test_v2_source_api_does_not_require_selectors_or_url_pattern(client):
    response = client.post("/api/sources", json={
        "name": "零选择器公示来源", "base_url": "https://example.org", "enabled": True,
        "config": {
            "version": 2, "entry_urls": ["https://example.org/public"], "mode": "auto",
            "content_hint": "公开审批记录", "allowed_hosts": [],
            "limits": {"rate_limit_per_minute": 12, "timeout_seconds": 20, "max_pages": 2, "max_items": 40},
            "learned_profile": None,
        },
    })
    assert response.status_code == 201
    assert response.json()["config"]["version"] == 2


def test_v2_task_persists_profile_records_and_deduplicates(monkeypatch):
    fetcher = FixtureFetcher("national")
    monkeypatch.setattr("app.crawler._adaptive_fetcher", lambda *_args: fetcher)
    raw_config = {
        "version": 2, "entry_urls": [NATIONAL_OUTER], "mode": "auto",
        "allowed_hosts": ["new.tzxm.gov.cn"],
        "content_hint": "项目办理结果公示",
        "limits": {"rate_limit_per_minute": 120, "timeout_seconds": 20, "max_pages": 100, "max_items": 5000},
        "learned_profile": None,
    }
    with SessionLocal() as db:
        source = Source(name="全国公示测试", base_url="https://new.tzxm.gov.cn", config_json=json.dumps(raw_config))
        db.add(source); db.flush()
        task = CollectionTask(status="running", start_date=date(2026, 8, 24), source_ids_json=f"[{source.id}]",
                              source_snapshot_json="[]", keyword_config_json="[]")
        db.add(task); db.flush()
        snapshot = {"id": source.id, "name": source.name, "base_url": source.base_url, "config": raw_config}
        first = collect_source(db, task, snapshot)
        assert first == (1, 0, 0, 2, 1, 0)
        assert db.scalar(select(func.count(RawArticle.id))) == 1
        article = db.scalar(select(RawArticle))
        assert article.content_kind == "table_record"
        assert json.loads(article.raw_payload_json)["项目代码"] == "2608-320100-04-01-100001"
        assert db.scalar(select(func.count(SourceVersion.id)).where(SourceVersion.source_id == source.id)) == 1

        second_task = CollectionTask(status="running", start_date=date(2026, 8, 24), source_ids_json=f"[{source.id}]",
                                     source_snapshot_json="[]", keyword_config_json="[]")
        db.add(second_task); db.flush()
        second = collect_source(db, second_task, snapshot)
        assert second == (0, 1, 0, 2, 1, 0)
        assert db.scalar(select(func.count(RawArticle.id))) == 1
