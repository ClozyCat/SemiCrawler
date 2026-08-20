from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from itertools import combinations

from .models import RawArticle, StructuredRecord


STOP_WORDS = {
    "一个", "一些", "以及", "相关", "进行", "项目", "公司", "企业", "目前", "已经", "表示", "通过",
    "其中", "建设", "发展", "发布", "实现", "用于", "行业", "技术", "产品", "信息", "此次", "进一步",
    "科技", "基地", "产业", "集团", "有限公司", "有限责任公司", "股份有限公司", "集团公司",
    "亿元", "万元", "千万元", "百万元", "亿美元", "营收", "收入", "利润", "同比", "环比", "增长", "下降",
    "上半年", "下半年", "季度", "年度", "年内", "近日", "日前", "未来",
    "the", "and", "for", "with", "from", "that", "this",
}
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+-]{1,30}|[\u4e00-\u9fff]{2,8}")
NOISE_NUMBER_RE = re.compile(r"^(?:\d+(?:\.\d+)?|\d{4}年?|\d+(?:\.\d+)?%)$")
NOISE_AMOUNT_RE = re.compile(r"^[\d,.]+(?:亿元|万元|千万元|百万元|亿美元|美元|人民币|万件|万台)$")


ENTITY_PRIORITY = {"企业": 4, "机构": 3, "项目": 2, "地域": 1}


def _normalize_entity_label(label: str) -> str:
    normalized = unicodedata.normalize("NFKC", label or "")
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _entity_id(kind: str, label: str) -> str:
    # The same name may be emitted into multiple LLM fields. Entity identity is
    # name-based; the visible category is resolved by priority below.
    return f"entity:{_normalize_entity_label(label)}"


def _fallback_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in TOKEN_RE.findall(text):
        normalized = token.lower() if token.isascii() else token
        if not _is_noise_token(normalized):
            tokens.append(normalized)
    return tokens


def _is_noise_token(token: str) -> bool:
    normalized = token.strip().lower()
    if not normalized or normalized in STOP_WORDS:
        return True
    if NOISE_NUMBER_RE.fullmatch(normalized) or NOISE_AMOUNT_RE.fullmatch(normalized):
        return True
    if normalized.endswith(("有限公司", "有限责任公司", "股份有限公司", "集团公司")):
        return True
    return False


def _tokens(text: str) -> list[str]:
    try:
        import jieba.analyse

        return [token.strip() for token in jieba.analyse.extract_tags(text, topK=40, withWeight=False)
                if len(token.strip()) > 1 and not _is_noise_token(token)]
    except ImportError:
        return _fallback_tokens(text)


def _configured_keywords(keyword_config: list[dict] | None) -> list[str]:
    values: list[str] = []
    for row in keyword_config or []:
        raw = str(row.get("keywords") or "")
        values.extend(part.strip() for part in re.split(r"[,，、;；\s]+", raw) if len(part.strip()) > 1)
    return list(dict.fromkeys(values))


def build_analytics(records: list[StructuredRecord], articles: dict[int, RawArticle],
                    max_nodes: int = 60, max_keywords: int = 30,
                    allowed_keywords: list[str] | None = None) -> dict:
    node_counts: Counter[str] = Counter()
    node_data: dict[str, dict[str, str]] = {}
    edge_counts: Counter[tuple[str, str]] = Counter()
    edge_records: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
    keyword_counts: Counter[str] = Counter()
    keyword_documents: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()

    fields = (
        ("company", "企业", "company_name"),
        ("organization", "机构", "organization"),
        ("region", "地域", "region"),
        ("project", "项目", "project_name"),
    )
    for record in records:
        entities: list[str] = []
        for kind, category, field in fields:
            label = (getattr(record, field) or "").strip()
            if not label or label in {"未披露", "未知", "—"}:
                continue
            entity_id = _entity_id(kind, label)
            if entity_id in entities:
                # A record may repeat an entity in company and organization;
                # count one occurrence per record and resolve its best type.
                current = node_data.get(entity_id)
                if current and ENTITY_PRIORITY[category] > ENTITY_PRIORITY[current["category"]]:
                    current["category"] = category
                continue
            node_counts[entity_id] += 1
            current = node_data.get(entity_id)
            if not current or ENTITY_PRIORITY[category] > ENTITY_PRIORITY[current["category"]]:
                node_data[entity_id] = {"id": entity_id, "name": label, "category": category}
            entities.append(entity_id)

        for source, target in combinations(sorted(set(entities)), 2):
            edge_counts[(source, target)] += 1
            if len(edge_records[(source, target)]) < 10:
                edge_records[(source, target)].append(record.id)

        type_counts[record.info_type or "其他"] += 1
        article = articles.get(record.article_id) if record.article_id else None
        text = " ".join(filter(None, [record.company_name, record.organization, record.project_name,
                                      record.details, article.title if article else "", article.body if article else ""]))
        if allowed_keywords is not None:
            matched = [keyword for keyword in allowed_keywords for _ in range(text.lower().count(keyword.lower()))]
            keyword_counts.update(matched)
            keyword_documents.update(set(matched))
        else:
            document_tokens = set(_tokens(text))
            keyword_counts.update(_tokens(text))
            keyword_documents.update(document_tokens)

    selected_ids = {node_id for node_id, _ in node_counts.most_common(max_nodes)}
    nodes = [{**node_data[node_id], "value": count} for node_id, count in node_counts.most_common(max_nodes)]
    edges = [
        {"source": source, "target": target, "value": count, "record_ids": edge_records[(source, target)]}
        for (source, target), count in edge_counts.most_common()
        if source in selected_ids and target in selected_ids
    ]
    keywords = [
        {"text": token, "count": count, "document_count": keyword_documents[token]}
        for token, count in keyword_counts.most_common(max_keywords)
    ]
    return {
        "summary": {"record_count": len(records), "article_count": len(articles),
                    "entity_count": len(node_counts), "relation_count": len(edge_counts)},
        "keywords": keywords,
        "graph": {"nodes": nodes, "edges": edges},
        "info_types": [{"name": name, "value": value} for name, value in type_counts.most_common()],
    }
