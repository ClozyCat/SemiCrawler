import json
from datetime import date

from app.database import SessionLocal
from app.llm import _normalize_region_and_organization, structure_article


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
from app.models import ModelSetting, RawArticle, Source


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
