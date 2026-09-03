from app.anysearch import AnySearchClient, AnySearchError


def test_search_sends_anysearch_query_and_domain_terms(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 0,
                "data": {
                    "results": [
                        {
                            "title": "先进封装项目开工",
                            "url": "https://example.com/project",
                            "snippet": "项目于近日正式开工",
                            "published_at": "2026-08-21",
                        }
                    ],
                    "metadata": {"total_results": 1},
                },
            }

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.anysearch.httpx.post", fake_post)
    items = AnySearchClient("as_sk-test").search(
        "先进封装 项目", num=20, domains=["Example.COM", "example.com"]
    )

    assert captured["url"] == "https://api.anysearch.com/v1/search"
    assert captured["headers"]["Authorization"] == "Bearer as_sk-test"
    assert captured["json"] == {
        "query": "先进封装 项目 site:example.com",
        "max_results": 20,
        "content_types": ["web", "news"],
    }
    assert str(items[0].url) == "https://example.com/project"
    assert items[0].published_date == "2026-08-21"


def test_extract_reads_anysearch_data_payload(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 0,
                "data": {
                    "url": "https://example.com/project",
                    "title": "项目开工",
                    "content": "正文内容",
                },
            }

    monkeypatch.setattr("app.anysearch.httpx.post", lambda *args, **kwargs: Response())
    page = AnySearchClient().extract("https://example.com/project")
    assert page.title == "项目开工"
    assert page.text == "正文内容"


def test_search_surfaces_anysearch_error_payload(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 401, "message": "invalid_api_key"}

    monkeypatch.setattr("app.anysearch.httpx.post", lambda *args, **kwargs: Response())
    try:
        AnySearchClient("invalid").search("先进封装")
    except AnySearchError as exc:
        assert "401" in str(exc)
    else:
        raise AssertionError("AnySearchError was not raised")
