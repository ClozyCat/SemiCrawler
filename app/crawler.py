from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .dokobot import (
    DokobotClient,
    DokobotError,
    build_search_query,
    source_hint_variants,
)
from .llm import (
    ModelOutputError,
    plan_search_queries,
    structure_article,
    structure_pending,
)
from .models import CollectionTask, ModelSetting, RawArticle, TaskLog, utc_now
from .source_config import (
    SourceConfig,
    WebSearchSourceConfig,
    source_type,
    validate_source_config,
)

USER_AGENT = "SemiCrawler/1.0 (+public-news-collector)"


class TaskTerminationRequested(Exception):
    pass


def ensure_task_active(db: Session, task: CollectionTask) -> None:
    db.refresh(task, attribute_names=["status"])
    if task.status in {"terminating", "terminated"}:
        raise TaskTerminationRequested


_EVENT_TERMS_RE = re.compile(
    r"(?:论坛|峰会|年会|大会|展会|展览会|博览会|交流会|研讨会|同期活动)"
)
_EVENT_PROMOTION_PATTERNS = (
    re.compile(r"(?:将于|将在|拟于|定于).{0,40}(?:举办|召开|开幕|举行)"),
    re.compile(r"(?:同期|现场).{0,20}(?:论坛|活动|圆桌|发布)"),
    re.compile(r"(?:展商|展位|参展|参会|报名|观众预登记|嘉宾|议程|圆桌对话)"),
    re.compile(r"(?:汇聚|齐聚|亮相|不容错过|敬请期待|链接.{0,8}机遇)"),
    re.compile(r"(?:\d+(?:\.\d+)?万?平方米|\d+\+.{0,12}(?:展商|企业|论坛))"),
)
_SUBSTANTIVE_EVENT_RE = re.compile(
    r"(?:正式签约|达成.{0,12}(?:协议|合作)|完成.{0,12}(?:融资|募资)|获得.{0,12}(?:融资|投资)|"
    r"宣布.{0,20}(?:投资|增资|收购|并购|投产|量产|扩产)|投资(?:金额|总额|规模).{0,20}\d|"
    r"(?:项目|产线|工厂|基地).{0,30}(?:落户|开工|竣工|投产|量产|扩产|获批)|"
    r"成功研制|实现.{0,8}突破|通过.{0,8}验收|首台套)"
)


def keyword_values(config: list[dict[str, Any]]) -> list[str]:
    """Flatten configured hierarchy cells; field names are deliberately ignored."""
    values: list[str] = []
    for row in config:
        for value in row.values():
            if not isinstance(value, str):
                continue
            values.extend(
                item.strip().casefold()
                for item in re.split(r"[、,，;；\n]+", value)
                if item.strip()
            )
    return list(dict.fromkeys(values))


def keyword_groups(config: Any) -> dict[str, list[str]]:
    """Return the three configured vocabularies, with legacy arrays as technical terms."""
    if isinstance(config, list):
        return {
            "technical": keyword_values(config),
            "industry_noun": [],
            "industry_verb": [],
        }
    if not isinstance(config, dict):
        return {"technical": [], "industry_noun": [], "industry_verb": []}

    aliases = {
        "technical": ("technical", "technical_terms", "sheet1", "技术名词"),
        "industry_noun": ("industry_noun", "industry_nouns", "sheet2", "项目名词"),
        "industry_verb": ("industry_verb", "industry_verbs", "sheet3", "项目动词"),
    }
    groups: dict[str, list[str]] = {}
    for name, keys in aliases.items():
        raw = next((config[key] for key in keys if key in config), [])
        groups[name] = keyword_values(raw if isinstance(raw, list) else [])
    return groups


def merge_ranked_search_results(
    batches: list[list[Any]], limit: int | None = None
) -> list[Any]:
    """Round-robin ranked result sets so one query cannot crowd out the others."""
    merged: list[Any] = []
    seen: set[str] = set()
    rank = 0
    while (limit is None or len(merged) < limit) and any(
        rank < len(batch) for batch in batches
    ):
        for batch in batches:
            if rank >= len(batch):
                continue
            item = batch[rank]
            key = canonical_url(str(item.link), str(item.link))
            if key not in seen:
                seen.add(key)
                merged.append(item)
                if limit is not None and len(merged) == limit:
                    break
        rank += 1
    return merged


def is_low_value_event_promotion(title: str, body: str) -> bool:
    """Identify event invitations/previews that lack a concrete industry event."""
    text = " ".join(f"{title}\n{body}".split())
    if not _EVENT_TERMS_RE.search(text) or _SUBSTANTIVE_EVENT_RE.search(text):
        return False
    promotion_signals = sum(
        bool(pattern.search(text)) for pattern in _EVENT_PROMOTION_PATTERNS
    )
    return promotion_signals >= 2


@lru_cache(maxsize=32)
def _robots(origin: str) -> RobotFileParser:
    parser = RobotFileParser()
    parser.set_url(origin.rstrip("/") + "/robots.txt")
    try:
        response = httpx.get(
            parser.url,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
            follow_redirects=True,
        )
        parser.parse(response.text.splitlines() if response.is_success else [])
    except httpx.HTTPError:
        parser.parse([])
    return parser


def fetch_html(url: str, timeout: float = 20) -> str:
    parsed = urlparse(url)
    if not _robots(f"{parsed.scheme}://{parsed.netloc}").can_fetch(USER_AGENT, url):
        raise PermissionError(f"robots.txt 不允许采集 {url}")
    response = httpx.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True
    )
    response.raise_for_status()
    return response.text


def canonical_url(url: str, base_url: str) -> str:
    parsed = urlparse(urljoin(base_url, url))
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, "")
    )


def _text(node) -> str:
    return (
        "\n".join(
            line.strip() for line in node.get_text("\n").splitlines() if line.strip()
        )
        if node
        else ""
    )


def parse_date(text: str, formats: list[str]) -> date | None:
    match = re.search(r"20\d{2}[年/.-]\d{1,2}[月/.-]\d{1,2}日?", text)
    if not match:
        return None
    for fmt in [*formats, "%Y.%m.%d"]:
        try:
            return datetime.strptime(match.group(0), fmt).date()
        except ValueError:
            pass
    return None


def parse_article(html: str, url: str, config: SourceConfig) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.select_one(config.selectors.title))[:500]
    date_text = _text(soup.select_one(config.selectors.date))[:200]
    content = soup.select_one(config.selectors.content)
    if content:
        for unwanted in content.select(
            "script,style,nav,aside,.advertisement,.related,.copyright,.prevnext"
        ):
            unwanted.decompose()
    body = _text(content)
    if not title or len(body) < 50:
        raise ValueError(f"选择器未解析出有效标题或正文（正文 {len(body)} 字）")
    return {
        "canonical_url": canonical_url(url, url),
        "title": title,
        "published_text": date_text,
        "published_at": parse_date(date_text, config.date_formats),
        "body": body,
    }


def discover_listing(
    html: str, page_url: str, config: SourceConfig
) -> tuple[list[str], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.select(config.selectors.list_links):
        if anchor.get("href"):
            candidate = canonical_url(anchor["href"], page_url)
            if re.search(config.article_url_pattern, urlparse(candidate).path):
                links.append(candidate)
    next_url = None
    if config.pagination.next_page_selector:
        node = soup.select_one(config.pagination.next_page_selector)
        if node and node.get("href"):
            next_url = canonical_url(node["href"], page_url)
    return list(dict.fromkeys(links)), next_url


def test_source(base_url: str, raw_config: dict[str, Any]) -> dict[str, Any]:
    config = validate_source_config(base_url, raw_config)
    if isinstance(config, WebSearchSourceConfig):
        return {
            "type": "web_search",
            "query": config.query,
            "source_hint": config.source_hint,
        }
    listing = fetch_html(config.entry_urls[0], config.request.timeout_seconds)
    links, _ = discover_listing(listing, config.entry_urls[0], config)
    if not links:
        raise ValueError("入口页没有匹配到文章链接")
    article = parse_article(
        fetch_html(links[0], config.request.timeout_seconds), links[0], config
    )
    return {
        "url": article["canonical_url"],
        "title": article["title"],
        "published_at": article["published_at"],
        "published_text": article["published_text"],
        "body_length": len(article["body"]),
        "first_paragraph": article["body"].split("\n", 1)[0][:300],
    }


def collect_web_search_source(
    db: Session,
    task: CollectionTask,
    snapshot: dict[str, Any],
    config: WebSearchSourceConfig,
) -> tuple[int, int, int, int, int, int]:
    ensure_task_active(db, task)
    setting = db.get(ModelSetting, 1)
    if not setting or not setting.api_key:
        raise ValueError("联网搜索需要先在 API配置 中保存结构化模型的 API Key")

    client = DokobotClient()
    close_stale_sessions = getattr(client, "close_stale_sessions", None)
    if close_stale_sessions:
        close_stale_sessions()
    search_engine = client.select_search_engine()
    db.add(
        TaskLog(
            task_id=task.id,
            message=f"搜索引擎连通性检测通过，本次联网搜索使用 {search_engine}",
        )
    )
    db.commit()
    try:
        planned_queries = plan_search_queries(
            setting,
            config.query,
            source_hint=config.source_hint,
            start_date=task.start_date,
        )
    except (
        ModelOutputError,
        ValidationError,
        json.JSONDecodeError,
        httpx.HTTPError,
        ValueError,
    ) as exc:
        planned_queries = []
        db.add(
            TaskLog(
                task_id=task.id,
                level="notice",
                message=(
                    "LLM 搜索查询规划失败，已回退到原始查询："
                    f"{type(exc).__name__}: {str(exc)[:300]}"
                ),
            )
        )
    original_query_key = " ".join(config.query.split()).casefold()
    planned_queries = [
        query
        for query in planned_queries[:5]
        if " ".join(query.split()).casefold() != original_query_key
    ]
    llm_query_count = len(planned_queries)
    planned_queries.append(config.query)
    db.add(
        TaskLog(
            task_id=task.id,
            message=(
                f"本次将执行 {len(planned_queries)} 条搜索查询"
                f"（{llm_query_count} 条 LLM 规划查询 + 1 条原始查询）："
                f"{json.dumps(planned_queries, ensure_ascii=False)}"
            ),
        )
    )
    db.commit()

    source_variants = source_hint_variants(config.source_hint)
    search_specs = [
        (planned_query, source_hint)
        for planned_query in planned_queries
        for source_hint in source_variants
    ]
    result_batches = []
    search_errors: list[DokobotError] = []
    for planned_query, source_hint in search_specs:
        ensure_task_active(db, task)
        search_query = build_search_query(planned_query, source_hint, task.start_date)
        try:
            result_batches.append(client.search(search_query, num=config.max_results))
        except DokobotError as exc:
            search_errors.append(exc)
            db.add(
                TaskLog(
                    task_id=task.id,
                    level="error",
                    message=f"Dokobot 查询失败 {search_query}：{exc}",
                )
            )
            db.commit()
    if not result_batches and search_errors:
        raise search_errors[0]
    results = merge_ranked_search_results(result_batches)
    ensure_task_active(db, task)
    pages = []
    failed = 0
    for search_item in results:
        ensure_task_active(db, task)
        page = None
        try:
            page = client.read(str(search_item.link))
            pages.append((search_item, page))
            ensure_task_active(db, task)
        except DokobotError as exc:
            failed += 1
            db.add(
                TaskLog(
                    task_id=task.id,
                    level="error",
                    message=f"Dokobot 读页失败 {search_item.link}: {exc}",
                )
            )
            db.commit()
        finally:
            if page:
                try:
                    close_session = getattr(client, "close_session", None)
                    if close_session:
                        close_session(page.session_id)
                except DokobotError:
                    pass

    configured = json.loads(task.keyword_config_json or "[]")
    groups = keyword_groups(configured) if task.keyword_filter_enabled else {}
    saved = structured = deduped = date_filtered = keyword_filtered = 0
    for search_item, page in pages:
        ensure_task_active(db, task)
        url = canonical_url(str(page.url), str(page.url))
        body = page.text.strip()
        title = page.title or search_item.title
        published_at = parse_date(
            f"{title}\n{search_item.snippet}\n{body[:4000]}",
            [
                "%Y-%m-%d",
                "%Y/%m/%d",
                "%Y年%m月%d日",
            ],
        )
        if published_at and published_at < task.start_date:
            date_filtered += 1
            continue
        if task.keyword_filter_enabled:
            haystack = f"{title}\n{body}".casefold()
            if isinstance(configured, dict):
                matched = all(
                    any(keyword in haystack for keyword in terms)
                    for terms in groups.values()
                )
            else:
                matched = any(
                    keyword in haystack for keyword in groups.get("technical", [])
                )
            if not matched or is_low_value_event_promotion(title, body):
                keyword_filtered += 1
                continue
        digest = hashlib.sha256(" ".join(body.split()).encode()).hexdigest()
        existing = db.scalar(
            select(RawArticle).where(
                (RawArticle.canonical_url == url) | (RawArticle.content_hash == digest)
            )
        )
        if existing:
            deduped += 1
            task.deduplicated_count += 1
            continue
        article = RawArticle(
            source_id=snapshot["id"],
            task_id=task.id,
            canonical_url=url,
            title=title[:500],
            published_at=published_at,
            published_text=published_at.isoformat() if published_at else None,
            body=body,
            content_hash=digest,
            status="pending",
        )
        db.add(article)
        db.flush()
        source_name = (urlparse(url).hostname or snapshot["name"]).removeprefix("www.")
        created = structure_article(db, article, setting, source_name=source_name)
        ensure_task_active(db, task)
        structured += created
        if article.status == "review_required":
            failed += 1
            db.add(
                TaskLog(
                    task_id=task.id,
                    level="error",
                    message=f"结构化待审核 {url}: {article.error_message}",
                )
            )
        saved += 1
        task.fetched_count += 1
        task.structured_count += created
        db.commit()
    db.add(
        TaskLog(
            task_id=task.id,
            message=(
                f"Dokobot 本地联网检索完成 {snapshot['name']}："
                f"执行 {len(search_specs)} 条搜索查询（{len(planned_queries)} 个关键词 × "
                f"{len(source_variants)} 个来源范围），"
                f"合并找到 {len(results)} 篇，读取 {len(pages)} 篇，"
                f"保存 {saved} 篇、结构化 {structured} 条，"
                f"日期过滤 {date_filtered} 篇，关键词跳过 {keyword_filtered} 篇"
            ),
        )
    )
    return saved, deduped, failed, len(results), date_filtered, keyword_filtered


def collect_source(
    db: Session, task: CollectionTask, snapshot: dict[str, Any]
) -> tuple[int, int, int, int, int, int]:
    config = validate_source_config(snapshot["base_url"], snapshot.get("config", {}))
    if isinstance(config, WebSearchSourceConfig):
        return collect_web_search_source(db, task, snapshot, config)
    configured = json.loads(task.keyword_config_json or "[]")
    groups = keyword_groups(configured) if task.keyword_filter_enabled else {}
    seen_urls: set[str] = set()
    discovered = saved = date_filtered = keyword_filtered = deduped = failed = 0
    delay = 60 / config.request.rate_limit_per_minute
    for first_entry in config.entry_urls:
        ensure_task_active(db, task)
        entry = first_entry
        visited_pages: set[str] = set()
        for _ in range(config.pagination.max_pages):
            ensure_task_active(db, task)
            if not entry or entry in visited_pages:
                break
            visited_pages.add(entry)
            try:
                links, next_url = discover_listing(
                    fetch_html(entry, config.request.timeout_seconds), entry, config
                )
                ensure_task_active(db, task)
            except TaskTerminationRequested:
                raise
            except Exception as exc:
                failed += 1
                task.failed_count += 1
                db.add(
                    TaskLog(
                        task_id=task.id,
                        level="error",
                        message=f"入口抓取失败 {entry}: {exc}",
                    )
                )
                db.commit()
                break
            page_dates: list[date] = []
            for url in links:
                ensure_task_active(db, task)
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                discovered += 1
                try:
                    article = parse_article(
                        fetch_html(url, config.request.timeout_seconds), url, config
                    )
                    ensure_task_active(db, task)
                    if article["published_at"]:
                        page_dates.append(article["published_at"])
                    if (
                        article["published_at"]
                        and article["published_at"] < task.start_date
                    ):
                        date_filtered += 1
                        continue
                    if task.keyword_filter_enabled:
                        haystack = f"{article['title']}\n{article['body']}".casefold()
                        if isinstance(configured, dict):
                            matched = all(
                                any(keyword in haystack for keyword in terms)
                                for terms in groups.values()
                            )
                        else:
                            matched = any(
                                keyword in haystack
                                for keyword in groups.get("technical", [])
                            )
                        if not matched or is_low_value_event_promotion(
                            article["title"], article["body"]
                        ):
                            keyword_filtered += 1
                            continue
                    digest = hashlib.sha256(
                        " ".join(article["body"].split()).encode()
                    ).hexdigest()
                    existing = db.scalar(
                        select(RawArticle).where(
                            (RawArticle.canonical_url == article["canonical_url"])
                            | (RawArticle.content_hash == digest)
                        )
                    )
                    if existing:
                        deduped += 1
                        task.deduplicated_count += 1
                        continue
                    db.add(
                        RawArticle(
                            source_id=snapshot["id"],
                            task_id=task.id,
                            content_hash=digest,
                            status="pending",
                            **article,
                        )
                    )
                    saved += 1
                    task.fetched_count += 1
                except TaskTerminationRequested:
                    raise
                except Exception as exc:
                    failed += 1
                    task.failed_count += 1
                    db.add(
                        TaskLog(
                            task_id=task.id,
                            level="error",
                            message=f"抓取失败 {url}: {exc}",
                        )
                    )
                finally:
                    # Expose per-article counters to the polling API while the task is running.
                    db.commit()
                    time.sleep(delay)
            if page_dates and max(page_dates) < task.start_date:
                break
            entry = next_url
    return saved, deduped, failed, discovered, date_filtered, keyword_filtered


def run_task(db: Session, task: CollectionTask) -> None:
    ensure_task_active(db, task)
    if task.status != "queued":
        return
    task.status = "running"
    task.started_at = utc_now()
    db.commit()
    totals = [0, 0, 0, 0, 0, 0]
    try:
        for snapshot in json.loads(task.source_snapshot_json):
            ensure_task_active(db, task)
            try:
                values = collect_source(db, task, snapshot)
            except TaskTerminationRequested:
                raise
            except Exception as exc:
                values = (0, 0, 1, 0, 0, 0)
                task.failed_count += 1
                action = (
                    "联网检索"
                    if source_type(snapshot.get("config", {})) == "web_search"
                    else "来源配置"
                )
                db.add(
                    TaskLog(
                        task_id=task.id,
                        level="error",
                        message=f"{action}失败 {snapshot['name']}: {exc}",
                    )
                )
            totals = [a + b for a, b in zip(totals, values)]
            db.commit()
        ensure_task_active(db, task)
        directly_structured = task.structured_count
        structured, llm_failed = (
            structure_pending(
                db,
                task,
                stop_requested=lambda: ensure_task_active(db, task),
            )
            if task.auto_structure_enabled
            else (0, 0)
        )
        ensure_task_active(db, task)
        task.fetched_count, task.deduplicated_count = totals[0], totals[1]
        task.structured_count = directly_structured + structured
        task.failed_count = totals[2] + llm_failed
        task.status = "completed" if task.failed_count == 0 else "completed_with_errors"
        task.completed_at = utc_now()
        db.add(
            TaskLog(
                task_id=task.id,
                message=(
                    f"任务完成：发现 {totals[3]} 篇，日期过滤 {totals[4]} 篇，关键词跳过 {totals[5]} 篇，"
                    f"保存 {totals[0]} 篇，去重 {totals[1]} 篇，结构化 {task.structured_count} 条，失败 {task.failed_count} 篇"
                ),
            )
        )
        db.commit()
    except TaskTerminationRequested:
        db.rollback()
        db.refresh(task)
        task.status = "terminated"
        task.completed_at = utc_now()
        db.add(TaskLog(task_id=task.id, level="notice", message="任务已终止"))
        db.commit()
