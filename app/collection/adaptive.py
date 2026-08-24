from __future__ import annotations

from .article_discovery import ArticleProfileDetector
from .article_executor import ArticleCollectionExecutor, ArticleProfileValidator
from .executors import CollectionExecutor, Fetcher
from .inspection import PageInspector
from .profiles import CollectionProfile
from .probing import DeterministicDetector
from .validation import ProfileValidator


class DeterministicProbeError(ValueError):
    pass


def detect_and_validate(fetcher: Fetcher, source_url: str,
                        allowed_hosts: list[str]) -> CollectionProfile:
    inspector = PageInspector()
    detector = DeterministicDetector()
    article_detector = ArticleProfileDetector()
    queue = [source_url]
    visited: set[str] = set()
    reasons: list[str] = []
    while queue and len(visited) < 8:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        response = fetcher.fetch(url)
        observation = inspector.inspect(response)
        result = detector.detect(observation, source_url, allowed_hosts)
        if result.profile:
            return ProfileValidator(CollectionExecutor(fetcher, inspector=inspector)).validate(result.profile)
        reasons.append(f"{url}: {result.reason}")
        if result.inspect_urls:
            queue.extend(candidate for candidate in result.inspect_urls if candidate not in visited)
            continue
        article_profile = article_detector.detect(
            response, source_url, observation.fingerprint, allowed_hosts,
            pagination=detector.detect_pagination(observation),
        )
        if article_profile:
            return ArticleProfileValidator(ArticleCollectionExecutor(fetcher, inspector=inspector)).validate(
                article_profile
            )
    raise DeterministicProbeError("；".join(reasons) or "没有可检查的页面候选")
