import json
from datetime import date

import httpx

from app.database import SessionLocal
from app.llm import (
    _call,
    _normalize_region_and_organization,
    plan_search_queries,
    review_search_results,
    structure_article,
)


def test_search_review_returns_valid_unique_indexes(monkeypatch):
    monkeypatch.setattr(
        "app.llm._call",
        lambda setting, messages: '{"keep": [2, 0, 2, 99]}',
    )
    setting = ModelSetting(base_url="https://api.example.com", model_name="test", api_key="x")
    results = [{"index": index, "title": str(index)} for index in range(3)]

    assert review_search_results(setting, "芯片项目", results) == [2, 0]
from app.models import ModelSetting, RawArticle, Source


def test_region_is_normalized_and_fine_grained_location_is_preserved():
    region, organization = _normalize_region_and_organization(
        "常州市钟楼区", "", "常州市钟楼区某半导体项目签约"
    )
    assert region == "中国大陆-华东"
    assert organization == "常州市钟楼区"


def test_region_separator_is_normalized():
    region, organization = _normalize_region_and_organization("中国大陆/华南", "某大学", "")
    assert region == "中国大陆-华南"
    assert organization == "某大学"


def test_company_name_is_not_kept_as_development_zone_or_school():
    region, organization = _normalize_region_and_organization(
        "中国大陆-西南", "成都某某半导体有限公司", "该公司发布了新产品。"
    )
    assert region == "中国大陆-西南"
    assert organization == ""


def test_explicit_development_zone_attribution_replaces_company_name():
    region, organization = _normalize_region_and_organization(
        "中国大陆", "某某科技有限公司", "该项目正式落户成都成华经开区，计划年内开工。"
    )
    assert region == "中国大陆-西南"
    assert organization == "成都成华经开区"


def test_official_zone_name_and_alias_are_preserved():
    region, organization = _normalize_region_and_organization(
        "北京", "", "项目位于北京经济技术开发区（亦庄），建设先进封装产线。"
    )
    assert region == "中国大陆-华北"
    assert organization == "北京经济技术开发区（亦庄）"


def test_short_place_name_is_preserved_without_alias_expansion():
    region, organization = _normalize_region_and_organization(
        "中国大陆-华北", "亦庄", "该产线落户亦庄，预计明年投产。"
    )
    assert region == "中国大陆-华北"
    assert organization == "亦庄"


def test_development_zone_takes_priority_over_school_in_same_article():
    region, organization = _normalize_region_and_organization(
        "中国大陆-西南",
        "四川大学",
        "项目依托四川大学的技术成果，正式落户成都成华经开区。",
    )
    assert region == "中国大陆-西南"
    assert organization == "成都成华经开区"


def test_sentence_like_organization_is_replaced_with_noun_phrase_from_context():
    region, organization = _normalize_region_and_organization(
        "北京", "项目位于北京经济技术开发区", "项目位于北京经济技术开发区。"
    )
    assert region == "中国大陆-华北"
    assert organization == "北京经济技术开发区"


def test_ecocity_takes_priority_over_sentence_like_school_source():
    context = (
        "近日，中新天津生态城重点产业项目建设全面提速。"
        "天津芯擎科技技术主要来源于清华大学天津电子信息研究院孵化企业。"
    )
    region, organization = _normalize_region_and_organization(
        "中国大陆-华北", "天津芯擎科技技术主要来源于清华大学", context
    )
    assert region == "中国大陆-华北"
    assert organization == "中新天津生态城"


def test_luowei_extracts_official_zone_name_and_alias():
    region, organization = _normalize_region_and_organization(
        "北京", "", "项目落位北京经济技术开发区（亦庄），计划年内开工。"
    )
    assert region == "中国大陆-华北"
    assert organization == "北京经济技术开发区（亦庄）"


def test_model_request_has_no_search_provider_fields(monkeypatch, capsys):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"items": []}'}, "finish_reason": "stop"}]}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.llm.httpx.post", fake_post)
    setting = ModelSetting(
        base_url="https://api.example.com/v1",
        model_name="test-model",
        api_key="test-key",
        request_headers_json=json.dumps(
            [
                {"key": "X-API-Version", "value": "2026-08-01"},
                {"key": "authorization", "value": "Custom test-authorization"},
            ]
        ),
    )
    assert _call(setting, [{"role": "user", "content": "test"}]) == '{"items": []}'
    assert captured["url"].endswith("/v1/chat/completions")
    assert set(captured["json"]) == {
        "model", "messages", "temperature", "response_format", "max_tokens",
    }
    assert captured["json"]["model"] == "test-model"
    assert captured["json"]["max_tokens"] == 8192
    assert captured["headers"] == {
        "X-API-Version": "2026-08-01",
        "authorization": "Custom test-authorization",
    }
    assert captured["timeout"].connect == 10
    assert captured["timeout"].read == 240
    assert captured["timeout"].write == 30
    assert captured["timeout"].pool == 10
    debug_output = capsys.readouterr().out
    assert "LLM REQUEST" in debug_output
    assert "LLM RESPONSE" in debug_output
    assert "LLM FINAL CONTENT" in debug_output
    assert "test-model" in debug_output
    assert "test-key" not in debug_output


def test_model_response_accepts_data_wrapped_chat_completion(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "choices": [
                        {
                            "message": {
                                "content": '{"queries": ["江苏 半导体 项目"]}'
                            },
                            "finish_reason": "stop",
                        }
                    ]
                },
                "success": True,
            }

    monkeypatch.setattr("app.llm.httpx.post", lambda *args, **kwargs: Response())
    setting = ModelSetting(
        base_url="https://api.example.com/v1",
        model_name="wrapped-model",
        api_key="test-key",
    )

    assert _call(setting, [{"role": "user", "content": "test"}]) == (
        '{"queries": ["江苏 半导体 项目"]}'
    )


def test_search_planner_returns_clean_deduplicated_queries(monkeypatch):
    monkeypatch.setattr(
        "app.llm._call",
        lambda setting, messages: json.dumps(
            {
                "queries": [
                    "  先进封装   项目 开工  ",
                    "Chiplet 扩产",
                    "先进封装 项目 开工",
                ]
            },
            ensure_ascii=False,
        ),
    )
    setting = ModelSetting(
        base_url="https://api.example.com",
        model_name="test-model",
        api_key="secret",
    )

    queries = plan_search_queries(
        setting,
        "检索先进封装与 Chiplet 项目动态",
        start_date=date(2026, 8, 20),
    )

    assert queries == ["先进封装 项目 开工", "Chiplet 扩产"]


def test_deepseek_v4_request_uses_low_reasoning_effort(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"records": []}'}, "finish_reason": "stop"}]}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.llm.httpx.post", fake_post)
    setting = ModelSetting(
        base_url="https://api.deepseek.com",
        model_name="deepseek-v4-flash",
        api_key="test-key",
    )

    assert _call(setting, [{"role": "user", "content": "test"}]) == '{"records": []}'
    assert captured["json"]["max_tokens"] == 8192
    assert captured["json"]["reasoning_effort"] == "low"


def test_llm_retries_invalid_output_and_marks_low_confidence(monkeypatch):
    outputs = iter(["not json", json.dumps({"records": [{"region": "中国大陆/华东", "company_name": "示例半导体",
        "event_date": "2026-08-19", "info_type": "项目签约", "investment_amount": {"original": "10亿元", "value": "10", "currency": "CNY", "unit": "亿元"},
        "project_name": "先进封装项目", "details": "项目在华东签约。", "evidence": {"company_name": "示例半导体签约"}, "confidence": {"company_name": 0.5}}]}, ensure_ascii=False)])
    monkeypatch.setattr("app.llm._call", lambda setting, messages: next(outputs))
    with SessionLocal() as db:
        source = Source(name="LLM测试", base_url="https://example.com", enabled=True, builtin=False, config_json="{}")
        db.add(source); db.flush()
        article = RawArticle(source_id=source.id, canonical_url="https://example.com/a", title="签约", published_at=date(2026, 8, 19),
            body="示例半导体签约先进封装项目，投资10亿元。" * 5, content_hash="a" * 64, status="pending")
        db.add(article); db.commit(); db.refresh(article)
        count = structure_article(db, article, ModelSetting(base_url="https://api.example.com", model_name="mock", api_key="x", enabled=True))
        db.commit()
        assert count == 1
        assert article.status == "completed"
        record = db.query(__import__("app.models", fromlist=["StructuredRecord"]).StructuredRecord).one()
        assert record.info_type == "项目签约"
        assert record.status == "review_required"
        assert record.amount_currency == "CNY"


def test_llm_keeps_only_one_record_per_info_type(monkeypatch):
    records = [
        {
            "info_type": "项目签约",
            "project_name": "第一个项目",
            "details": "第一条签约信息。",
        },
        {
            "info_type": "项目签约",
            "project_name": "第二个项目",
            "details": "第二条签约信息。",
        },
    ]
    monkeypatch.setattr(
        "app.llm._call",
        lambda setting, messages: json.dumps({"records": records}, ensure_ascii=False),
    )
    with SessionLocal() as db:
        source = Source(name="去重测试", base_url="https://dedupe.example.com", enabled=True, builtin=False, config_json="{}")
        db.add(source)
        db.flush()
        article = RawArticle(
            source_id=source.id,
            canonical_url="https://dedupe.example.com/a",
            title="多项目签约",
            body="资料包含两条项目签约信息。" * 5,
            content_hash="d" * 64,
            status="pending",
        )
        db.add(article)
        db.commit()
        db.refresh(article)

        count = structure_article(
            db,
            article,
            ModelSetting(base_url="https://api.example.com", model_name="mock", api_key="x", enabled=True),
        )
        db.commit()

        stored = db.query(__import__("app.models", fromlist=["StructuredRecord"]).StructuredRecord).all()
        assert count == 1
        assert len(stored) == 1
        assert stored[0].info_type == "项目签约"
        assert stored[0].project_name == "第一个项目"


def test_llm_emits_one_record_and_uses_info_type_priority(monkeypatch):
    records = [
        {
            "info_type": "建设开工",
            "project_name": "封装产线",
            "details": "封装产线已开工建设。",
        },
        {
            "info_type": "项目立项",
            "project_name": "封装产线",
            "details": "封装产线完成项目立项。",
        },
        {
            "info_type": "项目规划",
            "project_name": "封装产线",
            "details": "封装产线纳入项目规划。",
        },
    ]
    monkeypatch.setattr(
        "app.llm._call",
        lambda setting, messages: json.dumps({"records": records}, ensure_ascii=False),
    )
    with SessionLocal() as db:
        source = Source(name="单条记录测试", base_url="https://one.example.com", enabled=True, builtin=False, config_json="{}")
        db.add(source)
        db.flush()
        article = RawArticle(
            source_id=source.id,
            canonical_url="https://one.example.com/a",
            title="项目规划、立项并开工",
            body="项目已纳入规划、完成立项并开工建设。" * 5,
            content_hash="o" * 64,
            status="pending",
        )
        db.add(article)
        db.commit()
        db.refresh(article)

        count = structure_article(
            db,
            article,
            ModelSetting(base_url="https://api.example.com", model_name="mock", api_key="x", enabled=True),
        )
        db.commit()

        stored = db.query(__import__("app.models", fromlist=["StructuredRecord"]).StructuredRecord).all()
        assert count == 1
        assert len(stored) == 1
        assert stored[0].info_type == "项目规划"
        assert "项目立项：封装产线完成项目立项。" in stored[0].details
        assert "建设开工：封装产线已开工建设。" in stored[0].details


def test_llm_empty_output_has_readable_error_after_retry(monkeypatch):
    monkeypatch.setattr("app.llm._call", lambda setting, messages: "")
    with SessionLocal() as db:
        source = Source(name="空响应测试", base_url="https://empty.example.com", enabled=True, builtin=False, config_json="{}")
        db.add(source); db.flush()
        article = RawArticle(source_id=source.id, canonical_url="https://empty.example.com/a", title="待结构化文章",
            body="某半导体企业公布了一项具体产业进展。" * 5, content_hash="e" * 64, status="pending")
        db.add(article); db.commit(); db.refresh(article)
        count = structure_article(db, article, ModelSetting(base_url="https://api.example.com", model_name="mock", api_key="x", enabled=True))
        assert count == 0
        assert article.status == "review_required"
        assert article.error_message == "模型返回了空内容，请检查模型名称、API 额度及接口兼容性（已自动重试）"
        assert "validation error" not in article.error_message


def test_llm_timeout_is_not_retried(monkeypatch):
    calls = 0

    def time_out(setting, messages):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("app.llm._call", time_out)
    with SessionLocal() as db:
        source = Source(name="超时测试", base_url="https://timeout.example.com", enabled=True, builtin=False, config_json="{}")
        db.add(source); db.flush()
        article = RawArticle(source_id=source.id, canonical_url="https://timeout.example.com/a", title="待结构化文章",
            body="某半导体企业公布了一项具体产业进展。" * 5, content_hash="t" * 64, status="pending")
        db.add(article); db.commit(); db.refresh(article)

        count = structure_article(db, article, ModelSetting(base_url="https://api.example.com", model_name="mock", api_key="x", enabled=True))

        assert count == 0
        assert calls == 1
        assert article.status == "review_required"
        assert article.error_message == "模型请求超时，请稍后重试"
