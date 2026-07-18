"""Wiki 编译器工具函数 — slug 生成、相似度、分词、JSON 解析

从 wiki_compiler.py 提取的独立工具模块。
"""

from __future__ import annotations

import json
import re
from typing import Iterator

from app.knowledge.wiki_compiler_types import ENTITY_TYPE_TO_PAGE_TYPE

# CJK 字符检测（用于决定匹配策略与最小词长）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# ────────── 命名约定（AGENTS.md §五）──────────

_SLUG_SAFE_RE = re.compile(r"[^a-zA-Z0-9\-_]")


# ────────── M1: 相似度检测工具函数 ──────────


def entity_to_wiki_slugs(name: str) -> list[str]:
    """GS-2: 将图谱实体名称映射为可能的 wiki slug 候选"""
    candidates = [name]
    kebab = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", name).strip("-").lower()
    if kebab and kebab != name:
        candidates.append(kebab)
    for prefix in ["host-", "service-", "component-", "incident-"]:
        if not name.lower().startswith(prefix):
            candidates.append(f"{prefix}{name}")
    return candidates


# 向后兼容别名
_entity_to_wiki_slugs = entity_to_wiki_slugs


def tokenize(text: str) -> list[str]:
    """简单分词：按空格/CJK字符/标点拆分，保留 2+ 字符的 token"""
    tokens: list[str] = []
    # 按非字母数字/CJK 拆分
    parts = re.split(r"[^\w\u4e00-\u9fff]+", text.lower().strip())
    for part in parts:
        if len(part) >= 2:
            tokens.append(part)
        elif _CJK_RE.search(part):
            # CJK 单字符也保留
            tokens.append(part)
    return tokens


# 向后兼容别名
_tokenize = tokenize


def cosine_similarity(bow1: dict[str, float], bow2: dict[str, float]) -> float:
    """计算两个词袋的余弦相似度"""
    if not bow1 or not bow2:
        return 0.0
    # 交集
    common = set(bow1.keys()) & set(bow2.keys())
    if not common:
        return 0.0
    dot = sum(bow1[k] * bow2[k] for k in common)
    norm1 = sum(v * v for v in bow1.values()) ** 0.5
    norm2 = sum(v * v for v in bow2.values()) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return round(dot / (norm1 * norm2), 4)


# 向后兼容别名
_cosine_similarity = cosine_similarity


def parse_json_response(text: str) -> dict | None:
    """从 LLM 响应中提取 JSON 对象"""
    if not text.strip():
        return None
    # 尝试直接解析
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 尝试提取 { ... } 对象
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


# 向后兼容别名
_parse_json_response = parse_json_response


def slugify(name: str) -> str:
    """转 kebab-case slug 安全形式"""
    s = name.strip().lower()
    s = s.replace(" ", "-").replace("_", "-")
    s = _SLUG_SAFE_RE.sub("", s)
    # 合并连续 -
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unnamed"


# 向后兼容别名
_slugify = slugify


def make_slug(entity_type: str, name: str) -> str:
    """根据 AGENTS.md 命名约定生成 slug

    - 实体页：{type}-{name}（host/service/component）
    - 故障页：{symptom}-troubleshooting
    - 概念页：直接用概念名
    - Runbook 页：runbook-{scenario}
    """
    page_type = ENTITY_TYPE_TO_PAGE_TYPE.get(entity_type, "concept")
    base = slugify(name)
    if page_type == "host":
        return f"host-{base}"
    if page_type == "service":
        return f"service-{base}"
    if page_type == "incident":
        # 若 name 已含 troubleshooting 字样则不再追加
        if "troubleshoot" in base or "故障" in name:
            return base
        return f"{base}-troubleshooting"
    if page_type == "runbook":
        return f"runbook-{base}"
    # concept / entity
    return base


def make_hierarchical_slug(
    title: str,
    level: int,
    parent_slug: str | None = None,
    entity_type: str | None = None,
    max_length: int = 100,
) -> str:
    """生成层级化 Slug，反映文档章节结构

    Slug 命名规则：
    - H1：{slugified-title}（文档主标题）
    - H2：{parent-slug}-{section-slug}
    - H3：{grandparent-slug}-{parent-slug}-{section-slug}
    - 截断规则：总长度不超过 max_length，保留关键识别信息
    - 分隔符：连字符 "-"
    - 大小写：全小写

    Args:
        title: 章节标题
        level: 标题层级（1-6）
        parent_slug: 父章节的 slug（可选）
        entity_type: 实体类型（用于确定前缀，可选）
        max_length: slug 最大长度

    Returns:
        层级化 slug 字符串
    """
    base = slugify(title)
    if not base:
        base = f"section-{level}"

    prefix = ""
    if entity_type:
        page_type = ENTITY_TYPE_TO_PAGE_TYPE.get(entity_type, "")
        if page_type == "host":
            prefix = "host-"
        elif page_type == "service":
            prefix = "service-"
        elif page_type == "runbook":
            prefix = "runbook-"
        elif page_type == "incident":
            if "troubleshoot" not in base and "故障" not in title:
                base += "-troubleshooting"

    if parent_slug:
        parts = parent_slug.split("-")
        if len(parts) > level - 1:
            parent_base = "-".join(parts[: level - 1])
        else:
            parent_base = parent_slug
        candidate = f"{prefix}{parent_base}-{base}" if prefix else f"{parent_base}-{base}"
    else:
        candidate = f"{prefix}{base}" if prefix else base

    if len(candidate) <= max_length:
        return candidate

    truncated_base = base[: max_length - len(prefix) - 1]
    candidate = f"{prefix}{truncated_base}" if prefix else truncated_base
    return candidate.rstrip("-")


def generate_slug_for_heading_tree(
    heading_tree: list[dict],
    parent_slug: str | None = None,
) -> list[dict]:
    """递归为标题树生成层级化 Slug

    Args:
        heading_tree: 标题树字典列表（由 HeadingNode.to_dict() 生成）
        parent_slug: 父章节 slug

    Returns:
        更新后的标题树列表（含 slug 字段）
    """
    result = []
    for node in heading_tree:
        slug = make_hierarchical_slug(
            title=node["title"],
            level=node["level"],
            parent_slug=parent_slug,
        )
        node["slug"] = slug
        if node.get("children"):
            node["children"] = generate_slug_for_heading_tree(
                node["children"],
                parent_slug=slug,
            )
        result.append(node)
    return result


def iter_tree_nodes(nodes: list[dict]) -> Iterator[dict]:
    """递归遍历标题树的所有节点"""
    for node in nodes:
        yield node
        if node.get("children"):
            yield from iter_tree_nodes(node["children"])
