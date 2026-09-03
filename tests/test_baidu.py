from datetime import date

import pytest

from app.baidu import BaiduClient, BaiduError


def test_search_sends_baidu_query_date_and_domain_filters(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "request_id": "request-1",
                "references": [
                    {
                        "id": 1,
                        "type": "web",
                        "title": "先进封装项目开工",
                        "url": "https://example.com/project",
                        "content": "项目于近日正式开工",
                        "date": "2026-08-21 10:00:00",
                    },
                    {
                        "id": 2,
                        "type": "image",
                        "url": "https://example.com/image.jpg",
                    },
                ],
            }

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.baidu.httpx.post", fake_post)
    items = BaiduClient("baidu-test").search(
        "先进封装 项目",
        num=20,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 9, 3),
        domains=["Example.COM", "example.com"],
    )

    assert captured["url"] == (
        "https://qianfan.baidubce.com/v2/ai_search/web_search"
    )
    assert captured["headers"] == {"Authorization": "Bearer baidu-test"}
    assert captured["json"]["messages"] == [
        {"role": "user", "content": "先进封装 项目"}
    ]
    assert captured["json"]["search_source"] == "baidu_search_v2"
    assert captured["json"]["resource_type_filter"] == [
        {"type": "web", "top_k": 20}
    ]
    assert captured["json"]["search_filter"] == {
        "match": {"site": ["example.com"]},
        "range": {
            "page_time": {"gte": "2026-08-01", "lte": "2026-09-03"}
        },
    }
    assert len(items) == 1
    assert items[0].published_date == "2026-08-21 10:00:00"
    assert items[0].content == "项目于近日正式开工"


def test_search_truncates_query_to_baidu_weighted_limit(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"request_id": "request-1", "references": []}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("app.baidu.httpx.post", fake_post)
    BaiduClient("baidu-test").search("中" * 40)

    assert captured["json"]["messages"][0]["content"] == "中" * 36


def test_search_surfaces_baidu_error_payload(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": "216003", "message": "Authentication error"}

    monkeypatch.setattr("app.baidu.httpx.post", lambda *args, **kwargs: Response())

    with pytest.raises(BaiduError, match="216003"):
        BaiduClient("invalid").search("先进封装")
