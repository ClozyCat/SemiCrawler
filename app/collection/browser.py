"""Optional Playwright browser transport for JavaScript-rendered pages."""

from .fetcher import BrowserUnavailableError, PlaywrightFetcher

BrowserExecutor = PlaywrightFetcher

__all__ = ["BrowserExecutor", "BrowserUnavailableError", "PlaywrightFetcher"]
