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
                with self._client.stream(method, current, data=data) as response:
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
