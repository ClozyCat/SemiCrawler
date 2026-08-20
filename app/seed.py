import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Source


DEFAULT_SOURCES = [
    {
        "name": "全球半导体观察（DRAMx）",
        "base_url": "https://www.dramx.com",
        "config": {
            "entry_urls": ["https://www.dramx.com/News/"],
            "article_url_pattern": "/News/[^/]+/\\d{8}-\\d+\\.html$",
            "selectors": {"list_links": "a", "title": "h1", "date": ".newstitle-bottom", "content": ".newspage-cont"},
            "pagination": {"next_page_selector": "a.next", "max_pages": 20},
            "request": {"rate_limit_per_minute": 20, "timeout_seconds": 20},
        },
    },
    {
        "name": "半导体产业网",
        "base_url": "https://www.casmita.com",
        "config": {
            "entry_urls": ["https://www.casmita.com/news/list.php?catid=77"],
            "article_url_pattern": "/news/\\d{6}/\\d{2}/\\d+\\.html",
            "selectors": {"list_links": "a", "title": "h1.title", "date": ".info", "content": "#article"},
            "pagination": {"next_page_selector": "a[title='下一页']", "max_pages": 20},
            "request": {"rate_limit_per_minute": 20, "timeout_seconds": 20},
        },
    },
]


def seed_default_sources(db: Session) -> None:
    existing = {item.name: item for item in db.scalars(select(Source)).all()}
    for item in DEFAULT_SOURCES:
        if item["name"] not in existing:
            db.add(Source(
                name=item["name"], base_url=item["base_url"], enabled=True,
                builtin=True, config_json=json.dumps(item["config"], ensure_ascii=False),
            ))
        elif existing[item["name"]].builtin:
            # Keep shipped adapters current across local upgrades.
            existing[item["name"]].base_url = item["base_url"]
            existing[item["name"]].config_json = json.dumps(item["config"], ensure_ascii=False)
    db.commit()
