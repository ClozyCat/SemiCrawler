from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import UTC, date, datetime
from datetime import time as datetime_time
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
    session_id: str | None = None


_URL_RE = re.compile(r"https?://[^\s<>\"'）】]+")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\((https?://[^)]+)\)")
_SEARCH_HOSTS = {
    "google.com",
    "www.google.com",
    "accounts.google.com",
    "support.google.com",
    "bing.com",
    "www.bing.com",
    "baidu.com",
    "www.baidu.com",
    "sogou.com",
    "www.sogou.com",
    "so.com",
    "www.so.com",
}
_CLI_LOCK = threading.Lock()
_SESSION_LOCK = threading.Lock()
_ACTIVE_SESSIONS: deque[str] = deque()
MAX_DOKOBOT_TABS = 5
_SEARCH_ENGINE_HOME = {
    "google": "https://www.google.com/",
    "bing": "https://www.bing.com/",
    "baidu": "https://www.baidu.com/",
    "sogou": "https://www.sogou.com/",
    "so360": "https://www.so.com/",
}
SEARCH_ENGINE_LABELS = {
    "google": "Google",
    "bing": "Bing",
    "baidu": "百度",
    "sogou": "搜狗",
    "so360": "360 搜索",
}
SEARCH_ENGINE_FALLBACK_ORDER = ("google", "bing", "baidu", "sogou", "so360")
DEFAULT_SEARCH_ENGINE = "google"


def _effective_start_date(query: str, start_date: date) -> date:
    dates = [start_date]
    for raw_date in re.findall(
        r"(?<!\w)after:(\d{4}-\d{2}-\d{2})", query, re.IGNORECASE
    ):
        try:
            dates.append(date.fromisoformat(raw_date))
        except ValueError:
            continue
    return max(dates)


def build_search_query(
    query: str,
    source_hint: str,
    start_date: date,
    *,
    engine: str = DEFAULT_SEARCH_ENGINE,
) -> str:
    """Translate the source form into operators understood by web search engines."""
    if engine not in SEARCH_ENGINE_LABELS:
        raise DokobotError(f"不支持的搜索引擎：{engine}")
    effective_start = _effective_start_date(query, start_date)
    clean_query = re.sub(
        r"(?<!\w)after:\d{4}-\d{2}-\d{2}", "", query, flags=re.IGNORECASE
    )
    parts = [" ".join(clean_query.split())]
    if engine == "google":
        parts.append(f"after:{effective_start.isoformat()}")
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
    return " ".join(part for part in parts if part)


def source_hint_variants(source_hint: str) -> list[str]:
    """Split URL source hints so each URL becomes an independent search scope."""
    variants: list[str] = []
    for line in source_hint.splitlines():
        line = line.strip()
        if not line:
            continue
        matches = re.findall(
            r"https?://[^\s<>\"'）】]+|(?<![@\w])(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+",
            line,
            re.IGNORECASE,
        )
        if matches:
            variants.extend(item.rstrip(".,;:!?，。；：！？)]}") for item in matches)
        else:
            variants.append(line)
    return list(dict.fromkeys(variant for variant in variants if variant)) or [""]


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
        self.search_engine = DEFAULT_SEARCH_ENGINE
        self.preferred_search_engine = DEFAULT_SEARCH_ENGINE
        self.search_start_date: date | None = None
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
        args = ["read", "--local", "--reuse-tab", url, "--timeout", "90"]
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
        session_match = re.search(
            r"(?:Session|sessionId)[:=]\s*([^\s)]+)",
            _decode_output(completed.stderr),
        )
        session_id = session_match.group(1) if session_match else None
        try:
            page = DokobotPage(
                title=title, url=page_url, text=body, session_id=session_id
            )
        except ValidationError as exc:
            raise DokobotError("Dokobot 未能从页面提取有效正文") from exc
        if page.session_id:
            with _SESSION_LOCK:
                has_capacity = len(_ACTIVE_SESSIONS) < MAX_DOKOBOT_TABS
            if not has_capacity:
                self.close_stale_sessions()
                with _SESSION_LOCK:
                    has_capacity = len(_ACTIVE_SESSIONS) < MAX_DOKOBOT_TABS
                if not has_capacity:
                    try:
                        self.close_session(page.session_id)
                    except DokobotError:
                        pass
                    raise DokobotError(
                        f"Dokobot 接管标签页已达到上限（{MAX_DOKOBOT_TABS} 个）"
                    )
            with _SESSION_LOCK:
                _ACTIVE_SESSIONS.append(page.session_id)
        return page

    def close_session(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._command("close", session_id, timeout=30)
        with _SESSION_LOCK:
            try:
                _ACTIVE_SESSIONS.remove(session_id)
            except ValueError:
                pass

    def close_stale_sessions(self) -> None:
        with _SESSION_LOCK:
            sessions = list(dict.fromkeys(_ACTIVE_SESSIONS))
        for session_id in sessions:
            try:
                self.close_session(session_id)
            except DokobotError:
                continue

    def select_search_engine(self, preferred: str | None = None) -> str:
        """Use the preferred engine, falling back only when it is unreachable."""
        preferred = preferred or self.preferred_search_engine
        if preferred not in SEARCH_ENGINE_LABELS:
            raise DokobotError(f"不支持的搜索引擎：{preferred}")
        errors: list[str] = []
        candidates = (
            preferred,
            *(item for item in SEARCH_ENGINE_FALLBACK_ORDER if item != preferred),
        )
        for engine in candidates:
            page: DokobotPage | None = None
            try:
                page = self.read(_SEARCH_ENGINE_HOME[engine], screens=1)
            except DokobotError as exc:
                errors.append(f"{engine}: {exc}")
                continue
            finally:
                if page:
                    try:
                        self.close_session(page.session_id)
                    except DokobotError:
                        pass
            self.search_engine = engine
            return engine
        detail = "; ".join(errors)
        raise DokobotError(
            "支持的搜索引擎均无法连接，已跳过联网搜索任务"
            + (f"：{detail}" if detail else "")
        )

    def search(
        self, query: str, *, num: int, start_date: date | None = None
    ) -> list[DokobotSearchItem]:
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
        elif self.search_engine == "baidu":
            date_filter = ""
            effective_start_date = start_date or self.search_start_date
            if effective_start_date:
                start_timestamp = int(
                    datetime.combine(
                        effective_start_date, datetime_time.min, tzinfo=UTC
                    ).timestamp()
                )
                date_filter = "&gpc=" + quote_plus(
                    f"stf={start_timestamp},2147483647|stftype=1"
                )
            build_url = lambda offset: (
                f"https://www.baidu.com/s?wd={encoded}&rn={page_size}&pn={offset}"
                + date_filter
            )
        elif self.search_engine == "sogou":
            build_url = lambda offset: (
                f"https://www.sogou.com/web?query={encoded}&page={offset // page_size + 1}"
            )
        elif self.search_engine == "so360":
            build_url = lambda offset: (
                f"https://www.so.com/s?q={encoded}&pn={offset // page_size + 1}"
            )
        else:
            raise DokobotError(f"不支持的搜索引擎：{self.search_engine}")

        last_error: DokobotError | None = None
        results: list[DokobotSearchItem] = []
        seen: set[str] = set()
        offset = 0
        while len(results) < limit and offset < 1000:
            page: DokobotPage | None = None
            try:
                page = self.read(build_url(offset), screens=3)
            except DokobotError as exc:
                last_error = exc
                break
            finally:
                if page:
                    try:
                        self.close_session(page.session_id)
                    except DokobotError:
                        pass

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
