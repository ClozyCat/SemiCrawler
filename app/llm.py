from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .constants import INFO_TYPES, REGION_OPTIONS
from .models import (
    CollectionTask,
    ModelSetting,
    RawArticle,
    Source,
    StructuredRecord,
    TaskLog,
)


class Amount(BaseModel):
    original: str = "未披露"
    value: str | None = None
    currency: str | None = None
    unit: str | None = None
    note: str | None = None


class ExtractedRecord(BaseModel):
    region: str = ""
    organization: str = ""
    company_name: str = ""
    event_date: date | None = None
    info_type: str
    investment_amount: Amount = Field(default_factory=Amount)
    project_name: str = ""
    details: str
    evidence: dict[str, str] = Field(default_factory=dict)
    confidence: dict[str, float] = Field(default_factory=dict)

    @field_validator("info_type")
    @classmethod
    def valid_type(cls, value: str) -> str:
        if value not in INFO_TYPES:
            raise ValueError("未知资讯类型")
        return value

    @field_validator("confidence")
    @classmethod
    def valid_confidence(cls, value: dict[str, float]) -> dict[str, float]:
        if any(score < 0 or score > 1 for score in value.values()):
            raise ValueError("置信度必须在 0 到 1 之间")
        return value


class Extraction(BaseModel):
    records: list[ExtractedRecord]


class SearchPlan(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=5)

    @field_validator("queries")
    @classmethod
    def clean_queries(cls, values: list[str]) -> list[str]:
        queries = [" ".join(value.split()) for value in values if value.strip()]
        queries = list(dict.fromkeys(queries))
        if not queries:
            raise ValueError("至少需要一条搜索查询")
        if any(len(query) > 300 for query in queries):
            raise ValueError("搜索查询不能超过 300 个字符")
        return queries


class ModelOutputError(ValueError):
    pass


MAX_OUTPUT_TOKENS = 4096
MODEL_TIMEOUT = httpx.Timeout(connect=10, read=240, write=30, pool=10)


SYSTEM_PROMPT = """你是半导体新闻事实抽取器。仅依据原文，不推测。每篇原文最终只能产出一条结构化记录；如果原文同时包含多种资讯类型，也只能输出一条记录，并从给定资讯类型列表中选择优先级最高的一种作为 `info_type`。其余资讯类型的事实要点必须简明概括并合并到这条记录的 `details` 中，不得拆分为多条。无结构化价值则 records 为空。
资讯类型只能从给定枚举选择。地域只能从以下枚举中选择，并且必须使用完整名称：{regions}。
“开发区/院校”只能填写项目或企业明确归属的具体地方、园区、学校或科研机构，例如省、市、区县、开发区、经开区、高新区、产业园、生态城、大学、学院、研究院、实验室。如果同一篇资料同时出现开发区/园区（包括经开区、高新区、产业园、生态城等）和院校/科研机构，该字段只填开发区/园区，不填院校。字段值必须是名词或专有名词短语，不得填写句子、动作或描述性短语；例如“天津芯擎科技技术主要来源于清华大学”不是合法字段值。企业、集团及其简称不是开发区/院校，必须填写到“企业名称”，严禁将公司名填入“开发区/院校”。
中国大陆的具体地址不能直接写入地域：地域填对应的“中国大陆-大区”，具体地点填入“开发区/院校”。原文有明确的落户、位于、入驻、选址等归属关系时必须提取对应地点；例如“成都成华经开区”应填入开发区/院校并将地域填为“中国大陆-西南”，“北京经济技术开发区（亦庄）”应完整保留官方名称和括号内别名并将地域填为“中国大陆-华北”。没有明确具体地点时该字段为空。
每个非空字段给出原文证据和 0-1 置信度。金额保留原文，并拆分 value/currency/unit/note。只输出 JSON。""".format(regions="、".join(REGION_OPTIONS))

_MAINLAND_REGION_KEYWORDS = {
    "华北": ("北京", "天津", "河北", "山西", "内蒙古"),
    "东北": ("辽宁", "沈阳", "大连", "吉林", "长春", "黑龙江", "哈尔滨"),
    "华东": ("上海", "江苏", "南京", "苏州", "无锡", "常州", "南通", "徐州", "扬州", "镇江", "泰州", "宿迁",
             "浙江", "杭州", "宁波", "安徽", "合肥", "福建", "福州", "厦门", "江西", "南昌", "山东", "济南", "青岛"),
    "华中": ("河南", "郑州", "湖北", "武汉", "湖南", "长沙"),
    "华南": ("广东", "广州", "深圳", "东莞", "佛山", "珠海", "广西", "南宁", "海南", "海口"),
    "西南": ("重庆", "四川", "成都", "贵州", "贵阳", "云南", "昆明", "西藏", "拉萨"),
    "西北": ("陕西", "西安", "甘肃", "兰州", "青海", "西宁", "宁夏", "银川", "新疆", "乌鲁木齐"),
}

_DEVELOPMENT_ZONE_SUFFIXES = (
    r"经济技术开发区|高新技术产业开发区|高新技术开发区|产业开发区|工业园区|产业园区|"
    r"科技园区|开发区|经开区|高新区|产业园|工业园|科技园|生态城"
)
_DEVELOPMENT_ZONE_SUFFIX = rf"(?:{_DEVELOPMENT_ZONE_SUFFIXES})"
_ORGANIZATION_SUFFIX = rf"(?:{_DEVELOPMENT_ZONE_SUFFIXES}|大学|学院|学校|研究院|研究所|实验室|省|市|区|县|旗)"
_TYPED_ORGANIZATION_RE = re.compile(
    rf"^[\u4e00-\u9fffA-Za-z0-9·-]{{1,60}}{_ORGANIZATION_SUFFIX}(?:[（(][^）)\n]{{1,20}}[）)])?$"
)
_ATTRIBUTION_RE = re.compile(
    rf"(?:落户(?:于)?|落位(?:于)?|位于|选址(?:于)?|坐落(?:于)?|入驻(?:了)?|迁入|设在|建于|建设地点(?:为|是)|"
    rf"项目地址(?:为|是)|依托|联合|携手)\s*[“\"「『]?"
    rf"([\u4e00-\u9fffA-Za-z0-9·-]{{1,60}}?{_ORGANIZATION_SUFFIX}(?:[（(][^）)\n]{{1,20}}[）)])?)"
)
_KNOWN_PLACE_RE = re.compile(
    rf"((?:中新天津|北京|天津|上海|重庆|成都|深圳|广州|苏州|南京|无锡|常州|杭州|宁波|合肥|武汉|"
    rf"西安|厦门|青岛|济南|郑州|长沙|沈阳|大连|长春|哈尔滨|东莞|佛山|珠海)"
    rf"[\u4e00-\u9fffA-Za-z0-9·-]{{0,30}}?{_ORGANIZATION_SUFFIX}(?:[（(][^）)\n]{{1,20}}[）)])?)"
)
_ZONE_ATTRIBUTION_RE = re.compile(
    rf"(?:落户(?:于)?|落位(?:于)?|位于|选址(?:于)?|坐落(?:于)?|入驻(?:了)?|迁入|设在|建于|建设地点(?:为|是)|项目地址(?:为|是))\s*[“\"「『]?"
    rf"([\u4e00-\u9fffA-Za-z0-9·-]{{1,60}}?{_DEVELOPMENT_ZONE_SUFFIX}(?:[（(][^）)\n]{{1,20}}[）)])?)"
)
_KNOWN_ZONE_RE = re.compile(
    rf"((?:中新天津|北京|天津|上海|重庆|成都|深圳|广州|苏州|南京|无锡|常州|杭州|宁波|合肥|武汉|"
    rf"西安|厦门|青岛|济南|郑州|长沙|沈阳|大连|长春|哈尔滨|东莞|佛山|珠海)"
    rf"[\u4e00-\u9fffA-Za-z0-9·-]{{0,30}}?{_DEVELOPMENT_ZONE_SUFFIX}(?:[（(][^）)\n]{{1,20}}[）)])?)"
)
_COMPANY_NAME_RE = re.compile(r"(?:有限责任公司|股份有限公司|有限公司|公司|集团|企业)$")
_NON_NOUN_RE = re.compile(r"(?:落户|落位|位于|选址|坐落|入驻|迁入|设在|建设|投资|签约|合作|携手|联合|项目|公司|企业|计划|将于|已在|来源于|孵化)")


def _is_specific_organization(value: str) -> bool:
    name = value.strip()
    if not name or _COMPANY_NAME_RE.search(name) or _NON_NOUN_RE.search(name):
        return False
    return bool(_TYPED_ORGANIZATION_RE.fullmatch(name) or re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9·-]{1,30}", name))


def _development_zone_from_context(context: str) -> str:
    for pattern in (_ZONE_ATTRIBUTION_RE, _KNOWN_ZONE_RE):
        match = pattern.search(context or "")
        if match:
            return match.group(1).strip("  　，,。；;：:>、\"'“”‘’「」『』")
    return ""


def _organization_from_context(context: str) -> str:
    """Return only an explicitly typed place or academic/research organization."""
    zone = _development_zone_from_context(context)
    if zone:
        return zone
    for pattern in (_ATTRIBUTION_RE, _KNOWN_PLACE_RE):
        match = pattern.search(context or "")
        if match:
            return match.group(1).strip(" 　，,。；;：:、\"'“”‘’「」『』")
    return ""


def _mainland_subregion(text: str) -> str | None:
    for subregion, keywords in _MAINLAND_REGION_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return subregion
    return None


def _normalize_region_and_organization(region: str, organization: str, context: str) -> tuple[str, str]:
    """Convert model output to the configured vocabulary without losing locality."""
    raw_region = (region or "").strip()
    raw_org = (organization or "").strip()
    # A development zone is the preferred entity when a source also mentions a school.
    context_zone = _development_zone_from_context(context)
    org = context_zone or (raw_org if _is_specific_organization(raw_org) else "")
    if not org and _TYPED_ORGANIZATION_RE.fullmatch(raw_region) and raw_region not in REGION_OPTIONS:
        org = raw_region
    if not org:
        org = _organization_from_context(context)
    compact = raw_region.replace("/", "-").replace("\\", "-").replace(" ", "")
    for option in REGION_OPTIONS:
        if compact == option or compact.endswith("-" + option.split("-", 1)[-1]):
            organization_subregion = _mainland_subregion(org)
            if option.startswith("中国大陆-") and organization_subregion:
                return f"中国大陆-{organization_subregion}", org
            return option, org
    if raw_region in _MAINLAND_REGION_KEYWORDS:
        return f"中国大陆-{raw_region}", org
    if compact in {"中国大陆", "大陆", "中国"}:
        # A province/city in the article is more reliable than guessing a subregion.
        compact = ""
    haystack = " ".join((raw_region, org, context or ""))
    subregion = _mainland_subregion(haystack)
    if subregion:
        return f"中国大陆-{subregion}", org
    if "台湾" in haystack:
        return "中国台湾", org
    if "香港" in haystack:
        return "中国香港", org
    if "澳门" in haystack:
        return "中国澳门", org
    if raw_region in {"海外", "国外", "境外"} or any(word in haystack for word in ("美国", "日本", "韩国", "德国", "新加坡")):
        return "海外", org
    return "其他", org


def _messages(article: RawArticle, error: str | None = None) -> list[dict[str, str]]:
    request = {
        "allowed_info_types": INFO_TYPES,
        "allowed_regions": REGION_OPTIONS,
        "schema": Extraction.model_json_schema(),
        "article": {"title": article.title, "published_at": article.published_at.isoformat() if article.published_at else None,
                    "url": article.canonical_url, "body": article.body[:24000]},
    }
    if error:
        request["previous_error"] = error
        request["instruction"] = "修复上一份输出并完整重发合法 JSON"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(request, ensure_ascii=False)}]


def _print_llm_debug(label: str, value: Any) -> None:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    print(f"\n{'=' * 24} {label} {'=' * 24}", flush=True)
    print(value, flush=True)
    print("=" * (50 + len(label)), flush=True)


def _uses_deepseek_v4_reasoning_controls(setting: ModelSetting) -> bool:
    hostname = (urlparse(setting.base_url).hostname or "").lower()
    return hostname == "api.deepseek.com" and setting.model_name.lower().startswith("deepseek-v4-")


def _call(setting: ModelSetting, messages: list[dict[str, str]]) -> str:
    url = setting.base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": setting.model_name,
        "messages": messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": MAX_OUTPUT_TOKENS,
    }
    if _uses_deepseek_v4_reasoning_controls(setting):
        payload["reasoning_effort"] = "low"
    headers = {"Authorization": f"Bearer {setting.api_key}"}
    for header in json.loads(setting.request_headers_json or "[]"):
        key = header["key"]
        existing_key = next(
            (name for name in headers if name.casefold() == key.casefold()), None
        )
        if existing_key is not None:
            del headers[existing_key]
        headers[key] = header["value"]
    _print_llm_debug("LLM REQUEST", {"url": url, "body": payload})
    response = httpx.post(
        url,
        headers=headers,
        json=payload,
        timeout=MODEL_TIMEOUT,
    )
    try:
        response_data = response.json()
    except (ValueError, TypeError):
        response_data = getattr(response, "text", "<无法读取响应内容>")
    _print_llm_debug("LLM RESPONSE", {
        "status_code": getattr(response, "status_code", "unknown"),
        "body": response_data,
    })
    response.raise_for_status()
    completion_data = response_data
    if (
        isinstance(response_data, dict)
        and "choices" not in response_data
        and isinstance(response_data.get("data"), dict)
    ):
        completion_data = response_data["data"]
    try:
        choice = completion_data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelOutputError("模型接口响应中缺少消息内容") from exc
    if not isinstance(content, str) or not content.strip():
        finish_reason = choice.get("finish_reason")
        suffix = "（输出达到长度上限）" if finish_reason == "length" else ""
        raise ModelOutputError(f"模型返回了空内容{suffix}")
    _print_llm_debug("LLM FINAL CONTENT", content)
    return content


def _parse(raw: str) -> Extraction:
    return Extraction.model_validate_json(_json_text(raw))


def _json_text(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ModelOutputError("模型返回了空内容")
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text


def plan_search_queries(
    setting: ModelSetting,
    topic: str,
    *,
    source_hint: str = "",
    start_date: date | None = None,
) -> list[str]:
    """Turn a search topic into one or more focused search-engine queries."""
    request = {
        "topic": topic,
        "source_hint": source_hint,
        "start_date": start_date.isoformat() if start_date else None,
        "schema": SearchPlan.model_json_schema(),
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是搜索查询规划器。判断用户主题是否需要拆成多个互补查询。"
                "主题已经明确时只返回一条；包含多个技术、事件类型或同义表达，且单条查询可能漏检时，"
                "拆成最多五条简洁、可直接提交给搜索引擎的查询。查询必须忠于原主题，不得虚构企业、"
                "地点或项目。不要加入 after、before、site 等搜索运算符，系统会统一追加日期和来源限制。"
                "只输出符合给定 schema 的 JSON。"
            ),
        },
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
    ]
    raw = _call(setting, messages)
    return SearchPlan.model_validate_json(_json_text(raw)).queries


def _public_model_error(exc: Exception) -> str:
    if isinstance(exc, ModelOutputError):
        return f"{exc}，请检查模型名称、API 额度及接口兼容性"
    if isinstance(exc, httpx.TimeoutException):
        return "模型请求超时，请稍后重试"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"模型接口请求失败（HTTP {exc.response.status_code}）"
    if isinstance(exc, httpx.HTTPError):
        return "无法连接模型接口，请检查 API 地址和网络"
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return "模型返回的 JSON 不完整或格式不符合要求"
    return "模型接口响应无法解析"


def _one_record_per_article(records: list[ExtractedRecord]) -> list[ExtractedRecord]:
    """Collapse model output to one record, honoring the configured type priority."""
    if not records:
        return []

    priority = {info_type: index for index, info_type in enumerate(INFO_TYPES)}
    ordered = sorted(records, key=lambda record: priority[record.info_type])
    primary = ordered[0]

    # Keep the highest-priority record's structured fields and fold all other
    # extracted facts into details so no information is silently discarded.
    additions: list[str] = []
    seen_details = {primary.details.strip()} if primary.details else set()
    for record in ordered[1:]:
        detail = (record.details or "").strip()
        if not detail or detail in seen_details:
            continue
        additions.append(f"{record.info_type}：{detail}")
        seen_details.add(detail)
    if additions:
        primary.details = "\n".join(filter(None, [primary.details.strip(), *additions]))
    return [primary]


def persist_extracted_record(
    db: Session,
    article: RawArticle,
    item: ExtractedRecord,
    *,
    source_name: str | None = None,
) -> StructuredRecord:
    amount = item.investment_amount
    region, organization = _normalize_region_and_organization(
        item.region, item.organization, f"{article.title}\n{article.body}"
    )
    if source_name is None:
        source_name = db.scalar(select(Source.name).where(Source.id == article.source_id)) or ""
    low_confidence = any(score < .6 for score in item.confidence.values())
    record = StructuredRecord(
        article_id=article.id, task_id=article.task_id, region=region,
        organization=organization, company_name=item.company_name,
        event_date=item.event_date or article.published_at,
        info_type=item.info_type, investment_amount=amount.original or "未披露",
        project_name=item.project_name, source_name=source_name,
        original_url=article.canonical_url, details=item.details,
        evidence_json=json.dumps(item.evidence, ensure_ascii=False),
        confidence_json=json.dumps(item.confidence, ensure_ascii=False),
        status="review_required" if low_confidence else "completed",
        amount_value=amount.value, amount_currency=amount.currency,
        amount_unit=amount.unit, amount_note=amount.note,
    )
    db.add(record)
    return record


def structure_article(
    db: Session,
    article: RawArticle,
    setting: ModelSetting,
    *,
    source_name: str | None = None,
) -> int:
    raw = ""
    retry_error = None
    public_error = "模型输出无法通过校验"
    for attempt in range(2):
        try:
            raw = _call(setting, _messages(article, retry_error))
            result = _parse(raw)
            break
        except httpx.TimeoutException as exc:
            # The provider may continue generating and charge for a request after
            # the client stops waiting, so never issue an automatic timeout retry.
            public_error = _public_model_error(exc)
            article.status = "review_required"
            article.error_message = public_error
            article.llm_output = raw
            article.model_name = setting.model_name
            return 0
        except (ModelOutputError, ValidationError, json.JSONDecodeError, httpx.HTTPError) as exc:
            retry_error = str(exc)[:1000]
            public_error = _public_model_error(exc)
    else:
        article.status = "review_required"; article.error_message = f"{public_error}（已自动重试）"; article.llm_output = raw; article.model_name = setting.model_name
        return 0

    records = _one_record_per_article(result.records)
    for item in records:
        persist_extracted_record(db, article, item, source_name=source_name)
    article.status = "completed"; article.error_message = None; article.llm_output = raw; article.model_name = setting.model_name
    return len(records)


def structure_pending(
    db: Session,
    task: CollectionTask,
    stop_requested: Callable[[], None] | None = None,
) -> tuple[int, int]:
    setting = db.get(ModelSetting, 1)
    articles = db.scalars(select(RawArticle).where(RawArticle.task_id == task.id, RawArticle.status == "pending")).all()
    if not setting or not setting.enabled or not setting.api_key:
        if articles:
            db.add(TaskLog(task_id=task.id, level="notice", message="模型未启用，原文已保存为待结构化"))
        return 0, 0
    count = failed = 0
    for article in articles:
        if stop_requested:
            stop_requested()
        created = structure_article(db, article, setting)
        if stop_requested:
            stop_requested()
        count += created
        task.structured_count += created
        if article.status == "review_required":
            failed += 1
            task.failed_count += 1
            db.add(TaskLog(task_id=task.id, level="error", message=f"结构化待审核 {article.canonical_url}: {article.error_message}"))
        db.commit()
    return count, failed
