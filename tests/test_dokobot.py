from datetime import date
from subprocess import CompletedProcess

from app.dokobot import (
    DokobotClient,
    _decode_output,
    build_search_query,
    parse_search_results,
)


def test_search_query_uses_date_and_source_domains():
    query = build_search_query(
        "先进封装项目",
        "优先 gov.cn 和 https://example.com/news",
        date(2026, 8, 1),
    )
    assert "after:2026-08-01" in query
    assert "site:gov.cn OR site:example.com" in query


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
