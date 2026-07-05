"""Wiki Lint / Health Check 引擎（P1-3）

Karpathy LLM Wiki 的健康检查防御层。LLM 编译的 wiki 是有损压缩（"知识漂移"），
漂移会沿引用链累积。Lint 是发现这些问题的第一道防线。

四类检测（AGENTS.md §七）：
1. **矛盾检测**（Contradictions）：同一概念在多个 wiki 页面有冲突描述
   - 例如：A 页面说"默认端口 80"，B 页面说"默认端口 8080"
2. **Stale 检测**：raw 文档 checksum 变化但 wiki 未重编译（P1-1 已实现，本模块复用）
3. **Orphan 检测**：wiki 页面无任何入链（P0-2 已实现，本模块复用）
4. **Missing Concept 检测**：死链 + 图谱实体无 wiki 页面

输出统一为 LintIssue，可推送至 ReviewQueue 人工裁定。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import structlog
import yaml

from app.knowledge.wikilink import (
    get_orphan_slugs,
    get_all_deadlinks,
)
from app.knowledge.wiki_index import list_wiki_pages, _key_from_slug
from app.knowledge.wiki_drift import list_stale_pages
from app.storage.version_control import get_version_control

logger = structlog.get_logger()


# ────────── 数据模型 ──────────

# 严重度
SEV_INFO = "info"
SEV_WARN = "warn"
SEV_ERROR = "error"

# 问题类型
TYPE_CONTRADICTION = "contradiction"
TYPE_STALE = "stale"
TYPE_ORPHAN = "orphan"
TYPE_DEADLINK = "deadlink"
TYPE_MISSING_CONCEPT = "missing_concept"
TYPE_EMPTY_SECTION = "empty_section"
TYPE_MISSING_TYPE_SECTION = "missing_type_section"


@dataclass
class LintIssue:
    """单个 lint 问题"""

    type: str  # TYPE_*
    severity: str  # SEV_*
    slug: str  # 受影响页面
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class LintReport:
    """lint 报告"""

    issues: list[LintIssue] = field(default_factory=list)
    pages_checked: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    by_severity: dict[str, int] = field(default_factory=dict)

    def add(self, issue: LintIssue) -> None:
        self.issues.append(issue)
        self.by_type[issue.type] = self.by_type.get(issue.type, 0) + 1
        self.by_severity[issue.severity] = self.by_severity.get(issue.severity, 0) + 1

    def to_dict(self) -> dict:
        return {
            "pages_checked": self.pages_checked,
            "total_issues": len(self.issues),
            "by_type": self.by_type,
            "by_severity": self.by_severity,
            "issues": [
                {
                    "type": i.type,
                    "severity": i.severity,
                    "slug": i.slug,
                    "message": i.message,
                    "detail": i.detail,
                }
                for i in self.issues
            ],
        }


# ────────── 类型必含章节规范（AGENTS.md §三）──────────

REQUIRED_SECTIONS: dict[str, list[str]] = {
    "entity": ["概述", "属性", "来源"],
    "concept": ["概述", "来源"],
    "incident": ["概述", "排查步骤", "处置方案", "来源"],
    "runbook": ["概述", "排查步骤", "处置方案", "来源"],
    "service": ["概述", "来源"],
    "host": ["概述", "来源"],
}

# "待 LLM 重编译补充"等模板兜底标记
TEMPLATE_PLACEHOLDER_RE = re.compile(r"待\s*(LLM|补全|人工)")


# ────────── 主入口 ──────────


def lint_all(*, include_stale: bool = True) -> LintReport:
    """运行全部 lint 检查

    Args:
        include_stale: 是否包含 stale 检测（依赖 wiki_drift，可关闭以加速）
    """
    report = LintReport()
    pages = list_wiki_pages(limit=10000)
    report.pages_checked = len(pages)

    all_slugs = {p["slug"] for p in pages}
    all_slugs_with_index = all_slugs | {"index"}

    vc = get_version_control()

    # 加载每个页面的内容（避免重复 IO）
    page_contents: dict[str, str] = {}
    for p in pages:
        latest = vc.get_latest(_key_from_slug(p["slug"]))
        if latest:
            page_contents[p["slug"]] = latest["content"]

    # 1. 矛盾检测
    for issue in _check_contradictions(page_contents):
        report.add(issue)

    # 2. Stale 检测
    if include_stale:
        for stale in list_stale_pages():
            report.add(
                LintIssue(
                    type=TYPE_STALE,
                    severity=SEV_WARN,
                    slug=stale.slug,
                    message=f"raw 文档 {stale.source_doc_id} 已变化但 wiki 未重编译",
                    detail={
                        "source_doc_id": stale.source_doc_id,
                        "old_checksum": stale.old_checksum[:12],
                        "new_checksum": stale.new_checksum[:12],
                        "title": stale.title,
                        "type": stale.type,
                    },
                )
            )

    # 3. Orphan 检测
    orphans = get_orphan_slugs(all_slugs)
    for slug in orphans:
        report.add(
            LintIssue(
                type=TYPE_ORPHAN,
                severity=SEV_INFO,
                slug=slug,
                message="页面无任何入链，建议建立链接或归档",
            )
        )

    # 4. Missing Concept 检测（死链）
    dead = get_all_deadlinks(all_slugs_with_index)
    # 聚合：target → [source]
    dead_aggregated: dict[str, list[str]] = defaultdict(list)
    for d in dead:
        dead_aggregated[d.slug].append(d.source_slug)
    for target, sources in dead_aggregated.items():
        report.add(
            LintIssue(
                type=TYPE_MISSING_CONCEPT,
                severity=SEV_WARN,
                slug=target,
                message=f"被 {len(sources)} 个页面引用但页面不存在（死链）",
                detail={"cited_by": sources},
            )
        )

    # 5. 章节完整性 + 模板兜底检测
    for slug, content in page_contents.items():
        meta, body = _split_frontmatter(content)
        page_type = meta.get("type", "concept")
        page_severity = (
            SEV_WARN if meta.get("review_status") == "review_needed" else SEV_INFO
        )

        # 必含章节
        required = REQUIRED_SECTIONS.get(page_type, ["概述", "来源"])
        for sec in required:
            if not re.search(rf"^##\s+{sec}", body, re.MULTILINE):
                report.add(
                    LintIssue(
                        type=TYPE_MISSING_TYPE_SECTION,
                        severity=page_severity,
                        slug=slug,
                        message=f"{page_type} 类型页面缺少必含章节：{sec}",
                        detail={"page_type": page_type, "missing_section": sec},
                    )
                )

        # 模板兜底标记（LLM 未生成实质内容）
        placeholders = TEMPLATE_PLACEHOLDER_RE.findall(body)
        if placeholders:
            report.add(
                LintIssue(
                    type=TYPE_EMPTY_SECTION,
                    severity=SEV_INFO,
                    slug=slug,
                    message=f"页面含 {len(placeholders)} 处模板兜底标记，建议 LLM 重编译补全",
                    detail={"count": len(placeholders)},
                )
            )

    logger.info(
        "wiki_lint_done",
        pages=report.pages_checked,
        issues=len(report.issues),
        by_type=report.by_type,
    )
    return report


# ────────── 矛盾检测 ──────────

# 抽取"参数: 值"或"参数 = 值"模式
_PARAM_RE = re.compile(
    r"^\s*[-*]?\s*\*{0,2}([A-Za-z_][\w\- ]{0,40})\*{0,2}\s*[:：]\s*([^\n]+)",
    re.MULTILINE,
)


def _check_contradictions(page_contents: dict[str, str]) -> list[LintIssue]:
    """检测同一概念在多个页面的属性值冲突

    策略：
    - 抽取每个页面的"参数: 值"或"- **参数**: 值"行
    - 按"参数名"分组，比较各页面的值
    - 数值/枚举型完全不一致 → 标记矛盾
    """
    # param_name → [(slug, value)]
    param_values: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for slug, content in page_contents.items():
        _, body = _split_frontmatter(content)
        for m in _PARAM_RE.finditer(body):
            key = m.group(1).strip().strip("*").strip().lower()
            val = m.group(2).strip().rstrip("。.,;；")
            # 过滤太长或太短的无意义项
            if not key or len(key) < 2 or len(val) > 100:
                continue
            # 只保留看起来像参数（含数字/单位/路径/布尔）的值
            if not re.search(r"\d|true|false|yes|no|是|否|/|^\w+$", val, re.IGNORECASE):
                continue
            param_values[key].append((slug, val))

    issues: list[LintIssue] = []
    # 找矛盾：同一参数在 ≥2 个不同页面出现且值不一致
    for key, vals in param_values.items():
        if len(vals) < 2:
            continue
        # 按 slug 去重
        unique: dict[str, str] = {}
        for slug, v in vals:
            if slug not in unique:
                unique[slug] = v
        if len(unique) < 2:
            continue
        distinct_vals = set(unique.values())
        if len(distinct_vals) < 2:
            continue
        # 数值型矛盾优先报
        numeric_vals = [
            v for v in unique.values() if re.match(r"^\d+(\.\d+)?\s*\w*$", v)
        ]
        if len(numeric_vals) >= 2 and len(set(numeric_vals)) >= 2:
            issues.append(
                LintIssue(
                    type=TYPE_CONTRADICTION,
                    severity=SEV_ERROR,
                    slug=list(unique.keys())[0],
                    message=f"参数 '{key}' 在多个页面有冲突的数值：{dict(unique)}",
                    detail={"param": key, "values_per_page": unique},
                )
            )
        else:
            issues.append(
                LintIssue(
                    type=TYPE_CONTRADICTION,
                    severity=SEV_WARN,
                    slug=list(unique.keys())[0],
                    message=f"参数 '{key}' 在多个页面取值不一致：{dict(unique)}",
                    detail={"param": key, "values_per_page": unique},
                )
            )

    return issues


# ────────── 内部工具 ──────────


def _split_frontmatter(md: str) -> tuple[dict, str]:
    if not md.startswith("---"):
        return {}, md
    parts = md.split("---", 2)
    if len(parts) < 3:
        return {}, md
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].lstrip("\n")


# ────────── 单条工具：缺失概念补全建议 ──────────


def suggest_missing_pages(limit: int = 20) -> list[dict]:
    """建议需要补全的缺失 wiki 页面（基于死链）

    Returns:
        [{slug, cited_by: [slug], priority}]
    """
    pages = list_wiki_pages(limit=10000)
    all_slugs = {p["slug"] for p in pages} | {"index"}
    dead = get_all_deadlinks(all_slugs)

    cited_count: dict[str, list[str]] = defaultdict(list)
    for d in dead:
        cited_count[d.slug].append(d.source_slug)

    suggestions = [
        {
            "slug": slug,
            "cited_by": sources,
            "priority": min(len(sources), 10),  # 被引用越多优先级越高
        }
        for slug, sources in cited_count.items()
    ]
    suggestions.sort(key=lambda x: x["priority"], reverse=True)
    return suggestions[:limit]
