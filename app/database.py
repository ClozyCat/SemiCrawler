from __future__ import annotations

import os
from pathlib import Path

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


def migrate_legacy_database() -> None:
    """Add post-v0.1 audit columns without requiring a migration CLI for local installs."""
    additions = {
        "raw_articles": {
            "source_item_key": "VARCHAR(1000) DEFAULT ''",
            "content_kind": "VARCHAR(30) DEFAULT 'article'",
            "raw_payload_json": "TEXT DEFAULT '{}'",
            "model_name": "VARCHAR(200)",
            "llm_output": "TEXT",
        },
        "structured_records": {
            "amount_value": "VARCHAR(100)",
            "amount_currency": "VARCHAR(30)",
            "amount_unit": "VARCHAR(30)",
            "amount_note": "VARCHAR(200)",
        },
        "model_settings": {
            "keyword_config_json": "TEXT DEFAULT '[]'",
            "keyword_filter_enabled": "BOOLEAN DEFAULT 0",
        },
        "collection_tasks": {
            "keyword_filter_enabled": "BOOLEAN DEFAULT 0",
            "auto_structure_enabled": "BOOLEAN DEFAULT 0",
            "keyword_config_json": "TEXT DEFAULT '[]'",
        },
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, columns in additions.items():
            if table not in inspector.get_table_names():
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                    )
        if "raw_articles" in inspector.get_table_names():
            connection.execute(
                text("""
                UPDATE raw_articles
                SET source_item_key = canonical_url
                WHERE source_item_key IS NULL OR source_item_key = ''
            """)
            )
        tables = set(inspector.get_table_names())
        if {"collection_tasks", "raw_articles"}.issubset(tables):
            # Older versions counted discovered links as fetched articles. The UI now reports persisted raw articles.
            connection.execute(
                text("""
                UPDATE collection_tasks
                SET fetched_count = (
                    SELECT COUNT(*) FROM raw_articles WHERE raw_articles.task_id = collection_tasks.id
                )
            """)
            )
