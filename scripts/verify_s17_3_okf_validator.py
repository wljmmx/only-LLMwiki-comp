"""S17-3 OKF Validator + Lint 扩展 验证脚本（P2-1）

验证 backend/app/knowledge/okf_validator.py + wiki_lint 集成：
1. validate_concept: 合规页面无 finding
2. validate_concept: 缺 frontmatter → error
3. validate_concept: 缺 type → error
4. validate_concept: type 空 → error
5. validate_concept: 缺推荐字段 → warn
6. validate_reserved_file: index.md type 正确无 finding
7. validate_reserved_file: index.md type 错误 → warn
8. validate_reserved_file: log.md type 错误 → warn
9. validate_wiki: 空 wiki valid=True
10. validate_wiki: 含违规页面 → errors/warnings 统计
11. validate_wiki: 内部 index/log 守职责检查
12. validate_bundle: 合规 bundle valid=True
13. validate_bundle: 缺 type 的概念文件 → error
14. validate_bundle: 保留文件 type 错误 → warn
15. validate_bundle: 目录不存在 → error
16. to_lint_issues: 转为 LintIssue 兼容结构
17. wiki_lint.lint_all 集成 TYPE_OKF_VIOLATION
18. OKFValidationResult.to_dict 与 okf validate CLI 兼容
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_s173_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

import app.storage.version_control as vc_mod

vc_mod.DB_PATH = TMP_DIR / "versions.db"
import app.knowledge.wikilink as wl_mod

wl_mod.DB_PATH = TMP_DIR / "events.db"
import app.knowledge.wiki_drift as wd_mod

wd_mod.DB_PATH = TMP_DIR / "events.db"

from app.knowledge.okf_validator import (  # noqa: E402
    CODE_EMPTY_TYPE,
    CODE_INDEX_WRONG_TYPE,
    CODE_LOG_WRONG_TYPE,
    CODE_MISSING_FRONTMATTER,
    CODE_MISSING_RECOMMENDED,
    CODE_MISSING_TYPE,
    to_lint_issues,
    validate_bundle,
    validate_concept,
    validate_reserved_file,
    validate_wiki,
)
from app.knowledge.wiki_lint import (  # noqa: E402
    TYPE_OKF_VIOLATION,
    lint_all,
)
from app.storage.version_control import get_version_control  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def main() -> int:
    print("=" * 70)
    print("S17-3 OKF Validator + Lint 扩展验证 (P2-1)")
    print("=" * 70)

    # ── 1. validate_concept 合规页面 ──
    print("\n[1] validate_concept 合规页面")
    findings = validate_concept(
        "nginx-502",
        {
            "type": "incident",
            "title": "Nginx 502",
            "description": "网关错误",
            "resource": "opskg://doc/abc",
            "tags": ["nginx"],
            "timestamp": "2026-07-10T10:00:00Z",
        },
        "body content",
    )
    check(
        len(findings) == 0,
        f"合规页面无 finding (got {len(findings)}: {[f.code for f in findings]})",
    )

    # ── 2. validate_concept 缺 frontmatter ──
    print("\n[2] validate_concept 缺 frontmatter")
    findings = validate_concept("bad-page", {}, "no frontmatter body")
    error_codes = [f.code for f in findings if f.level == "error"]
    check(
        CODE_MISSING_FRONTMATTER in error_codes,
        f"缺 frontmatter → error (got errors={error_codes})",
    )

    # ── 3. validate_concept 缺 type ──
    print("\n[3] validate_concept 缺 type")
    findings = validate_concept(
        "no-type",
        {"title": "No Type", "description": "desc", "resource": "r", "tags": [], "timestamp": "t"},
        "---\n# body",
    )
    error_codes = [f.code for f in findings if f.level == "error"]
    check(
        CODE_MISSING_TYPE in error_codes,
        f"缺 type → error (got errors={error_codes})",
    )

    # ── 4. validate_concept type 空 ──
    print("\n[4] validate_concept type 空")
    findings = validate_concept(
        "empty-type",
        {"type": "  ", "title": "t", "description": "d", "resource": "r", "tags": [], "timestamp": "ts"},
        "body",
    )
    error_codes = [f.code for f in findings if f.level == "error"]
    check(
        CODE_EMPTY_TYPE in error_codes,
        f"type 空 → error (got errors={error_codes})",
    )

    # ── 5. validate_concept 缺推荐字段 ──
    print("\n[5] validate_concept 缺推荐字段")
    findings = validate_concept(
        "minimal",
        {"type": "concept"},
        "body",
    )
    warn_codes = [f.code for f in findings if f.level == "warn"]
    check(
        all(
            CODE_MISSING_RECOMMENDED in warn_codes for _ in [0]
        ) and len([c for c in warn_codes if c == CODE_MISSING_RECOMMENDED]) >= 4,
        f"缺推荐字段 → warn (got {len(warn_codes)} warnings)",
    )
    check(
        len([f for f in findings if f.level == "error"]) == 0,
        "缺推荐字段不产生 error (permissive)",
    )

    # ── 6-8. validate_reserved_file ──
    print("\n[6-8] validate_reserved_file 保留文件")
    findings = validate_reserved_file("index.md", {"type": "index"})
    check(
        len(findings) == 0,
        f"index.md type=index 无 finding (got {len(findings)})",
    )

    findings = validate_reserved_file("index.md", {"type": "concept"})
    codes = [f.code for f in findings]
    check(
        CODE_INDEX_WRONG_TYPE in codes,
        f"index.md type 错误 → warn (got {codes})",
    )
    check(
        all(f.level == "warn" for f in findings),
        "index.md type 错误是 warn 不是 error (permissive)",
    )

    findings = validate_reserved_file("log.md", {"type": "index"})
    codes = [f.code for f in findings]
    check(
        CODE_LOG_WRONG_TYPE in codes,
        f"log.md type 错误 → warn (got {codes})",
    )

    findings = validate_reserved_file("other.md", {"type": "x"})
    check(
        len(findings) == 0,
        f"非保留文件无检查 (got {len(findings)})",
    )

    # ── 9-11. validate_wiki 内部 wiki ──
    print("\n[9-11] validate_wiki 内部 wiki")
    vc = get_version_control()

    # 空 wiki
    result = validate_wiki()
    check(
        result.valid is True,
        f"空 wiki valid=True (got valid={result.valid}, errors={result.errors})",
    )
    check(
        result.concept_count == 0,
        f"空 wiki concept_count=0 (got {result.concept_count})",
    )

    # 灌入合规页面 + 违规页面
    vc.save_version(
        doc_key="wiki:good-page",
        title="Good",
        content=(
            "---\nslug: good-page\ntype: concept\ntitle: Good\ndescription: d\n"
            "resource: opskg://wiki/good-page\ntags: [t]\ntimestamp: 2026-07-10T10:00:00Z\n"
            "review_status: auto\n---\n\n# Good\n\n## 概述\ndesc\n\n## 来源\n- s\n"
        ),
        author="test",
        change_summary="seed good",
    )
    vc.save_version(
        doc_key="wiki:bad-page",
        title="Bad",
        content=(
            "---\nslug: bad-page\ntitle: Bad\nreview_status: auto\n---\n\n# Bad\n"
        ),  # 缺 type、缺推荐字段
        author="test",
        change_summary="seed bad",
    )

    result = validate_wiki()
    check(
        result.concept_count == 2,
        f"2 个概念 (got {result.concept_count})",
    )
    check(
        result.errors >= 1,
        f"bad-page 缺 type 产生 error (got errors={result.errors})",
    )
    check(
        result.warnings >= 4,
        f"推荐字段缺失产生 warning (got warnings={result.warnings})",
    )
    check(
        result.valid is False,
        f"有 error 时 valid=False (got valid={result.valid})",
    )

    # ── 12-15. validate_bundle 目录树 ──
    print("\n[12-15] validate_bundle 目录树")
    bundle_dir = TMP_DIR / "test_bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "incidents").mkdir()

    # 合规概念文件
    (bundle_dir / "concepts" / "good.md").write_text(
        "---\ntype: concept\ntitle: Good\ndescription: d\nresource: r\n"
        "tags: [t]\ntimestamp: ts\n---\n\n# Good\n",
        encoding="utf-8",
    )
    # 违规概念文件（缺 type）
    (bundle_dir / "incidents" / "bad.md").write_text(
        "---\ntitle: Bad\n---\n\n# Bad\n",
        encoding="utf-8",
    )
    # 合规 index.md
    (bundle_dir / "index.md").write_text(
        "---\ntype: index\ntitle: Idx\n---\n\n# Index\n",
        encoding="utf-8",
    )
    # 违规 log.md（type 错误）
    (bundle_dir / "log.md").write_text(
        "---\ntype: concept\ntitle: Log\n---\n\n# Log\n",
        encoding="utf-8",
    )

    result = validate_bundle(bundle_dir)
    check(
        result.concept_count == 2,
        f"bundle 2 个概念 (got {result.concept_count})",
    )
    check(
        result.errors >= 1,
        f"bad.md 缺 type → error (got errors={result.errors})",
    )
    check(
        any(f.code == CODE_LOG_WRONG_TYPE for f in result.findings),
        "log.md type 错误被检出",
    )
    check(
        result.valid is False,
        "有 error 时 valid=False",
    )

    # 目录不存在
    result = validate_bundle(TMP_DIR / "nonexistent")
    check(
        result.valid is False and result.errors >= 1,
        f"目录不存在 → error (got valid={result.valid}, errors={result.errors})",
    )

    # 合规 bundle（全部字段齐全）
    clean_bundle = TMP_DIR / "clean_bundle"
    (clean_bundle / "concepts").mkdir(parents=True)
    (clean_bundle / "concepts" / "c1.md").write_text(
        "---\ntype: concept\ntitle: C1\ndescription: d\nresource: r\n"
        "tags: [t]\ntimestamp: ts\n---\n\n# C1\n",
        encoding="utf-8",
    )
    (clean_bundle / "index.md").write_text(
        "---\ntype: index\ntitle: Idx\n---\n\n# Index\n",
        encoding="utf-8",
    )
    result = validate_bundle(clean_bundle)
    check(
        result.valid is True and result.errors == 0,
        f"合规 bundle valid=True (got valid={result.valid}, errors={result.errors})",
    )

    # ── 16. to_lint_issues ──
    print("\n[16] to_lint_issues 转换")
    result = validate_bundle(bundle_dir)
    issues = to_lint_issues(result)
    check(
        len(issues) == len(result.findings),
        f"issue 数量一致 (got {len(issues)} vs {len(result.findings)})",
    )
    if issues:
        check(
            all("type" in i and "severity" in i and "slug" in i for i in issues),
            "每个 issue 含 type/severity/slug",
        )
        check(
            all(i["type"] == "okf_violation" for i in issues),
            "type 统一为 okf_violation",
        )
        check(
            all("okf_code" in i["detail"] for i in issues),
            "detail 含 okf_code",
        )

    # ── 17. wiki_lint.lint_all 集成 ──
    print("\n[17] wiki_lint.lint_all 集成 TYPE_OKF_VIOLATION")
    # 清空 wiki 重新灌入已知数据
    vc.delete_all("wiki:good-page")
    vc.delete_all("wiki:bad-page")
    vc.save_version(
        doc_key="wiki:no-type-page",
        title="No Type",
        content="---\nslug: no-type-page\ntitle: No Type\n---\n\n# No Type\n",
        author="test",
        change_summary="seed",
    )
    report = lint_all(include_stale=False)
    okf_issues = [i for i in report.issues if i.type == TYPE_OKF_VIOLATION]
    check(
        len(okf_issues) > 0,
        f"lint_all 产生 TYPE_OKF_VIOLATION issue (got {len(okf_issues)})",
    )
    check(
        any(i.severity == "error" for i in okf_issues),
        "含 error 级 OKF 违规",
    )
    check(
        all("okf_code" in i.detail for i in okf_issues),
        "OKF issue detail 含 okf_code",
    )

    # ── 18. OKFValidationResult.to_dict 兼容性 ──
    print("\n[18] OKFValidationResult.to_dict 兼容 okf validate CLI")
    result = validate_wiki()
    d = result.to_dict()
    required_keys = {"okf_version", "valid", "errors", "warnings", "concept_count", "findings"}
    check(
        required_keys.issubset(d.keys()),
        f"含必需字段 (got keys={set(d.keys())})",
    )
    check(
        d["okf_version"] == "0.1",
        f"okf_version=0.1 (got {d['okf_version']})",
    )
    check(
        isinstance(d["valid"], bool),
        f"valid 是 bool (got {type(d['valid']).__name__})",
    )
    check(
        isinstance(d["findings"], list),
        "findings 是 list",
    )
    if d["findings"]:
        f0 = d["findings"][0]
        check(
            {"level", "code", "message"}.issubset(f0.keys()),
            "finding 含 level/code/message",
        )

    # ── 总结 ──
    print("\n" + "=" * 70)
    print(f"总计: {PASS} PASS / {FAIL} FAIL")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
