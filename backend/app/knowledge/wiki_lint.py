"""Wiki Lint / Health Check 引擎（P1-3）

Karpathy LLM Wiki 的健康检查防御层。LLM 编译的 wiki 是有损压缩（"知识漂移"），
漂移会沿引用链累积。Lint 是发现这些问题的第一道防线。

四类检测（AGENTS.md §七）：
1. **矛盾检测**（Contradictions）：同一概念在多个 wiki 页面有冲突描述
   - 例如：A 页面说"默认端口 80"，B 页面说"默认端口 8080"
   - S12-3 升级：regex 快速检测 + LLM 语义检测（可选）
2. **Stale 检测**：raw 文档 checksum 变化但 wiki 未重编译（P1-1 已实现，本模块复用）
3. **Orphan 检测**：wiki 页面无任何入链（P0-2 已实现，本模块复用）
4. **Missing Concept 检测**：死链 + 图谱实体无 wiki 页面

输出统一为 LintIssue，可推送至 ReviewQueue 人工裁定。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field

import structlog
import yaml

from app.knowledge.wiki_drift import list_stale_pages
from app.knowledge.wiki_index import _key_from_slug, list_wiki_pages
from app.knowledge.wikilink import (
    get_all_deadlinks,
    get_orphan_slugs,
    get_outlinks,
)
from app.storage.version_control import get_version_control

logger = structlog.get_logger()


# ────────── 数据模型 ──────────

# 严重度
SEV_INFO = "info"
SEV_WARN = "warn"
SEV_ERROR = "error"

# 问题类型
TYPE_CONTRADICTION = "contradiction"
TYPE_CONTRADICTION_SEMANTIC = "contradiction_semantic"  # S12-3 LLM 语义矛盾
TYPE_STALE = "stale"
TYPE_ORPHAN = "orphan"
TYPE_DEADLINK = "deadlink"
TYPE_MISSING_CONCEPT = "missing_concept"
TYPE_EMPTY_SECTION = "empty_section"
TYPE_MISSING_TYPE_SECTION = "missing_type_section"
TYPE_OKF_VIOLATION = "okf_violation"  # P2-1 OKF v0.1 合规违规


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

    # 6. P2-1: OKF v0.1 合规检测
    try:
        from app.knowledge.okf_validator import to_lint_issues, validate_wiki

        okf_result = validate_wiki()
        for issue_dict in to_lint_issues(okf_result):
            report.add(
                LintIssue(
                    type=TYPE_OKF_VIOLATION,
                    severity=issue_dict["severity"],
                    slug=issue_dict["slug"],
                    message=issue_dict["message"],
                    detail=issue_dict["detail"],
                )
            )
    except Exception as e:
        logger.warning("wiki_lint_okf_check_failed", error=str(e))

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


# ────────── S12-3 LLM 语义矛盾检测 ──────────


async def lint_all_async(
    *,
    include_stale: bool = True,
    include_semantic: bool = False,
    max_semantic_pairs: int = 10,
) -> LintReport:
    """异步 lint，支持 LLM 语义矛盾检测

    与 lint_all() 行为一致，额外支持 include_semantic 参数。
    LLM 不可用时自动降级为仅 regex 检测。

    Args:
        include_stale: 是否包含 stale 检测
        include_semantic: 是否启用 LLM 语义矛盾检测（成本较高）
        max_semantic_pairs: 限制 LLM 调用的最大页面配对数
    """
    # 复用同步实现完成 regex 部分
    report = lint_all(include_stale=include_stale)

    if not include_semantic:
        return report

    # 加载页面内容与元信息
    pages = list_wiki_pages(limit=10000)
    vc = get_version_control()
    page_contents: dict[str, str] = {}
    page_metas: dict[str, dict] = {}
    for p in pages:
        latest = vc.get_latest(_key_from_slug(p["slug"]))
        if latest:
            page_contents[p["slug"]] = latest["content"]
            page_metas[p["slug"]] = p

    # 运行 LLM 语义检测
    try:
        semantic_issues = await _check_contradictions_semantic(
            page_contents, page_metas, max_pairs=max_semantic_pairs
        )
        for issue in semantic_issues:
            report.add(issue)
    except Exception as e:
        logger.warning("wiki_lint_semantic_failed", error=str(e))

    return report


async def _check_contradictions_semantic(
    page_contents: dict[str, str],
    page_metas: dict[str, dict],
    max_pairs: int = 10,
) -> list[LintIssue]:
    """LLM 辅助语义矛盾检测

    候选对筛选（避免 O(N²) LLM 调用）：
    - 共享至少一个 tag 的页面
    - 一个 outlink 到另一个

    LLM 对每对页面判断：是否存在事实层面的冲突，返回 JSON 列表
    """
    from app.config import get_settings
    from app.core.llm import get_llm_client

    settings = get_settings()
    llm = get_llm_client()

    # 收集候选对
    candidate_pairs = _collect_semantic_candidate_pairs(page_metas)
    if not candidate_pairs:
        return []

    # 限制对数（控制成本）
    candidate_pairs = candidate_pairs[:max_pairs]

    issues: list[LintIssue] = []
    for slug_a, slug_b in candidate_pairs:
        try:
            conflicts = await _llm_detect_conflicts(
                llm,
                settings,
                slug_a=slug_a,
                content_a=page_contents[slug_a],
                slug_b=slug_b,
                content_b=page_contents[slug_b],
            )
            for c in conflicts:
                issues.append(
                    LintIssue(
                        type=TYPE_CONTRADICTION_SEMANTIC,
                        severity=SEV_WARN,
                        slug=slug_a,
                        message=f"语义矛盾（与 [[{slug_b}]]）：{c.get('summary', '')}",
                        detail={
                            "other_slug": slug_b,
                            "conflicting_claim": c.get("summary", ""),
                            "evidence_a": c.get("evidence_a", ""),
                            "evidence_b": c.get("evidence_b", ""),
                            "detection": "semantic_llm",
                        },
                    )
                )
        except Exception as e:
            logger.warning(
                "wiki_lint_pair_failed",
                slug_a=slug_a,
                slug_b=slug_b,
                error=str(e),
            )

    return issues


def _collect_semantic_candidate_pairs(
    page_metas: dict[str, dict],
) -> list[tuple[str, str]]:
    """收集可能存在矛盾的页面候选对

    规则：
    1. 共享至少一个 tag
    2. 一个页面 outlink 到另一个
    3. 排除自身对、重复对

    Returns:
        [(slug_a, slug_b), ...] 已去重
    """
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []

    # 规则 1: 共享 tag
    tag_to_slugs: dict[str, list[str]] = defaultdict(list)
    for slug, meta in page_metas.items():
        for tag in meta.get("tags", []) or []:
            tag_to_slugs[tag].append(slug)
    for slugs in tag_to_slugs.values():
        if len(slugs) < 2:
            continue
        for i, a in enumerate(slugs):
            for b in slugs[i + 1 :]:
                key = tuple(sorted([a, b]))
                if key not in seen:
                    seen.add(key)
                    pairs.append((a, b))

    # 规则 2: outlink
    for slug in page_metas:
        for ol in get_outlinks(slug):
            other = ol.target_slug
            if other == slug or other not in page_metas:
                continue
            key = tuple(sorted([slug, other]))
            if key not in seen:
                seen.add(key)
                pairs.append((slug, other))

    return pairs


async def _llm_detect_conflicts(
    llm,
    settings,
    slug_a: str,
    content_a: str,
    slug_b: str,
    content_b: str,
) -> list[dict]:
    """让 LLM 判断两个页面之间是否存在事实矛盾

    Returns:
        [{summary, evidence_a, evidence_b}]
    """
    from app.core.llm import ChatMessage

    # 截断过长的内容（控制 token 成本）
    _, body_a = _split_frontmatter(content_a)
    _, body_b = _split_frontmatter(content_b)
    body_a = body_a[:2000]
    body_b = body_b[:2000]

    system = (
        "你是知识库审校员。判断两份 wiki 页面是否存在事实层面的矛盾。"
        "只关注可验证的事实冲突（数值、配置、流程、状态、关系），"
        "忽略措辞差异、详略差异、视角差异。"
        "返回 JSON 数组，每项含 summary/evidence_a/evidence_b 三个字段。"
        "无矛盾时返回 []。"
    )
    prompt = f"""请检查以下两个 wiki 页面是否存在事实矛盾。

# 页面 A（slug: {slug_a}）
{body_a}

# 页面 B（slug: {slug_b}）
{body_b}

# 输出格式
返回 JSON 数组（无矛盾时返回 []），每项形如：
{{"summary": "冲突概述", "evidence_a": "A 中的证据", "evidence_b": "B 中的证据"}}

只返回 JSON，不要其他文字。
"""
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=prompt),
    ]
    try:
        resp = await llm.chat(
            messages=messages,
            temperature=0.0,
            max_tokens=settings.llm_max_tokens,
        )
    except Exception as e:
        logger.warning(
            "wiki_lint_llm_call_failed",
            slug_a=slug_a,
            slug_b=slug_b,
            error=str(e),
        )
        return []
    text = (resp.text or "").strip()
    return _parse_json_array(text)


def _parse_json_array(text: str) -> list[dict]:
    """容错解析 LLM 返回的 JSON 数组

    处理：
    - 去除 ```json ... ``` 代码块围栏
    - 提取首个 [ 到末尾 ] 之间的内容
    - 解析失败返回空列表
    """
    # 去除代码块围栏
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return []
    try:
        result = json.loads(text[start : end + 1])
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict)]
    except json.JSONDecodeError:
        pass
    return []


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
