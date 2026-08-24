from __future__ import annotations

import socket

import httpx
import pytest
from sqlalchemy import func, inspect, select, text

from app.collection.fetcher import FetchLimits, ResponseTooLargeError, SafeFetcher
from app.collection.safety import UnsafeUrlError, validate_url
from app.database import SessionLocal, engine, migrate_legacy_database
from app.models import RawArticle, Source
from app.source_config import SourceConfigV2, validate_source_config


def _public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def test_v2_config_allows_only_explicit_hosts():
    config = {
        "version": 2,
        "entry_urls": ["https://data.example.org/public"],
        "allowed_hosts": ["data.example.org"],
        "content_hint": "公开项目审批结果",
    }
    parsed = validate_source_config("https://example.org", config)
    assert isinstance(parsed, SourceConfigV2)
    assert parsed.limits.max_items == 5000

    config["entry_urls"] = ["https://other.example.org/public"]
    with pytest.raises(ValueError, match="主机已获允许"):
        validate_source_config("https://example.org", config)


def test_url_safety_rejects_private_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))
    ])
    with pytest.raises(UnsafeUrlError, match="非公网"):
        validate_url("https://example.org/data")
    with pytest.raises(UnsafeUrlError, match="仅允许"):
        validate_url("file:///etc/passwd")


def test_safe_fetcher_treats_html_robots_as_invalid(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>not found</html>")
        return httpx.Response(200, text="<table><tr><td>ok</td></tr></table>")

    with SafeFetcher("https://example.org/public", transport=httpx.MockTransport(handler), sleeper=lambda _: None) as fetcher:
        response = fetcher.fetch("https://example.org/public")
    assert response.robots_status == "invalid_content_type"
    assert "<table>" in response.text


def test_safe_fetcher_enforces_streaming_size_limit(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * 20)

    limits = FetchLimits(max_response_bytes=10)
    with SafeFetcher("https://example.org/public", limits=limits,
                     transport=httpx.MockTransport(handler), sleeper=lambda _: None) as fetcher:
        with pytest.raises(ResponseTooLargeError):
            fetcher.fetch("https://example.org/public")


def test_same_source_url_can_store_multiple_stable_items():
    with SessionLocal() as db:
        source = Source(name="公示站", base_url="https://example.org", config_json="{}")
        db.add(source)
        db.flush()
        common = dict(source_id=source.id, canonical_url="https://example.org/public", title="项目",
                      body="项目字段", content_hash="a" * 64)
        db.add_all([
            RawArticle(**common, source_item_key="project-1:item-a"),
            RawArticle(**{**common, "content_hash": "b" * 64}, source_item_key="project-1:item-b"),
        ])
        db.commit()
        assert db.scalar(select(func.count(RawArticle.id))) == 2


def test_legacy_raw_article_table_is_rebuilt_without_data_loss():
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO sources (id, name, base_url, enabled, builtin, config_json, created_at, updated_at)
            VALUES (1, '旧来源', 'https://example.org', 1, 0, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))
        connection.execute(text("DROP TABLE raw_articles"))
        connection.execute(text("""
            CREATE TABLE raw_articles (
                id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, task_id INTEGER,
                canonical_url VARCHAR(1000) NOT NULL UNIQUE, title VARCHAR(500) NOT NULL,
                published_at DATE, published_text VARCHAR(200), body TEXT NOT NULL,
                content_hash VARCHAR(64) NOT NULL, status VARCHAR(30) NOT NULL,
                error_message TEXT, model_name VARCHAR(200), llm_output TEXT, collected_at DATETIME NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO raw_articles
            (id, source_id, canonical_url, title, body, content_hash, status, collected_at)
            VALUES (1, 1, 'HTTPS://EXAMPLE.ORG/news/', '旧文章', '正文', 'abc', 'pending', CURRENT_TIMESTAMP)
        """))

    migrate_legacy_database()

    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT canonical_url, source_item_key, content_kind, raw_payload_json FROM raw_articles"
        )).one()
        assert row.source_item_key == "https://example.org/news"
        assert row.content_kind == "article"
        assert row.raw_payload_json == "{}"
        constraints = inspect(connection).get_unique_constraints("raw_articles")
        assert any(item["column_names"] == ["source_id", "source_item_key"] for item in constraints)
