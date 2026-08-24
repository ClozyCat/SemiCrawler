from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .profiles import PageResponse
from .safety import validate_public_address, validate_url

USER_AGENT = "SemiCrawler/2.0 (+public-information-collector)"


@dataclass(frozen=True)
class FetchLimits:
    timeout_seconds: float = 20
    max_response_bytes: int = 5 * 1024 * 1024
    max_redirects: int = 5
    rate_limit_per_minute: int = 12


class ResponseTooLargeError(ValueError):
    pass


class SafeFetcher:
    def __init__(self, entry_url: str, allowed_hosts: set[str] | None = None,
                 limits: FetchLimits | None = None, transport: httpx.BaseTransport | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        entry = validate_url(entry_url, allowed_hosts or None)
        self.allowed_hosts = {entry.host, *(host.rstrip(".").lower() for host in (allowed_hosts or set()))}
        self.limits = limits or FetchLimits()
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=self.limits.timeout_seconds,
            follow_redirects=False, transport=transport,
        )
        self._robots: dict[str, tuple[RobotFileParser | None, str]] = {}
        self._request_lock = threading.RLock()
        self._last_request_at = 0.0
        self._sleep = sleeper

    def _throttle(self) -> None:
        interval = 60 / self.limits.rate_limit_per_minute
        wait = interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = time.monotonic()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SafeFetcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, url: str, data: dict[str, str] | None = None,
                 check_redirect_robots: bool = False) -> tuple[httpx.Response, list[str]]:
        chain: list[str] = []
        current = url
        for _ in range(self.limits.max_redirects + 1):
            validate_url(current, self.allowed_hosts)
            with self._request_lock:
                self._throttle()
                with self._client.stream(
                    method, current, data=data if method == "POST" else None,
                    params=data if method == "GET" else None,
                ) as response:
                    stream = response.extensions.get("network_stream")
                    peer = stream.get_extra_info("server_addr") if stream else None
                    if peer:
                        validate_public_address(peer[0] if isinstance(peer, tuple) else str(peer))
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise httpx.HTTPError("重定向响应缺少 Location")
                        chain.append(str(response.url))
                        current = urljoin(str(response.url), location)
                        validate_url(current, self.allowed_hosts)
                        if check_redirect_robots:
                            parser, _ = self._robots_rules(current)
                            if parser and not parser.can_fetch(USER_AGENT, current):
                                raise PermissionError(f"robots.txt 不允许采集重定向目标 {current}")
                        if response.status_code == 303 or (response.status_code in {301, 302} and method == "POST"):
                            method, data = "GET", None
                        continue
                    length = response.headers.get("content-length")
                    if length and int(length) > self.limits.max_response_bytes:
                        raise ResponseTooLargeError("响应超过大小限制")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > self.limits.max_response_bytes:
                            raise ResponseTooLargeError("响应超过大小限制")
                    response._content = bytes(content)
                    return response, chain
        raise httpx.TooManyRedirects("重定向次数超过限制")

    def _robots_rules(self, url: str) -> tuple[RobotFileParser | None, str]:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        response, _ = self._request("GET", origin + "/robots.txt")
        content_type = response.headers.get("content-type", "").lower()
        if response.status_code in {404, 410}:
            result = (None, "missing")
        elif response.status_code != 200:
            result = (None, f"unavailable_{response.status_code}")
        elif ("html" in content_type or (content_type and not content_type.startswith("text/"))
              or response.text.lstrip().lower().startswith(("<!doctype html", "<html"))):
            result = (None, "invalid_content_type")
        else:
            parser = RobotFileParser()
            parser.set_url(origin + "/robots.txt")
            parser.parse(response.text.splitlines())
            result = (parser, "valid")
        self._robots[origin] = result
        return result

    def fetch(self, url: str, method: str = "GET", form: dict[str, str] | None = None) -> PageResponse:
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise ValueError("仅允许 GET 和普通表单 POST")
        validate_url(url, self.allowed_hosts)
        robots, robots_status = self._robots_rules(url)
        if robots and not robots.can_fetch(USER_AGENT, url):
            raise PermissionError(f"robots.txt 不允许采集 {url}")
        response, chain = self._request(method, url, form, check_redirect_robots=True)
        response.raise_for_status()
        encoding = response.encoding or "utf-8"
        return PageResponse(
            requested_url=url, url=str(response.url), status_code=response.status_code,
            headers=dict(response.headers), content=response.content, encoding=encoding,
            redirect_chain=chain, robots_status=robots_status,
        )


class BrowserUnavailableError(RuntimeError):
    """浏览器兜底未安装或启动失败。"""


class PlaywrightFetcher:
    """可选的 JavaScript 页面观察器，返回与 SafeFetcher 相同的数据契约。"""

    def __init__(self, entry_url: str, allowed_hosts: set[str] | None = None,
                 limits: FetchLimits | None = None):
        self.entry_url = entry_url
        self.allowed_hosts = allowed_hosts or set()
        self.limits = limits or FetchLimits()
        self._playwright = None
        self._browser = None
        self._context = None
        self._safety_fetcher: SafeFetcher | None = None

    def __enter__(self) -> "PlaywrightFetcher":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailableError(
                "浏览器执行器未启用：未安装 Playwright。请安装 playwright 并运行 playwright install chromium"
            ) from exc
        self._playwright = sync_playwright().start()
        try:
            self._safety_fetcher = SafeFetcher(self.entry_url, allowed_hosts=self.allowed_hosts, limits=self.limits)
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent=USER_AGENT, accept_downloads=False, service_workers="block",
            )
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._context = self._browser = self._playwright = None
        if self._safety_fetcher:
            self._safety_fetcher.close()
        self._safety_fetcher = None

    def fetch(self, url: str, method: str = "GET", form: dict[str, str] | None = None) -> PageResponse:
        if method.upper() != "GET":
            raise ValueError("浏览器兜底仅支持 GET 页面观察；表单 POST 请使用 HTTP 执行器")
        validate_url(url, self.allowed_hosts or None)
        if not self._context:
            raise BrowserUnavailableError("浏览器执行器尚未启动，请使用上下文管理器")
        robots_status = "browser_not_checked"
        if self._safety_fetcher:
            robots, robots_status = self._safety_fetcher._robots_rules(url)
            if robots and not robots.can_fetch(USER_AGENT, url):
                raise PermissionError(f"robots.txt 不允许采集 {url}")
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=int(self.limits.timeout_seconds * 1000))
            try:
                page.wait_for_load_state("networkidle", timeout=min(5000, int(self.limits.timeout_seconds * 1000)))
            except Exception:
                pass
            final_url = page.url
            validate_url(final_url, self.allowed_hosts or None)
            content = page.content().encode("utf-8")
            if len(content) > self.limits.max_response_bytes:
                raise ResponseTooLargeError("浏览器页面超过大小限制")
            return PageResponse(
                requested_url=url, url=final_url, status_code=200,
                headers={"content-type": "text/html; charset=utf-8", "x-transport": "browser"},
                content=content, encoding="utf-8", robots_status=robots_status,
            )
        finally:
            page.close()
