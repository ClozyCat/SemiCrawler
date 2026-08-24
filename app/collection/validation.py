from __future__ import annotations

from datetime import UTC, datetime

from .executors import CollectionExecutor
from .profiles import CollectionProfile, ProfileValidation


class ProfileValidationError(ValueError):
    pass


class ProfileValidator:
    def __init__(self, executor: CollectionExecutor):
        self.executor = executor

    def validate(self, profile: CollectionProfile) -> CollectionProfile:
        pages = list(self.executor.pages(profile, max_pages=2, max_items=500))
        items = [item for page in pages for item in page.items]
        if not items:
            raise ProfileValidationError("样本页没有解析出记录")
        if profile.pagination.kind != "none" and len(pages) < 2:
            raise ProfileValidationError("分页规则未能取得第二个样本页")

        expected_fields = set(profile.fields)
        populated = sum(
            1 for item in items for field in expected_fields if item.standard_fields.get(field, "").strip()
        )
        completeness = populated / (len(items) * len(expected_fields)) if expected_fields else 0
        dated = [item.published_at for item in items]
        dates_parseable = sum(value is not None for value in dated) / len(items) >= 0.95
        keys = [item.source_item_key for item in items]
        stable_keys = len(keys) == len(set(keys))
        page_key_sets = [{item.source_item_key for item in page.items} for page in pages]
        pagination_changes = len(pages) == 1 or any(page_key_sets[index] != page_key_sets[index - 1]
                                                    for index in range(1, len(page_key_sets)))
        ordered_dates = [value for value in dated if value]
        date_order = "unknown"
        if len(ordered_dates) >= 2:
            if all(left >= right for left, right in zip(ordered_dates, ordered_dates[1:])):
                date_order = "descending"
            elif all(left <= right for left, right in zip(ordered_dates, ordered_dates[1:])):
                date_order = "ascending"

        validation = ProfileValidation(
            pages_checked=len(pages), item_count=len(items), field_completeness=round(completeness, 4),
            dates_parseable=dates_parseable, pagination_changes=pagination_changes, stable_keys=stable_keys,
        )
        if completeness < 0.95:
            raise ProfileValidationError(f"样本字段完整率不足: {completeness:.1%}")
        if not dates_parseable or not stable_keys or not pagination_changes:
            raise ProfileValidationError("样本日期、记录键或分页变化验证失败")
        return profile.model_copy(update={
            "validation": validation, "date_order": date_order, "last_validated_at": datetime.now(UTC),
        })
