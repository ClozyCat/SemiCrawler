from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import date
from io import BytesIO
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.orm import Session

from .constants import DEFAULT_START_DATE, INFO_TYPES
from .analytics import build_analytics
from .crawler import run_task, test_source
from .database import Base, SessionLocal, engine, get_db, migrate_legacy_database
from .exporting import make_csv, make_xlsx
from .models import CollectionTask, ExportRecord, ModelSetting, RawArticle, Source, SourceVersion, StructuredRecord, TaskLog, utc_now
from .llm import structure_article
from .schemas import (AnalyticsOverview, AppMeta, ArticleList, ArticleRead, LogRead, ModelSettingRead, ModelSettingUpdate, RecordDetail,
                      RecordList, RecordRead, RecordUpdate, SourceCreate, SourceRead, SourceTest, SourceUpdate,
                      StructureResult, TaskCreate, TaskRead, DeleteIds)
from .seed import seed_default_sources
from .source_config import validate_source_config


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    migrate_legacy_database()
    with SessionLocal() as db:
        seed_default_sources(db)
    yield


app = FastAPI(title="芯闻采集台 API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def source_read(source: Source) -> SourceRead:
    return SourceRead.model_validate({
        "id": source.id, "name": source.name, "base_url": source.base_url,
        "enabled": source.enabled, "builtin": source.builtin,
        "config": json.loads(source.config_json), "created_at": source.created_at, "updated_at": source.updated_at,
    })


def task_read(task: CollectionTask) -> TaskRead:
    progress = {"queued": 0, "running": 50, "completed": 100, "failed": 100}.get(task.status, 0)
    return TaskRead(
        id=task.id, status=task.status, start_date=task.start_date,
        source_ids=json.loads(task.source_ids_json), source_snapshot=json.loads(task.source_snapshot_json),
        fetched_count=task.fetched_count, deduplicated_count=task.deduplicated_count,
        structured_count=task.structured_count, failed_count=task.failed_count, progress=progress,
        created_at=task.created_at, started_at=task.started_at, completed_at=task.completed_at,
        keyword_filter_enabled=bool(task.keyword_filter_enabled), auto_structure_enabled=bool(task.auto_structure_enabled),
        keyword_config=json.loads(task.keyword_config_json or "[]"),
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta", response_model=AppMeta)
def meta() -> AppMeta:
    return AppMeta(default_start_date=DEFAULT_START_DATE, info_types=INFO_TYPES)


@app.get("/api/sources", response_model=list[SourceRead])
def list_sources(db: Session = Depends(get_db)):
    return [source_read(item) for item in db.scalars(select(Source).order_by(Source.id)).all()]


@app.post("/api/sources", response_model=SourceRead, status_code=201)
def create_source(payload: SourceCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Source).where(Source.name == payload.name)):
        raise HTTPException(409, "来源名称已存在")
    try:
        validate_source_config(str(payload.base_url), payload.config)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    source = Source(name=payload.name, base_url=str(payload.base_url), enabled=payload.enabled, builtin=False,
                    config_json=json.dumps(payload.config, ensure_ascii=False))
    db.add(source)
    db.flush()
    db.add(SourceVersion(source_id=source.id, version=1, config_json=source.config_json))
    db.commit()
    db.refresh(source)
    return source_read(source)


@app.patch("/api/sources/{source_id}", response_model=SourceRead)
def update_source(source_id: int, payload: SourceUpdate, db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(404, "来源不存在")
    values = payload.model_dump(exclude_unset=True)
    candidate_base = str(values.get("base_url") or source.base_url)
    candidate_config = values.get("config") or json.loads(source.config_json)
    try:
        validate_source_config(candidate_base, candidate_config)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if "base_url" in values:
        values["base_url"] = str(values["base_url"])
    if "config" in values:
        values["config_json"] = json.dumps(values.pop("config"), ensure_ascii=False)
    for key, value in values.items():
        setattr(source, key, value)
    if "config_json" in values:
        version = (db.scalar(select(func.max(SourceVersion.version)).where(SourceVersion.source_id == source.id)) or 0) + 1
        db.add(SourceVersion(source_id=source.id, version=version, config_json=source.config_json))
    db.commit()
    db.refresh(source)
    return source_read(source)


@app.post("/api/sources/test")
def test_source_config(payload: SourceTest):
    try:
        return test_source(str(payload.base_url), payload.config)
    except Exception as exc:
        raise HTTPException(422, f"试抓取失败: {exc}") from exc


@app.post("/api/tasks", response_model=TaskRead, status_code=201)
def create_task(payload: TaskCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    sources = db.scalars(select(Source).where(Source.id.in_(payload.source_ids), Source.enabled.is_(True))).all()
    if len(sources) != len(payload.source_ids):
        raise HTTPException(400, "部分来源不存在或未启用")
    snapshots = [{"id": item.id, "name": item.name, "base_url": item.base_url,
                  "config": json.loads(item.config_json)} for item in sources]
    now = utc_now()
    task = CollectionTask(
        status="queued", start_date=payload.start_date,
        source_ids_json=json.dumps(payload.source_ids),
        source_snapshot_json=json.dumps(snapshots, ensure_ascii=False),
        started_at=None, completed_at=None,
        keyword_filter_enabled=payload.keyword_filter_enabled,
        auto_structure_enabled=payload.auto_structure_enabled,
        keyword_config_json=json.dumps(payload.keyword_config, ensure_ascii=False),
    )
    db.add(task)
    db.flush()
    db.add_all([
        TaskLog(task_id=task.id, message=f"已创建任务，资讯起始日期 {payload.start_date.isoformat()}"),
        TaskLog(task_id=task.id, message=f"已保存 {len(sources)} 个来源的配置快照"),
        TaskLog(task_id=task.id, level="notice", message="已排队执行真实 HTTP 采集"),
    ])
    db.commit()
    db.refresh(task)
    background_tasks.add_task(_run_task_background, task.id)
    return task_read(task)


def _run_task_background(task_id: int) -> None:
    with SessionLocal() as session:
        task = session.get(CollectionTask, task_id)
        if task:
            try:
                run_task(session, task)
            except Exception as exc:
                task.status = "failed"
                task.completed_at = utc_now()
                session.add(TaskLog(task_id=task.id, level="error", message=f"任务失败: {exc}"))
                session.commit()


@app.get("/api/tasks", response_model=list[TaskRead])
def list_tasks(db: Session = Depends(get_db)):
    return [task_read(item) for item in db.scalars(select(CollectionTask).order_by(CollectionTask.id.desc())).all()]


@app.get("/api/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(CollectionTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task_read(task)


@app.get("/api/tasks/{task_id}/logs", response_model=list[LogRead])
def get_logs(task_id: int, db: Session = Depends(get_db)):
    if not db.get(CollectionTask, task_id):
        raise HTTPException(404, "任务不存在")
    return db.scalars(select(TaskLog).where(TaskLog.task_id == task_id).order_by(TaskLog.id)).all()


@app.delete("/api/tasks")
def delete_tasks(payload: DeleteIds, db: Session = Depends(get_db)):
    task_ids = set(payload.ids)
    tasks = db.scalars(select(CollectionTask).where(CollectionTask.id.in_(task_ids))).all()
    if len(tasks) != len(task_ids):
        raise HTTPException(404, "部分采集任务不存在")
    article_ids = set(db.scalars(select(RawArticle.id).where(RawArticle.task_id.in_(task_ids))).all())
    db.execute(delete(StructuredRecord).where(or_(StructuredRecord.task_id.in_(task_ids), StructuredRecord.article_id.in_(article_ids))))
    db.execute(delete(RawArticle).where(RawArticle.task_id.in_(task_ids)))
    db.execute(delete(TaskLog).where(TaskLog.task_id.in_(task_ids)))
    db.execute(delete(CollectionTask).where(CollectionTask.id.in_(task_ids)))
    db.commit()
    return {"deleted": len(task_ids)}


def records_query(region: str | None, info_type: list[str] | None, source: str | None,
                  date_from: date | None, date_to: date | None, q: str | None = None):
    query = select(StructuredRecord)
    if region:
        query = query.where(StructuredRecord.region.contains(region))
    if info_type:
        query = query.where(StructuredRecord.info_type.in_(info_type))
    if source:
        query = query.where(StructuredRecord.source_name == source)
    if date_from:
        query = query.where(StructuredRecord.event_date >= date_from)
    if date_to:
        query = query.where(StructuredRecord.event_date <= date_to)
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.outerjoin(RawArticle, RawArticle.id == StructuredRecord.article_id).where(or_(
            StructuredRecord.region.ilike(term), StructuredRecord.organization.ilike(term),
            StructuredRecord.company_name.ilike(term), StructuredRecord.info_type.ilike(term),
            StructuredRecord.investment_amount.ilike(term), StructuredRecord.project_name.ilike(term),
            StructuredRecord.source_name.ilike(term), StructuredRecord.original_url.ilike(term),
            StructuredRecord.details.ilike(term), RawArticle.title.ilike(term), RawArticle.body.ilike(term),
            cast(StructuredRecord.event_date, String).ilike(term),
        ))
    return query


@app.get("/api/records", response_model=RecordList)
def list_records(
    q: str | None = None, region: str | None = None, info_type: list[str] | None = Query(None), source: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    base = records_query(region, info_type, source, date_from, date_to, q)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    items = db.scalars(base.order_by(StructuredRecord.event_date.desc(), StructuredRecord.id.desc()).offset(offset).limit(limit)).all()
    return RecordList(items=[RecordRead.model_validate(item) for item in items], total=total)


@app.get("/api/analytics/overview", response_model=AnalyticsOverview)
def analytics_overview(
    q: str | None = None, region: str | None = None, info_type: list[str] | None = Query(None), source: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
    max_nodes: int = Query(60, ge=10, le=150), max_keywords: int = Query(30, ge=10, le=100),
    db: Session = Depends(get_db),
):
    records = db.scalars(records_query(region, info_type, source, date_from, date_to, q)).unique().all()
    article_ids = {record.article_id for record in records if record.article_id}
    articles = {article.id: article for article in db.scalars(
        select(RawArticle).where(RawArticle.id.in_(article_ids))).all()} if article_ids else {}
    setting = db.get(ModelSetting, 1)
    keyword_config = json.loads(setting.keyword_config_json or "[]") if setting else []
    allowed_keywords = []
    for row in keyword_config:
        allowed_keywords.extend(part.strip() for part in re.split(r"[,，、;；\s]+", str(row.get("keywords") or "")) if len(part.strip()) > 1)
    allowed_keywords = list(dict.fromkeys(allowed_keywords))
    return build_analytics(records, articles, max_nodes=max_nodes, max_keywords=max_keywords,
                           allowed_keywords=allowed_keywords)


@app.delete("/api/records")
def delete_records(payload: DeleteIds, db: Session = Depends(get_db)):
    ids = set(payload.ids)
    count = db.scalar(select(func.count()).select_from(StructuredRecord).where(StructuredRecord.id.in_(ids))) or 0
    if count != len(ids):
        raise HTTPException(404, "部分结构化记录不存在")
    db.execute(delete(StructuredRecord).where(StructuredRecord.id.in_(ids)))
    db.commit()
    return {"deleted": len(ids)}


def article_read(article: RawArticle, source_name: str, record_count: int) -> ArticleRead:
    return ArticleRead.model_validate({
        **{key: getattr(article, key) for key in (
            "id", "source_id", "task_id", "canonical_url", "title", "published_at", "published_text",
            "body", "status", "error_message", "model_name", "collected_at")},
        "source_name": source_name, "record_count": record_count,
    })


@app.get("/api/articles", response_model=ArticleList)
def list_articles(
    q: str | None = None, status: str | None = None,
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    count_column = func.count(StructuredRecord.id).label("record_count")
    query = (select(RawArticle, Source.name, count_column).join(Source, Source.id == RawArticle.source_id)
             .outerjoin(StructuredRecord, StructuredRecord.article_id == RawArticle.id).group_by(RawArticle.id, Source.name))
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.where(or_(RawArticle.title.ilike(term), RawArticle.body.ilike(term),
                                RawArticle.canonical_url.ilike(term), RawArticle.published_text.ilike(term),
                                Source.name.ilike(term)))
    if status:
        query = query.where(RawArticle.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(RawArticle.collected_at.desc(), RawArticle.id.desc()).offset(offset).limit(limit)).all()
    return ArticleList(items=[article_read(article, source_name, record_count) for article, source_name, record_count in rows], total=total)


@app.get("/api/articles/{article_id}", response_model=ArticleRead)
def get_article(article_id: int, db: Session = Depends(get_db)):
    article = db.get(RawArticle, article_id)
    if not article:
        raise HTTPException(404, "原文不存在")
    source_name = db.scalar(select(Source.name).where(Source.id == article.source_id)) or ""
    record_count = db.scalar(select(func.count(StructuredRecord.id)).where(StructuredRecord.article_id == article.id)) or 0
    return article_read(article, source_name, record_count)


@app.delete("/api/articles")
def delete_articles(payload: DeleteIds, db: Session = Depends(get_db)):
    ids = set(payload.ids)
    articles = db.scalars(select(RawArticle).where(RawArticle.id.in_(ids))).all()
    if len(articles) != len(ids):
        raise HTTPException(404, "部分原始数据不存在")
    db.execute(delete(StructuredRecord).where(StructuredRecord.article_id.in_(ids)))
    db.execute(delete(RawArticle).where(RawArticle.id.in_(ids)))
    db.commit()
    return {"deleted": len(ids)}


@app.post("/api/articles/{article_id}/structure", response_model=StructureResult)
def structure_raw_article(article_id: int, db: Session = Depends(get_db)):
    article = db.get(RawArticle, article_id)
    if not article:
        raise HTTPException(404, "原文不存在")
    existing = db.scalar(select(func.count(StructuredRecord.id)).where(StructuredRecord.article_id == article.id)) or 0
    if existing or article.status == "completed":
        raise HTTPException(409, "该原文已经完成结构化")
    setting = db.get(ModelSetting, 1)
    # The auto-structure switch only controls task execution. Manual history
    # actions are available whenever the model API credentials are configured.
    if not setting or not setting.api_key:
        raise HTTPException(409, "请先在“API配置”中配置 API Key")
    created = structure_article(db, article, setting)
    db.commit()
    if article.status == "review_required":
        raise HTTPException(502, f"结构化失败: {article.error_message or '模型输出无法通过校验'}")
    return StructureResult(article_id=article.id, created_count=created, status=article.status)


@app.patch("/api/records/{record_id}", response_model=RecordRead)
def update_record(record_id: int, payload: RecordUpdate, db: Session = Depends(get_db)):
    record = db.get(StructuredRecord, record_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, key, value)
    record.status = "completed"
    db.commit()
    db.refresh(record)
    return record


@app.get("/api/records/{record_id}", response_model=RecordDetail)
def get_record_detail(record_id: int, db: Session = Depends(get_db)):
    record = db.get(StructuredRecord, record_id)
    if not record:
        raise HTTPException(404, "记录不存在")
    article = db.get(RawArticle, record.article_id) if record.article_id else None
    data = RecordRead.model_validate(record).model_dump()
    data.update(evidence=json.loads(record.evidence_json or "{}"), confidence=json.loads(record.confidence_json or "{}"),
                article=None if not article else {"id": article.id, "title": article.title, "body": article.body,
                    "published_text": article.published_text, "collected_at": article.collected_at,
                    "model_name": article.model_name, "status": article.status, "error_message": article.error_message})
    return data


def setting_read(setting: ModelSetting) -> ModelSettingRead:
    hint = ("*" * max(len(setting.api_key) - 4, 0) + setting.api_key[-4:]) if setting.api_key else ""
    return ModelSettingRead(base_url=setting.base_url, model_name=setting.model_name, enabled=setting.enabled,
                            has_api_key=bool(setting.api_key), api_key_hint=hint,
                            keyword_config=json.loads(setting.keyword_config_json or "[]"),
                            keyword_filter_enabled=bool(setting.keyword_filter_enabled))


@app.get("/api/settings/model", response_model=ModelSettingRead)
def get_model_setting(db: Session = Depends(get_db)):
    setting = db.get(ModelSetting, 1)
    if not setting:
        setting = ModelSetting(id=1); db.add(setting); db.commit(); db.refresh(setting)
    return setting_read(setting)


@app.put("/api/settings/model", response_model=ModelSettingRead)
def update_model_setting(payload: ModelSettingUpdate, db: Session = Depends(get_db)):
    setting = db.get(ModelSetting, 1) or ModelSetting(id=1)
    setting.base_url = str(payload.base_url).rstrip("/"); setting.model_name = payload.model_name
    setting.enabled = payload.enabled
    setting.keyword_config_json = json.dumps(payload.keyword_config, ensure_ascii=False)
    setting.keyword_filter_enabled = payload.keyword_filter_enabled
    if payload.api_key is not None and payload.api_key.strip():
        setting.api_key = payload.api_key.strip()
    db.add(setting); db.commit(); db.refresh(setting)
    return setting_read(setting)


@app.get("/api/exports")
def export_records(
    format: str = Query("xlsx", pattern="^(csv|xlsx)$"), q: str | None = None, region: str | None = None,
    info_type: list[str] | None = Query(None), source: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
    columns: str = Query("default", pattern="^(default|audit)$"), db: Session = Depends(get_db),
):
    filters = {"region": region, "info_type": info_type, "source": source,
               "date_from": date_from.isoformat() if date_from else None, "date_to": date_to.isoformat() if date_to else None}
    records = db.scalars(records_query(region, info_type, source, date_from, date_to, q).order_by(StructuredRecord.event_date.desc())).all()
    filename = f"半导体资讯结构化结果.{format}"
    content = make_csv(records, audit=columns == "audit") if format == "csv" else make_xlsx(records, audit=columns == "audit")
    media_type = "text/csv; charset=utf-8" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    db.add(ExportRecord(format=format, row_count=len(records), filters_json=json.dumps(filters, ensure_ascii=False), filename=filename))
    db.commit()
    return StreamingResponse(BytesIO(content), media_type=media_type,
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})
