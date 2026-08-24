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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .llm import structure_pending
from .models import CollectionTask, ModelSetting, RawArticle, Source, SourceVersion, TaskLog, utc_now
from .source_config import SourceConfig, SourceConfigV2, validate_source_config
from .collection.adaptive import detect_and_validate
from .collection.article_executor import ArticleCollectionExecutor
from .collection.executors import CollectionExecutor
from .collection.fetcher import FetchLimits, SafeFetcher
from .collection.profiles import CollectionProfile
from .collection.probe_agent import ProbeAgent

USER_AGENT = "SemiCrawler/1.0 (+public-news-collector)"

_EVENT_TERMS_RE = re.compile(r"(?:论坛|峰会|年会|大会|展会|展览会|博览会|交流会|研讨会|同期活动)")
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
            values.extend(item.strip().casefold() for item in re.split(r"[、,，;；\n]+", value) if item.strip())
    return list(dict.fromkeys(values))


def keyword_groups(config: Any) -> dict[str, list[str]]:
    """Return the three configured vocabularies, with legacy arrays as technical terms."""
    if isinstance(config, list):
        return {"technical": keyword_values(config), "industry_noun": [], "industry_verb": []}
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


def is_low_value_event_promotion(title: str, body: str) -> bool:
    """Identify event invitations/previews that lack a concrete industry event."""
    text = " ".join(f"{title}\n{body}".split())
    if not _EVENT_TERMS_RE.search(text) or _SUBSTANTIVE_EVENT_RE.search(text):
        return False
    promotion_signals = sum(bool(pattern.search(text)) for pattern in _EVENT_PROMOTION_PATTERNS)
    return promotion_signals >= 2


@lru_cache(maxsize=32)
def _robots(origin: str) -> RobotFileParser:
    parser = RobotFileParser()
    parser.set_url(origin.rstrip("/") + "/robots.txt")
    try:
        response = httpx.get(parser.url, headers={"User-Agent": USER_AGENT}, timeout=10, follow_redirects=True)
        parser.parse(response.text.splitlines() if response.is_success else [])
    except httpx.HTTPError:
        parser.parse([])
    return parser


def fetch_html(url: str, timeout: float = 20) -> str:
    parsed = urlparse(url)
    if not _robots(f"{parsed.scheme}://{parsed.netloc}").can_fetch(USER_AGENT, url):
        raise PermissionError(f"robots.txt 不允许采集 {url}")
    response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def canonical_url(url: str, base_url: str) -> str:
    parsed = urlparse(urljoin(base_url, url))
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def _text(node) -> str:
    return "\n".join(line.strip() for line in node.get_text("\n").splitlines() if line.strip()) if node else ""


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
        for unwanted in content.select("script,style,nav,aside,.advertisement,.related,.copyright,.prevnext"):
            unwanted.decompose()
    body = _text(content)
    if not title or len(body) < 50:
        raise ValueError(f"选择器未解析出有效标题或正文（正文 {len(body)} 字）")
    return {"canonical_url": canonical_url(url, url), "title": title, "published_text": date_text,
            "published_at": parse_date(date_text, config.date_formats), "body": body}


def discover_listing(html: str, page_url: str, config: SourceConfig) -> tuple[list[str], str | None]:
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
    if isinstance(config, SourceConfigV2):
        allowed_hosts = _adaptive_allowed_hosts(base_url, config)
        with _adaptive_fetcher(config, allowed_hosts) as fetcher:
            profile = (CollectionProfile.model_validate(config.learned_profile) if config.learned_profile
                       else detect_and_validate(fetcher, config.entry_urls[0], sorted(allowed_hosts)))
            page = next(CollectionExecutor(fetcher).pages(profile, max_pages=1, max_items=20), None)
            if not page or not page.items:
                raise ValueError("已验证规则没有解析出预览记录")
            item = page.items[0]
            return {
                "url": item.canonical_url, "title": item.title, "published_at": item.published_at,
                "published_text": item.published_text, "body_length": len(item.body),
                "first_paragraph": item.body.split("\n", 1)[0][:300],
                "content_kind": item.content_kind, "profile": profile.model_dump(mode="json"),
            }
    listing = fetch_html(config.entry_urls[0], config.request.timeout_seconds)
    links, _ = discover_listing(listing, config.entry_urls[0], config)
    if not links:
        raise ValueError("入口页没有匹配到文章链接")
    article = parse_article(fetch_html(links[0], config.request.timeout_seconds), links[0], config)
    return {"url": article["canonical_url"], "title": article["title"],
            "published_at": article["published_at"], "published_text": article["published_text"],
            "body_length": len(article["body"]), "first_paragraph": article["body"].split("\n", 1)[0][:300]}


def _adaptive_allowed_hosts(base_url: str, config: SourceConfigV2) -> set[str]:
    hosts = {urlparse(base_url).hostname or "", *config.allowed_hosts}
    hosts.update(urlparse(entry).hostname or "" for entry in config.entry_urls)
    return {host.rstrip(".").lower() for host in hosts if host}


def _adaptive_fetcher(config: SourceConfigV2, allowed_hosts: set[str]) -> SafeFetcher:
    limits = FetchLimits(
        timeout_seconds=config.limits.timeout_seconds,
        rate_limit_per_minute=config.limits.rate_limit_per_minute,
    )
    return SafeFetcher(config.entry_urls[0], allowed_hosts=allowed_hosts, limits=limits)


def _persist_learned_profile(db: Session, snapshot: dict[str, Any], config: SourceConfigV2,
                             profile: CollectionProfile) -> None:
    source = db.get(Source, snapshot["id"])
    if not source:
        return
    raw_config = config.model_dump(mode="json")
    raw_config["learned_profile"] = profile.model_dump(mode="json")
    serialized = json.dumps(raw_config, ensure_ascii=False)
    if source.config_json == serialized:
        return
    source.config_json = serialized
    version = (db.scalar(select(func.max(SourceVersion.version)).where(
        SourceVersion.source_id == source.id
    )) or 0) + 1
    db.add(SourceVersion(source_id=source.id, version=version, config_json=serialized))
    snapshot["config"] = raw_config


def _probe_model_call(setting: Any):
    from .llm import _call
    return lambda messages: _call(setting, messages)


def _adaptive_profile(db: Session, task: CollectionTask, snapshot: dict[str, Any],
                      config: SourceConfigV2, fetcher: SafeFetcher,
                      allowed_hosts: set[str], force_reprobe: bool = False) -> CollectionProfile:
    if config.learned_profile and not force_reprobe:
        return CollectionProfile.model_validate(config.learned_profile)
    if force_reprobe:
        config = config.model_copy(update={"learned_profile": None})
    try:
        profile = detect_and_validate(fetcher, config.entry_urls[0], sorted(allowed_hosts))
        _persist_learned_profile(db, snapshot, config, profile)
        db.add(TaskLog(task_id=task.id, message=(
            f"确定性探测完成：{profile.content_kind}/{profile.detection_method}，"
            f"置信度 {profile.confidence:.0%}"
        )))
        db.commit()
        return profile
    except Exception as deterministic_error:
        setting = db.get(ModelSetting, 1)
        if not setting or not setting.enabled or not setting.api_key:
            raise ValueError(f"确定性探测失败，模型未启用: {deterministic_error}") from deterministic_error
        profile = ProbeAgent(
            fetcher, _probe_model_call(setting), config.entry_urls[0], sorted(allowed_hosts),
        ).run().model_copy(update={"model_name": setting.model_name})
        _persist_learned_profile(db, snapshot, config, profile)
        db.add(TaskLog(task_id=task.id, message=(
            f"模型探测完成：{profile.content_kind}，模型 {setting.model_name}，"
            f"置信度 {profile.confidence:.0%}"
        )))
        db.commit()
        return profile


def collect_adaptive_source(db: Session, task: CollectionTask, snapshot: dict[str, Any],
                            config: SourceConfigV2) -> tuple[int, int, int, int, int, int]:
    configured = json.loads(task.keyword_config_json or "[]")
    groups = keyword_groups(configured) if task.keyword_filter_enabled else {}
    allowed_hosts = _adaptive_allowed_hosts(snapshot["base_url"], config)
    saved = deduped = failed = discovered = date_filtered = keyword_filtered = 0
    with _adaptive_fetcher(config, allowed_hosts) as fetcher:
        if config.learned_profile:
            profile = CollectionProfile.model_validate(config.learned_profile)
            try:
                if profile.content_kind == "articles":
                    from .collection.article_executor import ArticleProfileValidator
                    profile = ArticleProfileValidator(ArticleCollectionExecutor(fetcher)).validate(profile)
                else:
                    from .collection.validation import ProfileValidator
                    profile = ProfileValidator(CollectionExecutor(fetcher)).validate(profile)
                db.add(TaskLog(task_id=task.id, message=(
                    f"复用已验证规则，版本 {profile.profile_version}，指纹 {profile.fingerprint[:12]}"
                )))
            except Exception as validation_error:
                db.add(TaskLog(task_id=task.id, level="notice", message=(
                    f"规则验证失败，启动单次自动修复：{str(validation_error)[:500]}"
                )))
                db.commit()
                profile = _adaptive_profile(db, task, snapshot, config, fetcher, allowed_hosts, force_reprobe=True)
        else:
            profile = _adaptive_profile(db, task, snapshot, config, fetcher, allowed_hosts)

        if profile.content_kind == "articles":
            executor = ArticleCollectionExecutor(fetcher)
            for result in executor.items(
                profile, max_pages=config.limits.max_pages, max_items=config.limits.max_items,
                start_date=task.start_date,
            ):
                discovered += 1
                if not result.item:
                    failed += 1
                    db.add(TaskLog(task_id=task.id, level="error", message=f"文章抽取失败 {result.url}: {result.error}"))
                    continue
                item = result.item
                if item.published_at and item.published_at < task.start_date:
                    date_filtered += 1
                    continue
                if task.keyword_filter_enabled:
                    haystack = f"{item.title}\n{item.body}".casefold()
                    if isinstance(configured, dict):
                        matched = all(any(keyword in haystack for keyword in terms) for terms in groups.values())
                    else:
                        matched = any(keyword in haystack for keyword in groups.get("technical", []))
                    if not matched or is_low_value_event_promotion(item.title, item.body):
                        keyword_filtered += 1
                        continue
                existing = db.scalar(select(RawArticle).where(
                    RawArticle.source_id == snapshot["id"], RawArticle.source_item_key == item.source_item_key,
                ))
                if existing:
                    deduped += 1; task.deduplicated_count += 1
                    continue
                digest = hashlib.sha256(" ".join(item.body.split()).encode("utf-8")).hexdigest()
                db.add(RawArticle(
                    source_id=snapshot["id"], task_id=task.id, canonical_url=item.canonical_url,
                    source_item_key=item.source_item_key, content_kind="article",
                    raw_payload_json=json.dumps(item.raw_payload, ensure_ascii=False), title=item.title,
                    published_at=item.published_at, published_text=item.published_text, body=item.body,
                    content_hash=digest, status="pending",
                ))
                saved += 1; task.fetched_count += 1
            db.commit()
            return saved, deduped, failed, discovered, date_filtered, keyword_filtered

        executor = CollectionExecutor(fetcher)
        for page in executor.pages(
            profile, max_pages=config.limits.max_pages, max_items=config.limits.max_items,
            start_date=task.start_date,
        ):
            for item in page.items:
                discovered += 1
                if item.published_at and item.published_at < task.start_date:
                    date_filtered += 1
                    continue
                if task.keyword_filter_enabled:
                    haystack = f"{item.title}\n{item.body}".casefold()
                    if isinstance(configured, dict):
                        matched = all(any(keyword in haystack for keyword in terms) for terms in groups.values())
                    else:
                        matched = any(keyword in haystack for keyword in groups.get("technical", []))
                    if not matched or is_low_value_event_promotion(item.title, item.body):
                        keyword_filtered += 1
                        continue
                existing = db.scalar(select(RawArticle).where(
                    RawArticle.source_id == snapshot["id"],
                    RawArticle.source_item_key == item.source_item_key,
                ))
                if existing:
                    deduped += 1
                    task.deduplicated_count += 1
                    continue
                body = item.body
                digest = hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest()
                db.add(RawArticle(
                    source_id=snapshot["id"], task_id=task.id, canonical_url=item.canonical_url,
                    source_item_key=item.source_item_key, content_kind=item.content_kind,
                    raw_payload_json=json.dumps(item.fields, ensure_ascii=False), title=item.title,
                    published_at=item.published_at, published_text=item.published_text, body=body,
                    content_hash=digest, status="pending",
                ))
                saved += 1
                task.fetched_count += 1
            db.add(TaskLog(task_id=task.id, message=(
                f"表格第 {page.number} 页：解析 {len(page.items)} 条，累计保存 {saved} 条"
            )))
            db.commit()
    return saved, deduped, failed, discovered, date_filtered, keyword_filtered


def collect_source(db: Session, task: CollectionTask, snapshot: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    config = validate_source_config(snapshot["base_url"], snapshot.get("config", {}))
    if isinstance(config, SourceConfigV2):
        return collect_adaptive_source(db, task, snapshot, config)
    configured = json.loads(task.keyword_config_json or "[]")
    groups = keyword_groups(configured) if task.keyword_filter_enabled else {}
    seen_urls: set[str] = set()
    discovered = saved = date_filtered = keyword_filtered = deduped = failed = 0
    delay = 60 / config.request.rate_limit_per_minute
    for first_entry in config.entry_urls:
        entry = first_entry
        visited_pages: set[str] = set()
        for _ in range(config.pagination.max_pages):
            if not entry or entry in visited_pages:
                break
            visited_pages.add(entry)
            try:
                links, next_url = discover_listing(fetch_html(entry, config.request.timeout_seconds), entry, config)
            except Exception as exc:
                failed += 1
                task.failed_count += 1
                db.add(TaskLog(task_id=task.id, level="error", message=f"入口抓取失败 {entry}: {exc}"))
                db.commit()
                break
            page_dates: list[date] = []
            for url in links:
                if url in seen_urls:
                    continue
                seen_urls.add(url); discovered += 1
                try:
                    article = parse_article(fetch_html(url, config.request.timeout_seconds), url, config)
                    if article["published_at"]:
                        page_dates.append(article["published_at"])
                    if article["published_at"] and article["published_at"] < task.start_date:
                        date_filtered += 1
                        continue
                    if task.keyword_filter_enabled:
                        haystack = f"{article['title']}\n{article['body']}".casefold()
                        if isinstance(configured, dict):
                            matched = all(any(keyword in haystack for keyword in terms) for terms in groups.values())
                        else:
                            matched = any(keyword in haystack for keyword in groups.get("technical", []))
                        if (not matched or is_low_value_event_promotion(article["title"], article["body"])):
                            keyword_filtered += 1
                            continue
                    digest = hashlib.sha256(" ".join(article["body"].split()).encode()).hexdigest()
                    source_item_key = article["canonical_url"]
                    existing = db.scalar(select(RawArticle).where(
                        ((RawArticle.source_id == snapshot["id"]) & (RawArticle.source_item_key == source_item_key))
                        | (RawArticle.content_hash == digest)
                    ))
                    if existing:
                        deduped += 1
                        task.deduplicated_count += 1
                        continue
                    db.add(RawArticle(source_id=snapshot["id"], task_id=task.id, source_item_key=source_item_key,
                                      content_kind="article", raw_payload_json="{}", content_hash=digest,
                                      status="pending", **article))
                    saved += 1
                    task.fetched_count += 1
                except Exception as exc:
                    failed += 1
                    task.failed_count += 1
                    db.add(TaskLog(task_id=task.id, level="error", message=f"抓取失败 {url}: {exc}"))
                finally:
                    # Expose per-article counters to the polling API while the task is running.
                    db.commit()
                    time.sleep(delay)
            if page_dates and max(page_dates) < task.start_date:
                break
            entry = next_url
    return saved, deduped, failed, discovered, date_filtered, keyword_filtered


def run_task(db: Session, task: CollectionTask) -> None:
    task.status = "running"; task.started_at = utc_now(); db.commit()
    totals = [0, 0, 0, 0, 0, 0]
    for snapshot in json.loads(task.source_snapshot_json):
        try:
            values = collect_source(db, task, snapshot)
        except Exception as exc:
            values = (0, 0, 1, 0, 0, 0)
            task.failed_count += 1
            db.add(TaskLog(task_id=task.id, level="error", message=f"来源配置失败 {snapshot['name']}: {exc}"))
        totals = [a + b for a, b in zip(totals, values)]; db.commit()
    structured, llm_failed = structure_pending(db, task) if task.auto_structure_enabled else (0, 0)
    task.fetched_count, task.deduplicated_count, task.structured_count = totals[0], totals[1], structured
    task.failed_count = totals[2] + llm_failed
    task.status = "completed" if task.failed_count == 0 else "completed_with_errors"
    task.completed_at = utc_now()
    db.add(TaskLog(task_id=task.id, message=(
        f"任务完成：发现 {totals[3]} 篇，日期过滤 {totals[4]} 篇，关键词跳过 {totals[5]} 篇，"
        f"保存 {totals[0]} 篇，去重 {totals[1]} 篇，结构化 {structured} 条，失败 {task.failed_count} 篇"
    )))
    db.commit()
