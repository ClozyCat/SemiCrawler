from app.crawl4ai import Crawl4AIClient, Crawl4AIError


def test_extract_sends_browser_crawl_request_and_reads_fit_markdown(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": "https://example.com/final",
                        "metadata": {"title": "项目开工"},
                        "markdown": {
                            "fit_markdown": "2026年8月21日，先进封装项目正式开工。",
                            "raw_markdown": "完整页面",
                        },
                    }
                ],
            }

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.crawl4ai.httpx.post", fake_post)
    page = Crawl4AIClient(
        base_url="http://crawl4ai:11235/",
        api_token="crawl-secret",
        enabled=True,
        min_content_chars=10,
    ).extract("https://example.com/start")

    assert captured["url"] == "http://crawl4ai:11235/crawl"
    assert captured["headers"]["Authorization"] == "Bearer crawl-secret"
    assert captured["json"]["urls"] == ["https://example.com/start"]
    assert captured["json"]["crawler_config"]["params"]["check_robots_txt"] is True
    assert captured["json"]["crawler_config"]["params"]["cache_mode"] == "bypass"
    assert str(page.url) == "https://example.com/final"
    assert page.title == "项目开工"
    assert page.text.startswith("2026年8月21日")


def test_extract_uses_raw_markdown_when_fit_markdown_is_empty(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": "https://example.com/article",
                        "markdown": {
                            "fit_markdown": "",
                            "raw_markdown": "这是 Crawl4AI 返回的完整正文。",
                        },
                    }
                ],
            }

    monkeypatch.setattr("app.crawl4ai.httpx.post", lambda *args, **kwargs: Response())
    page = Crawl4AIClient(enabled=True, min_content_chars=5).extract(
        "https://example.com/article"
    )
    assert page.text == "这是 Crawl4AI 返回的完整正文。"


def test_extract_rejects_short_or_blocked_pages(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": "https://example.com/article",
                        "markdown": "Access Denied - verify you are human " * 8,
                    }
                ],
            }

    monkeypatch.setattr("app.crawl4ai.httpx.post", lambda *args, **kwargs: Response())
    try:
        Crawl4AIClient(enabled=True, min_content_chars=10).extract(
            "https://example.com/article"
        )
    except Crawl4AIError as exc:
        assert "拦截页面" in str(exc)
    else:
        raise AssertionError("expected blocked page to be rejected")


def test_disabled_client_does_not_make_http_request(monkeypatch):
    monkeypatch.setattr(
        "app.crawl4ai.httpx.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP should not be called")
        ),
    )
    try:
        Crawl4AIClient(enabled=False).extract("https://example.com/article")
    except Crawl4AIError as exc:
        assert "未启用" in str(exc)
    else:
        raise AssertionError("expected disabled client error")


def test_client_rejects_private_network_target_before_http(monkeypatch):
    monkeypatch.setattr(
        "app.crawl4ai.httpx.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP should not be called")
        ),
    )
    try:
        Crawl4AIClient(enabled=True).extract("http://127.0.0.1/private")
    except Crawl4AIError as exc:
        assert "非公网" in str(exc)
    else:
        raise AssertionError("expected private network URL to be rejected")
