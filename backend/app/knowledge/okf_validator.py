"""OKF v0.1 Validator（P2-1）

校验 wiki / bundle 是否符合 OKF v0.1 三硬性约束 + 推荐字段。

三硬性约束（https://openknowledgeformat.com/what-is-okf）：
1. 每个非保留概念文件含可解析 YAML frontmatter
2. frontmatter 含非空 `type` 字段
3. 保留文件 index.md / log.md 守职责（存在时 type 正确）

推荐字段（warn，不阻断 valid）：
- title / description / resource / tags / timestamp

输出：
- OKFValidationResult：与 `okf validate` CLI 兼容的 JSON 结构
- 集成到 wiki_lint：转为 LintIssue（TYPE_OKF_VIOLATION）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from app.knowledge.okf_adapter import OKF_RECOMMENDED_FIELDS, OKF_VERSION
from app.knowledge.wiki_index import _parse_frontmatter, list_wiki_pages
from app.storage.version_control import get_version_control

logger = structlog.get_logger()

# OKF 违规子类型码
CODE_MISSING_FRONTMATTER = "missing_frontmatter"
CODE_MISSING_TYPE = "missing_type"
CODE_EMPTY_TYPE = "empty_type"
CODE_INDEX_WRONG_TYPE = "index_wrong_type"
CODE_LOG_WRONG_TYPE = "log_wrong_type"
CODE_RESERVED_AS_CONCEPT = "reserved_as_concept"
CODE_MISSING_RECOMMENDED = "missing_recommended"

# OKF 保留文件与其期望 type
RESERVED_EXPECTED_TYPE = {
    "index.md": "index",
    "log.md": "log",
}


@dataclass
class OKFFinding:
    """单条 OKF 校验发现"""

    level: str  # error | warn | info
    code: str
    file: str = ""  # 相对路径或 slug
    message: str = ""
    field: str = ""  # 涉及字段（推荐字段缺失时）

    def to_dict(self) -> dict:
        d = {"level": self.level, "code": self.code, "message": self.message}
        if self.file:
            d["file"] = self.file
        if self.field:
            d["field"] = self.field
        return d


@dataclass
class OKFValidationResult:
    """OKF 校验结果（与 `okf validate` CLI 兼容）"""

    okf_version: str = OKF_VERSION
    valid: bool = True
    errors: int = 0
    warnings: int = 0
    concept_count: int = 0
    findings: list[OKFFinding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "okf_version": self.okf_version,
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "concept_count": self.concept_count,
            "findings": [f.to_dict() for f in self.findings],
        }

    def add_error(self, code: str, message: str, file: str = "", field: str = "") -> None:
        self.findings.append(
            OKFFinding(level="error", code=code, file=file, message=message, field=field)
        )
        self.errors += 1
        self.valid = False

    def add_warning(
        self, code: str, message: str, file: str = "", field: str = ""
    ) -> None:
        self.findings.append(
            OKFFinding(level="warn", code=code, file=file, message=message, field=field)
        )
        self.warnings += 1


# ────────── 校验：单个概念 ──────────


def validate_concept(
    slug: str, frontmatter: dict, body: str
) -> list[OKFFinding]:
    """校验单个概念文件的 OKF 合规性

    Args:
        slug: 页面 slug
        frontmatter: 已解析的 frontmatter dict
        body: 正文

    Returns:
        OKFFinding 列表
    """
    findings: list[OKFFinding] = []

    # 约束 1: frontmatter 存在且可解析
    # 调用方传入的 frontmatter 已解析；若为空 dict 视为缺失
    if frontmatter is None or (
        not frontmatter and body and not body.strip().startswith("---")
    ):
        findings.append(
            OKFFinding(
                level="error",
                code=CODE_MISSING_FRONTMATTER,
                file=slug,
                message="概念文件缺少可解析的 YAML frontmatter",
            )
        )
        return findings  # 后续检查无意义

    # 约束 2: type 非空
    type_val = frontmatter.get("type")
    if type_val is None:
        findings.append(
            OKFFinding(
                level="error",
                code=CODE_MISSING_TYPE,
                file=slug,
                message="frontmatter 缺少 type 字段",
                field="type",
            )
        )
    elif not str(type_val).strip():
        findings.append(
            OKFFinding(
                level="error",
                code=CODE_EMPTY_TYPE,
                file=slug,
                message="frontmatter type 字段为空",
                field="type",
            )
        )

    # 推荐字段缺失（warn）
    for field_name in OKF_RECOMMENDED_FIELDS:
        val = frontmatter.get(field_name)
        if val is None or (isinstance(val, str) and not val.strip()) or val == []:
            findings.append(
                OKFFinding(
                    level="warn",
                    code=CODE_MISSING_RECOMMENDED,
                    file=slug,
                    message=f"缺少推荐字段: {field_name}",
                    field=field_name,
                )
            )

    return findings


def validate_reserved_file(
    filename: str, frontmatter: dict
) -> list[OKFFinding]:
    """校验保留文件（index.md / log.md）是否守职责

    Args:
        filename: 文件名（index.md / log.md）
        frontmatter: 已解析的 frontmatter

    Returns:
        OKFFinding 列表
    """
    findings: list[OKFFinding] = []
    expected_type = RESERVED_EXPECTED_TYPE.get(filename)
    if expected_type is None:
        return findings

    actual_type = frontmatter.get("type")
    if actual_type != expected_type:
        code = (
            CODE_INDEX_WRONG_TYPE
            if filename == "index.md"
            else CODE_LOG_WRONG_TYPE
        )
        findings.append(
            OKFFinding(
                level="warn",
                code=code,
                file=filename,
                message=f"{filename} 的 type 应为 '{expected_type}'，实际为 '{actual_type}'",
                field="type",
            )
        )
    return findings


# ────────── 校验：内部 wiki（DB）──────────


def validate_wiki() -> OKFValidationResult:
    """校验整个 OpsKG 内部 wiki（DB 存储的页面）的 OKF 合规性

    Returns:
        OKFValidationResult
    """
    result = OKFValidationResult()
    vc = get_version_control()

    pages = list_wiki_pages(limit=10000)
    result.concept_count = len(pages)

    for p in pages:
        slug = p["slug"]
        try:
            latest = vc.get_latest(f"wiki:{slug}")
            if not latest:
                continue
            meta, body = _parse_frontmatter(latest["content"])
            findings = validate_concept(slug, meta, body)
            for f in findings:
                if f.level == "error":
                    result.add_error(f.code, f.message, f.file, f.field)
                else:
                    result.add_warning(f.code, f.message, f.file, f.field)
        except Exception as e:
            result.add_error(
                CODE_MISSING_FRONTMATTER,
                f"解析失败: {e}",
                file=slug,
            )

    # 校验内部保留页面：wiki:index 与 wiki:log
    for doc_key, filename in [
        ("wiki:index", "index.md"),
        ("wiki:log", "log.md"),
    ]:
        latest = vc.get_latest(doc_key)
        if latest:
            meta, _ = _parse_frontmatter(latest["content"])
            for f in validate_reserved_file(filename, meta):
                if f.level == "error":
                    result.add_error(f.code, f.message, f.file, f.field)
                else:
                    result.add_warning(f.code, f.message, f.file, f.field)

    logger.info(
        "okf_validate_wiki_done",
        valid=result.valid,
        errors=result.errors,
        warnings=result.warnings,
        concepts=result.concept_count,
    )
    return result


# ────────── 校验：bundle 目录树 ──────────


def validate_bundle(bundle_dir: Path | str) -> OKFValidationResult:
    """校验 OKF bundle 目录树的合规性

    Args:
        bundle_dir: bundle 根目录

    Returns:
        OKFValidationResult
    """
    bundle_dir = Path(bundle_dir)
    result = OKFValidationResult()

    if not bundle_dir.exists() or not bundle_dir.is_dir():
        result.add_error(
            "bundle_not_found",
            f"bundle 目录不存在: {bundle_dir}",
        )
        return result

    # 收集所有 .md 文件
    md_files = sorted(bundle_dir.rglob("*.md"))
    concept_files = [f for f in md_files if f.name not in RESERVED_EXPECTED_TYPE]
    reserved_files = [f for f in md_files if f.name in RESERVED_EXPECTED_TYPE]

    result.concept_count = len(concept_files)

    if not concept_files and not reserved_files:
        result.add_warning("empty_bundle", "bundle 中无任何 .md 文件")

    # 校验概念文件
    for f in concept_files:
        rel = str(f.relative_to(bundle_dir))
        try:
            content = f.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(content)
            findings = validate_concept(rel, meta, body)
            for finding in findings:
                # 用相对路径作为 file
                finding.file = rel
                if finding.level == "error":
                    result.add_error(finding.code, finding.message, finding.file, finding.field)
                else:
                    result.add_warning(finding.code, finding.message, finding.file, finding.field)
        except Exception as e:
            result.add_error(
                CODE_MISSING_FRONTMATTER,
                f"文件解析失败: {e}",
                file=rel,
            )

    # 校验保留文件
    for f in reserved_files:
        try:
            content = f.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(content)
            findings = validate_reserved_file(f.name, meta)
            for finding in findings:
                if finding.level == "error":
                    result.add_error(finding.code, finding.message, f.name, finding.field)
                else:
                    result.add_warning(finding.code, finding.message, f.name, finding.field)
        except Exception as e:
            result.add_error(
                "reserved_unparseable",
                f"{f.name} 解析失败: {e}",
                file=f.name,
            )

    logger.info(
        "okf_validate_bundle_done",
        bundle=str(bundle_dir),
        valid=result.valid,
        errors=result.errors,
        warnings=result.warnings,
        concepts=result.concept_count,
    )
    return result


# ────────── 集成到 wiki_lint：转 LintIssue ──────────


def to_lint_issues(result: OKFValidationResult) -> list[dict]:
    """把 OKFValidationResult 转为 wiki_lint 的 LintIssue 兼容结构

    供 wiki_lint.lint_all() 调用，作为 TYPE_OKF_VIOLATION issue 注入。

    Returns:
        [{type, severity, slug, message, detail}]
    """
    issues: list[dict] = []
    for f in result.findings:
        # 从 file 路径反推 slug（bundle 路径取末段，或直接用 file 字段）
        slug = f.file.rsplit("/", 1)[-1].replace(".md", "") if f.file else ""
        issues.append(
            {
                "type": "okf_violation",
                "severity": "error" if f.level == "error" else "warn",
                "slug": slug,
                "message": f"[OKF/{f.code}] {f.message}",
                "detail": {
                    "okf_code": f.code,
                    "okf_field": f.field,
                    "okf_file": f.file,
                    "okf_version": OKF_VERSION,
                },
            }
        )
    return issues
