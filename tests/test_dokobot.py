from datetime import date
from subprocess import CompletedProcess

from app.dokobot import (
    DokobotClient,
    DokobotError,
    DokobotPage,
    _decode_output,
    build_search_query,
    parse_search_results,
    source_hint_batches,
    source_hint_variants,
)


def test_search_query_uses_date_and_source_domains():
    query = build_search_query(
        "先进封装项目",
        "优先 gov.cn 和 https://example.com/news",
        date(2026, 8, 1),
    )
    assert "after:2026-08-01" in query
    assert "site:gov.cn OR site:example.com" in query


def test_source_hint_variants_split_each_url_into_independent_scopes():
    assert source_hint_variants(
        "https://example.com/news\nhttps://gov.cn/projects\n优先项目公告"
    ) == ["https://example.com/news", "https://gov.cn/projects", "优先项目公告"]


def test_source_hint_batches_groups_at_most_six_urls_for_one_query():
    source_hint = "\n".join(f"https://source-{index}.example/news" for index in range(1, 8))

    assert source_hint_batches(source_hint) == [
        "\n".join(f"https://source-{index}.example/news" for index in range(1, 7)),
        "https://source-7.example/news",
    ]


def test_source_hint_batches_preserves_text_hint_order():
    assert source_hint_batches("说明文字\nhttps://one.example\nhttps://two.example") == [
        "说明文字",
        "https://one.example\nhttps://two.example",
    ]


def test_search_results_extract_direct_and_google_redirect_links():
    text = """
项目正式开工
https://example.com/news/1

[企业宣布扩产](https://www.google.com/url?q=https%3A%2F%2Fcorp.example%2Fnews%2F2&sa=U)
"""
    items = parse_search_results(text, limit=10)
    assert [str(item.link) for item in items] == [
        "https://corp.example/news/2",
        "https://example.com/news/1",
    ]


def test_search_results_use_repeated_dokobot_references():
    text = """
YouTube [9]
---
苏州市人民政府 [17]
https://www.suzhou.gov.cn › news [17]
全国首条硅光芯片量产线落户苏州高新区 [17]
---
[9] https://www.youtube.com/
[17] https://www.suzhou.gov.cn/news/project.html
"""
    items = parse_search_results(text, limit=10)
    assert [(item.title, str(item.link)) for item in items] == [
        (
            "全国首条硅光芯片量产线落户苏州高新区",
            "https://www.suzhou.gov.cn/news/project.html",
        )
    ]


def test_client_search_reads_later_pages_until_result_limit(monkeypatch):
    urls = []

    def fake_read(self, url, *, screens=None):
        urls.append(url)
        if "first=" in url:
            start = int(url.split("first=")[1]) - 1
        else:
            start = int(url.split("start=")[1]) if "start=" in url else 0
        links = "\n".join(
            f"[结果 {index}](https://example.com/news/{index})"
            for index in range(start + 1, start + 11)
        )
        return DokobotPage(title="搜索", url=url, text=links)

    monkeypatch.setattr(DokobotClient, "read", fake_read)
    items = DokobotClient(executable="dokobot").search("先进封装", num=25)

    assert len(items) == 25
    assert urls == [
        "https://www.google.com/search?q=%E5%85%88%E8%BF%9B%E5%B0%81%E8%A3%85&num=10",
        "https://www.google.com/search?q=%E5%85%88%E8%BF%9B%E5%B0%81%E8%A3%85&num=10&start=10",
        "https://www.google.com/search?q=%E5%85%88%E8%BF%9B%E5%B0%81%E8%A3%85&num=10&start=20",
    ]


def test_client_search_deduplicates_results_across_pages(monkeypatch):
    calls = 0

    def fake_read(self, url, *, screens=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            indexes = range(1, 11)
        else:
            indexes = [10, *range(11, 20)]
        links = "\n".join(
            f"[结果 {index}](https://example.com/news/{index})" for index in indexes
        )
        return DokobotPage(title="搜索", url=url, text=links)

    monkeypatch.setattr(DokobotClient, "read", fake_read)
    items = DokobotClient(executable="dokobot").search("先进封装", num=15)

    assert len(items) == 15
    assert len({str(item.link) for item in items}) == 15
    assert calls == 2


def test_search_engine_connectivity_uses_google_by_default(monkeypatch):
    urls = []

    def fake_read(self, url, *, screens=None):
        urls.append(url)
        return DokobotPage(title="Search", url=url, text="available")

    monkeypatch.setattr(DokobotClient, "read", fake_read)
    client = DokobotClient(executable="dokobot")

    assert client.select_search_engine() == "google"
    assert client.search_engine == "google"
    assert urls == ["https://www.google.com/"]


def test_search_engine_connectivity_falls_back_from_google_to_bing(monkeypatch):
    urls = []

    def fake_read(self, url, *, screens=None):
        urls.append(url)
        if "google.com" in url:
            raise DokobotError("unreachable")
        return DokobotPage(title="Search", url=url, text="available")

    monkeypatch.setattr(DokobotClient, "read", fake_read)
    client = DokobotClient(executable="dokobot")

    assert client.select_search_engine() == "bing"
    assert client.search_engine == "bing"
    assert urls == ["https://www.google.com/", "https://www.bing.com/"]


def test_search_engine_connectivity_raises_when_all_are_unreachable(monkeypatch):
    def fake_read(self, url, *, screens=None):
        raise DokobotError("unreachable")

    monkeypatch.setattr(DokobotClient, "read", fake_read)
    client = DokobotClient(executable="dokobot")

    try:
        client.select_search_engine()
    except DokobotError as exc:
        assert "支持的搜索引擎均无法连接" in str(exc)
        assert "已跳过联网搜索任务" in str(exc)
    else:
        raise AssertionError("expected connectivity error")


def test_client_search_uses_selected_bing_engine(monkeypatch):
    urls = []

    def fake_read(self, url, *, screens=None):
        urls.append(url)
        return DokobotPage(
            title="Search",
            url=url,
            text="[结果](https://example.com/news/1)",
        )

    monkeypatch.setattr(DokobotClient, "read", fake_read)
    client = DokobotClient(executable="dokobot")
    client.search_engine = "bing"

    client.search("先进封装", num=1)

    assert urls == [
        "https://www.bing.com/search?q=%E5%85%88%E8%BF%9B%E5%B0%81%E8%A3%85&count=10"
    ]


def test_selected_domestic_engine_is_not_replaced_when_reachable(monkeypatch):
    urls = []

    def fake_read(self, url, *, screens=None):
        urls.append(url)
        return DokobotPage(title="Search", url=url, text="available")

    monkeypatch.setattr(DokobotClient, "read", fake_read)
    client = DokobotClient(executable="dokobot")

    assert client.select_search_engine("sogou") == "sogou"
    assert urls == ["https://www.sogou.com/"]


def test_default_fallback_order_is_google_bing_baidu_sogou_360(monkeypatch):
    urls = []

    def fake_read(self, url, *, screens=None):
        urls.append(url)
        if "so.com" not in url:
            raise DokobotError("unreachable")
        return DokobotPage(title="Search", url=url, text="available")

    monkeypatch.setattr(DokobotClient, "read", fake_read)
    client = DokobotClient(executable="dokobot")

    assert client.select_search_engine() == "so360"
    assert urls == [
        "https://www.google.com/",
        "https://www.bing.com/",
        "https://www.baidu.com/",
        "https://www.sogou.com/",
        "https://www.so.com/",
    ]


def test_non_google_query_removes_unsupported_after_operator():
    query = build_search_query(
        "电子工业项目信息 after:2026-08-01",
        "site:laoyaoba.com",
        date(2026, 8, 1),
        engine="bing",
    )

    assert "after:" not in query
    assert "site:laoyaoba.com" in query


def test_client_search_uses_baidu_date_filter(monkeypatch):
    urls = []

    def fake_read(self, url, *, screens=None):
        urls.append(url)
        return DokobotPage(
            title="Search",
            url=url,
            text="[结果](https://example.com/news/1)",
        )

    monkeypatch.setattr(DokobotClient, "read", fake_read)
    client = DokobotClient(executable="dokobot")
    client.search_engine = "baidu"

    client.search("先进封装 site:laoyaoba.com", num=1, start_date=date(2026, 8, 1))

    assert urls[0].startswith("https://www.baidu.com/s?wd=")
    assert "&pn=0&gpc=stf%3D" in urls[0]
    assert "%7Cstftype%3D1" in urls[0]


def test_cli_error_uses_windows_system_encoding_fallback():
    assert (
        _decode_output("系统找不到指定的文件。".encode("gb18030"))
        == "系统找不到指定的文件。"
    )


def test_client_read_always_uses_free_local_mode(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return CompletedProcess(
            command,
            0,
            "# 示例页面\n> https://example.com/a\n\n有效正文".encode(),
            b"",
        )

    monkeypatch.setattr("app.dokobot.subprocess.run", fake_run)
    page = DokobotClient(executable="dokobot").read("https://example.com/a")

    assert captured["command"][:3] == ["dokobot", "read", "--local"]
    assert "--reuse-tab" in captured["command"]
    assert "--api-key" not in captured["command"]
    assert "remote" not in captured["command"]
    assert page.title == "示例页面"
    assert page.text == "有效正文"


def test_client_uses_deployment_executable_and_home(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return CompletedProcess(
            command,
            0,
            b"# Example\n> https://example.com\n\nbody",
            b"",
        )

    monkeypatch.setenv("SEMICRAWLER_DOKOBOT_EXECUTABLE", "/usr/bin/dokobot")
    monkeypatch.setenv("SEMICRAWLER_DOKOBOT_HOME", "/home/semicrawler")
    monkeypatch.setattr("app.dokobot.subprocess.run", fake_run)

    DokobotClient().read("https://example.com")

    assert captured["command"][0] == "/usr/bin/dokobot"
    assert captured["env"]["HOME"] == "/home/semicrawler"


def test_windows_npm_wrapper_is_bypassed_for_urls_with_ampersands(
    monkeypatch, tmp_path
):
    npm_dir = tmp_path / "npm"
    cli_script = (
        npm_dir
        / "node_modules"
        / "@dokobot"
        / "cli"
        / "dist"
        / "cli"
        / "bin"
        / "dokobot.js"
    )
    cli_script.parent.mkdir(parents=True)
    cli_script.write_text("", encoding="utf-8")
    wrapper = npm_dir / "dokobot.cmd"
    wrapper.write_text("", encoding="utf-8")

    monkeypatch.setattr("app.dokobot.os.name", "nt")
    monkeypatch.setattr(
        "app.dokobot.shutil.which",
        lambda name: "C:/node.exe" if name == "node" else None,
    )

    client = DokobotClient(executable=str(wrapper))

    assert client.command_prefix == ["C:/node.exe", str(cli_script)]


def test_client_retries_transient_local_bridge_failure(monkeypatch):
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return CompletedProcess(
                command, 1, b"", "本地桥接暂不可用".encode("gb18030")
            )
        return CompletedProcess(command, 0, b"# ok\n> https://example.com\n\nbody", b"")

    monkeypatch.setattr("app.dokobot.subprocess.run", fake_run)
    monkeypatch.setattr("app.dokobot.time.sleep", lambda _: None)

    page = DokobotClient(executable="dokobot.cmd").read("https://example.com")

    assert calls == 2
    assert page.text == "body"
