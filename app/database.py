from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    configured = os.getenv("SEMICRAWLER_DATABASE_URL")
    if configured:
        return configured
    data_dir = Path(os.getenv("SEMICRAWLER_DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'semi_crawler.db').as_posix()}"


engine = create_engine(_database_url(), connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session


def _normalized_url(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def _raw_articles_needs_rebuild(connection) -> bool:
    inspector = inspect(connection)
    if "raw_articles" not in inspector.get_table_names():
        return False
    columns = {column["name"] for column in inspector.get_columns("raw_articles")}
    if not {"source_item_key", "content_kind", "raw_payload_json"}.issubset(columns):
        return True
    return any(
        set(item.get("column_names") or []) == {"canonical_url"}
        for item in inspector.get_unique_constraints("raw_articles")
    )


def _rebuild_raw_articles_sqlite() -> None:
    """Replace the legacy URL-unique table atomically and verify its invariants."""
    with engine.connect() as connection:
        if not _raw_articles_needs_rebuild(connection):
            return
        if connection.dialect.name != "sqlite":
            raise RuntimeError("raw_articles 唯一约束迁移当前仅支持 SQLite，请先执行数据库专用迁移")

        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                before = connection.execute(text("SELECT COUNT(*) FROM raw_articles")).scalar_one()
                connection.connection.driver_connection.create_function("normalize_item_key", 1, _normalized_url)
                connection.execute(text("""
                    CREATE TABLE raw_articles_v2 (
                        id INTEGER NOT NULL PRIMARY KEY,
                        source_id INTEGER NOT NULL REFERENCES sources(id),
                        task_id INTEGER REFERENCES collection_tasks(id),
                        canonical_url VARCHAR(1000) NOT NULL,
                        source_item_key VARCHAR(1000) NOT NULL,
                        content_kind VARCHAR(30) NOT NULL DEFAULT 'article',
                        raw_payload_json TEXT NOT NULL DEFAULT '{}',
                        title VARCHAR(500) NOT NULL,
                        published_at DATE,
                        published_text VARCHAR(200),
                        body TEXT NOT NULL,
                        content_hash VARCHAR(64) NOT NULL,
                        status VARCHAR(30) NOT NULL DEFAULT 'pending',
                        error_message TEXT,
                        model_name VARCHAR(200),
                        llm_output TEXT,
                        collected_at DATETIME NOT NULL,
                        CONSTRAINT uq_raw_articles_source_item_key UNIQUE (source_id, source_item_key)
                    )
                """))
                connection.execute(text("""
                    INSERT INTO raw_articles_v2 (
                        id, source_id, task_id, canonical_url, source_item_key, content_kind,
                        raw_payload_json, title, published_at, published_text, body, content_hash,
                        status, error_message, model_name, llm_output, collected_at
                    )
                    SELECT id, source_id, task_id, canonical_url, normalize_item_key(canonical_url),
                           'article', '{}', title, published_at, published_text, body, content_hash,
                           status, error_message, model_name, llm_output, collected_at
                    FROM raw_articles
                """))
                after = connection.execute(text("SELECT COUNT(*) FROM raw_articles_v2")).scalar_one()
                if before != after:
                    raise RuntimeError(f"raw_articles 迁移记录数不一致: {before} != {after}")
                connection.execute(text("DROP TABLE raw_articles"))
                connection.execute(text("ALTER TABLE raw_articles_v2 RENAME TO raw_articles"))
                connection.execute(text("CREATE INDEX ix_raw_articles_source_id ON raw_articles (source_id)"))
                connection.execute(text("CREATE INDEX ix_raw_articles_task_id ON raw_articles (task_id)"))
                connection.execute(text("CREATE INDEX ix_raw_articles_content_hash ON raw_articles (content_hash)"))
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            connection.commit()
            if violations:
                raise RuntimeError(f"raw_articles 迁移后外键校验失败: {violations[:3]}")


def migrate_legacy_database() -> None:
    """Add post-v0.1 audit columns without requiring a migration CLI for local installs."""
    additions = {
        "raw_articles": {"model_name": "VARCHAR(200)", "llm_output": "TEXT"},
        "structured_records": {
            "amount_value": "VARCHAR(100)", "amount_currency": "VARCHAR(30)",
            "amount_unit": "VARCHAR(30)", "amount_note": "VARCHAR(200)",
        },
        "model_settings": {"keyword_config_json": "TEXT DEFAULT '[]'", "keyword_filter_enabled": "BOOLEAN DEFAULT 0"},
        "collection_tasks": {"keyword_filter_enabled": "BOOLEAN DEFAULT 0", "auto_structure_enabled": "BOOLEAN DEFAULT 0", "keyword_config_json": "TEXT DEFAULT '[]'"},
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, columns in additions.items():
            if table not in inspector.get_table_names():
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
        tables = set(inspector.get_table_names())
        if {"collection_tasks", "raw_articles"}.issubset(tables):
            # Older versions counted discovered links as fetched articles. The UI now reports persisted raw articles.
            connection.execute(text("""
                UPDATE collection_tasks
                SET fetched_count = (
                    SELECT COUNT(*) FROM raw_articles WHERE raw_articles.task_id = collection_tasks.id
                )
            """))
    _rebuild_raw_articles_sqlite()
