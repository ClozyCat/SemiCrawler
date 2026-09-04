from __future__ import annotations

import os
import re
import time
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, HttpUrl, ValidationError


class Crawl4AIError(ValueError):
    """Raised when the Crawl4AI sidecar cannot return usable page content."""


class Crawl4AIPage(BaseModel):
    url: HttpUrl
    title: str = ""
    text: str = Field(min_length=1)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _validate_target_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise Crawl4AIError("Crawl4AI 只允许抓取有效的 HTTP(S) 网址")
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        raise Crawl4AIError("Crawl4AI 不允许抓取本机或本地网络地址")
    try:
        address = ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise Crawl4AIError("Crawl4AI 不允许抓取非公网 IP 地址")


class Crawl4AIClient:
    """Synchronous REST client for a separately deployed Crawl4AI service."""

    _BLOCK_PAGE_MARKERS = (
        "access denied",
        "verify you are human",
        "enable javascript and cookies to continue",
        "captcha",
        "访问被拒绝",
        "请完成安全验证",
        "请先登录",
    )

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_token: str | None = None,
        enabled: bool | None = None,
        timeout: float | None = None,
        min_content_chars: int | None = None,
    ):
        self.enabled = (
            _env_flag("CRAWL4AI_ENABLED") if enabled is None else enabled
        )
        self.base_url = (
            base_url
            or os.getenv("CRAWL4AI_BASE_URL", "http://127.0.0.1:11235")
        ).rstrip("/")
        self.api_token = (
            api_token if api_token is not None else os.getenv("CRAWL4AI_API_TOKEN", "")
        ).strip()
        self.timeout = timeout or float(os.getenv("CRAWL4AI_TIMEOUT_SECONDS", "90"))
        configured_minimum = min_content_chars or int(
            os.getenv("CRAWL4AI_MIN_CONTENT_CHARS", "200")
        )
        self.min_content_chars = max(configured_minimum, 1)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    @staticmethod
    def _markdown_text(result: dict[str, Any]) -> str:
        markdown = result.get("markdown")
        if isinstance(markdown, str):
            return markdown.strip()
        if isinstance(markdown, dict):
            for key in ("fit_markdown", "raw_markdown", "markdown_with_citations"):
                value = markdown.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def _validate_text(self, text: str) -> None:
        compact = _compact_text(text)
        if len(compact) < self.min_content_chars:
            raise Crawl4AIError(
                f"Crawl4AI 正文过短（{len(compact)} 字符，至少需要 {self.min_content_chars}）"
            )
        lowered = compact.casefold()
        if len(compact) < 1500 and any(
            marker in lowered for marker in self._BLOCK_PAGE_MARKERS
        ):
            raise Crawl4AIError("Crawl4AI 返回了验证、登录或访问拦截页面")

    def extract(self, url: str) -> Crawl4AIPage:
        if not self.enabled:
            raise Crawl4AIError("Crawl4AI 未启用")
        _validate_target_url(url)

        payload = {
            "urls": [url],
            "browser_config": {
                "type": "BrowserConfig",
                "params": {
                    "headless": True,
                    "text_mode": True,
                },
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {
                    "cache_mode": "bypass",
                    "check_robots_txt": True,
                    "wait_until": "domcontentloaded",
                    "page_timeout": 60000,
                    "delay_before_return_html": 1.0,
                    "process_iframes": True,
                    "remove_overlay_elements": True,
                    "remove_consent_popups": True,
                },
            },
        }
        data: Any = None
        for attempt in range(2):
            try:
                response = httpx.post(
                    f"{self.base_url}/crawl",
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.TimeoutException as exc:
                raise Crawl4AIError("Crawl4AI 请求超时") from exc
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if attempt == 0 and status_code in {429, 502, 503, 504}:
                    retry_after = exc.response.headers.get("Retry-After", "1")
                    try:
                        delay = min(max(float(retry_after), 0.1), 3.0)
                    except ValueError:
                        delay = 1.0
                    time.sleep(delay)
                    continue
                detail = exc.response.text[:300]
                raise Crawl4AIError(
                    f"Crawl4AI 请求失败（HTTP {status_code}）：{detail}"
                ) from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise Crawl4AIError("无法连接 Crawl4AI 服务") from exc

        if not isinstance(data, dict) or data.get("success") is not True:
            raise Crawl4AIError("Crawl4AI 返回格式无效或抓取失败")
        results = data.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise Crawl4AIError("Crawl4AI 未返回网页结果")
        result = results[0]
        if result.get("success") is not True:
            message = str(result.get("error_message") or "未知错误")[:300]
            raise Crawl4AIError(f"Crawl4AI 抓取失败：{message}")

        text = self._markdown_text(result)
        self._validate_text(text)
        metadata = result.get("metadata")
        title = metadata.get("title", "") if isinstance(metadata, dict) else ""
        try:
            return Crawl4AIPage(
                url=result.get("url") or url,
                title=title if isinstance(title, str) else "",
                text=text,
            )
        except ValidationError as exc:
            raise Crawl4AIError("Crawl4AI 返回的网页正文无效") from exc
