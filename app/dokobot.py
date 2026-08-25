from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from datetime import date
from locale import getpreferredencoding
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

from pydantic import BaseModel, Field, HttpUrl, ValidationError


class DokobotError(ValueError):
    pass


class DokobotSearchItem(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    link: HttpUrl
    snippet: str = ""


class DokobotPage(BaseModel):
    title: str = ""
    url: HttpUrl
    text: str = Field(min_length=1)


_URL_RE = re.compile(r"https?://[^\s<>\"'）】]+")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\((https?://[^)]+)\)")
_SEARCH_HOSTS = {
    "google.com",
    "www.google.com",
    "accounts.google.com",
    "support.google.com",
    "bing.com",
    "www.bing.com",
}
_CLI_LOCK = threading.Lock()
_SEARCH_ENGINE_HOME = {
    "google": "https://www.google.com/",
    "bing": "https://www.bing.com/",
}


def build_search_query(query: str, source_hint: str, start_date: date) -> str:
    """Translate the source form into operators understood by Google search."""
    parts = [query.strip(), f"after:{start_date.isoformat()}"]
    domains = [
        host.lower()
        for host in re.findall(
            r"(?<![@\w])(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)",
            source_hint,
            re.IGNORECASE,
        )
    ]
    if domains:
        sites = " OR ".join(f"site:{host}" for host in dict.fromkeys(domains))
        parts.append(f"({sites})")
    elif source_hint.strip():
        parts.append(source_hint.strip())
    return " ".join(parts)


def _result_url(raw_url: str) -> str | None:
    candidate = raw_url.rstrip(".,;:!?，。；：！？)]}")
    parsed = urlparse(candidate)
    if parsed.hostname in _SEARCH_HOSTS and parsed.path == "/url":
        values = parse_qs(parsed.query)
        candidate = (values.get("q") or values.get("url") or [""])[0]
        parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.hostname in _SEARCH_HOSTS or parsed.hostname.endswith(".google.com"):
        return None
    return candidate


def parse_search_results(text: str, *, limit: int) -> list[DokobotSearchItem]:
    """Extract links from Dokobot's layout-preserving search page text."""
    candidates: list[tuple[str, str]] = []
    for match in _MARKDOWN_LINK_RE.finditer(text):
        candidates.append((match.group(1).strip(), match.group(2)))

    lines = [line.strip() for line in text.splitlines()]
    reference_urls: dict[str, str] = {}
    definition_start = len(lines)
    for index, line in enumerate(lines):
        definition = re.fullmatch(r"\[(\d+)]\s+(https?://\S+)", line)
        if definition:
            definition_start = min(definition_start, index)
            reference_urls[definition.group(1)] = definition.group(2)

    visible_lines = lines[:definition_start]
    for reference, raw_url in reference_urls.items():
        occurrences = [
            line
            for line in visible_lines
            if re.search(rf"\[{re.escape(reference)}]", line)
        ]
        # Search results repeat the same reference for source, display URL,
        # and title. Navigation links normally appear only once.
        if len(occurrences) < 2:
            continue
        title = next(
            (
                re.sub(rf"\s*\[{re.escape(reference)}]\s*$", "", line).strip()
                for line in reversed(occurrences)
                if not _URL_RE.search(line)
            ),
            "网页结果",
        )
        candidates.append((title, raw_url))

    for index, line in enumerate(lines):
        if re.fullmatch(r"\[\d+]\s+https?://\S+", line) or re.search(
            r"\[\d+]\s*$", line
        ):
            continue
        for match in _URL_RE.finditer(line):
            title = next(
                (
                    lines[pos]
                    for pos in range(index - 1, max(index - 4, -1), -1)
                    if lines[pos] and not _URL_RE.search(lines[pos])
                ),
                "网页结果",
            )
            candidates.append((title, match.group(0)))

    results: list[DokobotSearchItem] = []
    seen: set[str] = set()
    for raw_title, raw_url in candidates:
        url = _result_url(raw_url)
        if not url or url in seen:
            continue
        try:
            results.append(
                DokobotSearchItem(title=raw_title[:500] or "网页结果", link=url)
            )
        except ValidationError:
            continue
        seen.add(url)
        if len(results) >= limit:
            break
    return results


def _decode_output(value: bytes) -> str:
    if not value:
        return ""
    encodings = ["utf-8", getpreferredencoding(False), "gb18030"]
    for encoding in dict.fromkeys(encodings):
        try:
            return value.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return value.decode("utf-8", errors="replace")


class DokobotClient:
    """Thin wrapper around Dokobot's free local browser bridge."""

    def __init__(self, executable: str | None = None):
        configured_executable = os.getenv("SEMICRAWLER_DOKOBOT_EXECUTABLE")
        self.executable = (
            executable or configured_executable or shutil.which("dokobot") or ""
        )
        self.home = os.getenv("SEMICRAWLER_DOKOBOT_HOME", "").strip()
        self.search_engine = "google"
        if not self.executable:
            raise DokobotError(
                "未找到 Dokobot CLI，请先安装 @dokobot/cli "
                "并执行 dokobot install-bridge；systemd 部署可设置 "
                "SEMICRAWLER_DOKOBOT_EXECUTABLE"
            )
        self.command_prefix = [self.executable]
        if os.name == "nt" and self.executable.casefold().endswith((".cmd", ".bat")):
            cli_script = (
                Path(self.executable).parent
                / "node_modules"
                / "@dokobot"
                / "cli"
                / "dist"
                / "cli"
                / "bin"
                / "dokobot.js"
            )
            node = shutil.which("node")
            if node and cli_script.is_file():
                # Bypass npm's .cmd wrapper: cmd.exe interprets '&' inside
                # search URLs as a command separator even with list arguments.
                self.command_prefix = [node, str(cli_script)]

    def _command(self, *args: str, timeout: int) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        env["DOKOBOT_DISABLE_AUTO_UPDATE"] = "1"
        env["DOKOBOT_TELEMETRY_DISABLED"] = "1"
        if self.home:
            # Dokobot discovers Linux bridge sockets below $HOME/.dokobot.
            env["HOME"] = self.home
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            with _CLI_LOCK:
                completed = subprocess.run(
                    [*self.command_prefix, *args],
                    capture_output=True,
                    timeout=timeout,
                    env=env,
                    creationflags=creationflags,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise DokobotError(
                "Dokobot 本地读页超时，请确认浏览器和本地桥接保持在线"
            ) from exc
        except OSError as exc:
            raise DokobotError("无法启动 Dokobot CLI，请检查安装路径") from exc
        if completed.returncode:
            detail = next(
                (
                    line.strip()
                    for line in reversed(_decode_output(completed.stderr).splitlines())
                    if line.strip()
                ),
                "未知错误",
            )
            raise DokobotError(f"Dokobot 本地读页失败：{detail[:300]}")
        return completed

    def read(self, url: str, *, screens: int | None = None) -> DokobotPage:
        args = ["read", "--local", url, "--timeout", "90"]
        if screens is not None:
            args.extend(["--screens", str(screens)])
        last_error: DokobotError | None = None
        for attempt in range(2):
            try:
                completed = self._command(*args, timeout=120)
                break
            except DokobotError as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(1)
        else:
            raise last_error or DokobotError("Dokobot 本地读页失败")
        output = _decode_output(completed.stdout).strip()
        lines = output.splitlines()
        title = (
            lines[0].removeprefix("# ").strip()
            if lines and lines[0].startswith("# ")
            else ""
        )
        page_url = url
        body_start = 1 if title else 0
        if len(lines) > body_start and lines[body_start].startswith("> "):
            page_url = lines[body_start].removeprefix("> ").strip() or url
            body_start += 1
        body = "\n".join(lines[body_start:]).strip()
        try:
            return DokobotPage(title=title, url=page_url, text=body)
        except ValidationError as exc:
            raise DokobotError("Dokobot 未能从页面提取有效正文") from exc

    def select_search_engine(self) -> str:
        """Select the first search engine reachable through the local browser."""
        errors: list[str] = []
        for engine in ("google", "bing"):
            try:
                self.read(_SEARCH_ENGINE_HOME[engine], screens=1)
            except DokobotError as exc:
                errors.append(f"{engine}: {exc}")
                continue
            self.search_engine = engine
            return engine
        detail = "; ".join(errors)
        raise DokobotError(
            "Google 和 Bing 均无法连接，已跳过联网搜索任务"
            + (f"：{detail}" if detail else "")
        )

    def search(self, query: str, *, num: int) -> list[DokobotSearchItem]:
        limit = min(max(num, 1), 100)
        page_size = 10
        encoded = quote_plus(query)
        if self.search_engine == "google":
            build_url = lambda offset: (
                f"https://www.google.com/search?q={encoded}&num={page_size}"
                + (f"&start={offset}" if offset else "")
            )
        elif self.search_engine == "bing":
            build_url = lambda offset: (
                f"https://www.bing.com/search?q={encoded}&count={page_size}"
                + (f"&first={offset + 1}" if offset else "")
            )
        else:
            raise DokobotError(f"不支持的搜索引擎：{self.search_engine}")

        last_error: DokobotError | None = None
        results: list[DokobotSearchItem] = []
        seen: set[str] = set()
        offset = 0
        while len(results) < limit and offset < 1000:
            try:
                page = self.read(build_url(offset), screens=3)
            except DokobotError as exc:
                last_error = exc
                break

            page_results = parse_search_results(page.text, limit=page_size)
            new_results = [item for item in page_results if str(item.link) not in seen]
            if not new_results:
                break
            results.extend(new_results)
            seen.update(str(item.link) for item in new_results)
            if len(results) >= limit:
                return results[:limit]
            offset += page_size

        if results:
            return results[:limit]
        if last_error:
            raise DokobotError(f"Dokobot 搜索失败：{last_error}") from last_error
        raise DokobotError("Dokobot 未从搜索页提取到结果，请检查浏览器是否出现验证码")
