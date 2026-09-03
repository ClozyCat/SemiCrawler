from datetime import date

from app.tavily import TavilyClient


def test_search_sends_date_and_site_query(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "先进封装项目开工",
                        "url": "https://example.com/project",
                        "content": "项目于近日正式开工",
                        "score": 0.9,
                    }
                ]
            }

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.tavily.httpx.post", fake_post)
    items = TavilyClient("tvly-test").search(
        "site:example.com 先进封装 项目", num=20, start_date=date(2026, 8, 1)
    )

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["json"]["query"].startswith("site:example.com")
    assert captured["json"]["start_date"] == "2026-08-01"
    assert captured["json"]["max_results"] == 20
    assert str(items[0].url) == "https://example.com/project"


def test_extract_returns_markdown_body(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "url": "https://example.com/project",
                        "raw_content": "# 项目开工\n\n正文内容",
                    }
                ]
            }

    monkeypatch.setattr("app.tavily.httpx.post", lambda *args, **kwargs: Response())
    page = TavilyClient("tvly-test").extract("https://example.com/project")

    assert page.text.startswith("# 项目开工")
