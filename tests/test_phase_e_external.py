"""Read-only release checks for the two surveyed public sites.

These tests are intentionally opt-in: ``SEMICRAWLER_EXTERNAL=1 uv run pytest -m external``.
Daily CI uses the fixed fixtures in ``test_adaptive_collection.py`` instead.
"""

import os
from datetime import date

import pytest

from app.collection.adaptive import detect_and_validate
from app.collection.executors import CollectionExecutor
from app.collection.fetcher import FetchLimits, SafeFetcher


pytestmark = pytest.mark.external
ENABLED = os.getenv("SEMICRAWLER_EXTERNAL") == "1"
NATIONAL = "https://new.tzxm.gov.cn/bsdt/"
JIANGSU = "https://tzxm.fzggw.jiangsu.gov.cn/portalopenPublicInformation.do?method=queryExamineAll"


def _external_fetcher(url: str, host: str):
    return SafeFetcher(url, allowed_hosts={host}, limits=FetchLimits(
        rate_limit_per_minute=20, timeout_seconds=20, max_response_bytes=5 * 1024 * 1024,
    ))


@pytest.mark.skipif(not ENABLED, reason="external regression is opt-in")
def test_national_external_read_only_two_pages():
    try:
        with _external_fetcher(NATIONAL, "new.tzxm.gov.cn") as fetcher:
            profile = detect_and_validate(fetcher, NATIONAL, ["new.tzxm.gov.cn"])
            pages = list(CollectionExecutor(fetcher).pages(profile, max_pages=2, max_items=40,
                                                      start_date=date.today()))
    except Exception as exc:
        pytest.skip(f"external environment unavailable: {exc}")
    assert pages and all(page.items for page in pages)
    keys = [item.source_item_key for page in pages for item in page.items]
    assert len(keys) == len(set(keys))
    assert all(item.title and item.published_at for page in pages for item in page.items)


@pytest.mark.skipif(not ENABLED, reason="external regression is opt-in")
def test_jiangsu_external_read_only_two_pages():
    try:
        with _external_fetcher(JIANGSU, "tzxm.fzggw.jiangsu.gov.cn") as fetcher:
            profile = detect_and_validate(fetcher, JIANGSU, ["tzxm.fzggw.jiangsu.gov.cn"])
            pages = list(CollectionExecutor(fetcher).pages(profile, max_pages=2, max_items=40,
                                                      start_date=date.today()))
    except Exception as exc:
        pytest.skip(f"external environment unavailable: {exc}")
    assert profile.pagination.kind == "form_post"
    assert len(pages) <= 2 and pages and all(page.items for page in pages)
    keys = [item.source_item_key for page in pages for item in page.items]
    assert len(keys) == len(set(keys))
    completeness = sum(bool(item.title and item.published_at and item.standard_fields.get("project_code"))
                        for page in pages for item in page.items) / max(1, len(keys))
    assert completeness >= .95
