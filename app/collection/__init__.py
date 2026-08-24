"""Adaptive collection primitives shared by detectors and executors."""

from .profiles import ArticleItem, CollectionProfile, PageResponse, RecordItem
from .browser import BrowserExecutor, BrowserUnavailableError, PlaywrightFetcher
from .probe_agent import ProbeAgent, ProbeDecision

__all__ = ["ArticleItem", "CollectionProfile", "PageResponse", "ProbeAgent", "ProbeDecision", "RecordItem",
           "BrowserExecutor", "BrowserUnavailableError", "PlaywrightFetcher"]
