"""S17-1 OKF 适配层 验证脚本

验证 backend/app/knowledge/okf_adapter.py 的核心能力：
1. OKF_VERSION 正确导出
2. wikilink_to_okf: [[slug]] -> [display](/{dir}/{slug}.md)
3. wikilink_to_okf: [[slug|显示文本]] 自定义显示
4. wikilink_to_okf: [[#anchor]] 页面内锚点保留
5. wikilink_to_okf: 断链（slug 未在映射中）保留纯显示文本（permissive）
6. okf_link_to_wikilink: 反向转换可逆
7. okf_link_to_wikilink: 非 .md 链接保留不变（permissive）
8. extract_description: 从「## 概述」章节抽取首段
9. extract_description: 无概述章节时取首段
10. extract_description: 超长截断加省略号
11. derive_resource: sources.doc_id -> opskg://doc/{doc_id}
12. derive_resource: properties.ip -> host://{ip}
13. derive_resource: 兜底 opskg://wiki/{slug}
14. normalize_frontmatter_for_okf: 补全 description/resource/timestamp
15. normalize_frontmatter_for_okf: type 缺失默认 concept
16. normalize_frontmatter_for_okf: 保留扩展字段（permissive）
17. type_dir_for: 已知类型映射正确
18. type_dir_for: 未知类型兜底 concepts
19. slug_from_concept_id: 路径反推 slug
20. export_bundle: 空 wiki 导出空 bundle（含 index.md）
21. export_bundle: 单页面导出（frontmatter 规范化 + 链接转换 + 目录结构）
22. export_bundle: index.md 含渐进披露导航
23. export_bundle: log.md 从 VersionControl 聚合
24. import_bundle: 容忍未知 type（默认 concept）
25. import_bundle: 容忍缺失推荐字段
26. import_bundle: 反向链接转换 OKF MD 链接 -> [[wikilink]]
27. import_bundle: 已存在页面默认跳过（overwrite=False）
28. import_bundle: overwrite=True 覆盖
29. export_bundle_tarball: tarball 含 okf-bundle/ 根目录
30. 端到端：导出 -> 导入 -> 内容一致（双链往返）
31. bundle_summary: 统计正确
32. list_bundle_concepts: 排除保留文件
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_s171_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

# 重定向 DB 路径到临时目录
import app.storage.version_control as vc_mod

vc_mod.DB_PATH = TMP_DIR / "versions.db"
import app.knowledge.wikilink as wl_mod

wl_mod.DB_PATH = TMP_DIR / "events.db"
import app.knowledge.wiki_drift as wd_mod

wd_mod.DB_PATH = TMP_DIR / "events.db"

from app.knowledge.okf_adapter import (  # noqa: E402
    OKF_VERSION,
    OKF_RECOMMENDED_FIELDS,
    TYPE_TO_DIR,
    build_okf_link,
    bundle_summary,
    derive_resource,
    export_bundle,
    export_bundle_tarball,
    extract_description,
    import_bundle,
    list_bundle_concepts,
    normalize_frontmatter_for_okf,
    okf_link_to_wikilink,
    render_log_md,
    slug_from_concept_id,
    type_dir_for,
    wikilink_to_okf,
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
    print("S17-1 OKF 适配层验证")
    print("=" * 70)

    # ── 1. 版本与常量 ──
    print("\n[1] 版本与常量")
    check(OKF_VERSION == "0.1", f"OKF_VERSION == '0.1' (got {OKF_VERSION})")
    check(
        set(OKF_RECOMMENDED_FIELDS)
        == {"title", "description", "resource", "tags", "timestamp"},
        f"OKF_RECOMMENDED_FIELDS 完整 (got {OKF_RECOMMENDED_FIELDS})",
    )
    check(
        TYPE_TO_DIR["incident"] == "incidents",
        f"TYPE_TO_DIR incident->incidents (got {TYPE_TO_DIR.get('incident')})",
    )
    check(
        TYPE_TO_DIR["runbook"] == "runbooks",
        f"TYPE_TO_DIR runbook->runbooks (got {TYPE_TO_DIR.get('runbook')})",
    )

    # ── 2. 双链转换：[[wikilink]] -> OKF 标准 MD 链接 ──
    print("\n[2] wikilink_to_okf 双链转换")
    slug_map = {"nginx-502": "incident", "nginx-timeout": "concept"}

    out = wikilink_to_okf("see [[nginx-502]]", slug_map)
    check(
        out == "see [nginx-502](/incidents/nginx-502.md)",
        f"[[slug]] -> [slug](/incidents/slug.md) (got {out!r})",
    )

    out = wikilink_to_okf("[[nginx-timeout|超时调优]]", slug_map)
    check(
        out == "[超时调优](/concepts/nginx-timeout.md)",
        f"[[slug|显示]] -> [显示](/dir/slug.md) (got {out!r})",
    )

    out = wikilink_to_okf("[[#anchor]] section", slug_map)
    check(
        "[[#anchor]]" in out,
        f"[[#anchor]] 保留 (got {out!r})",
    )

    out = wikilink_to_okf("[[unknown-slug]] here", slug_map)
    check(
        out == "unknown-slug here",
        f"断链保留纯显示文本 (got {out!r})",
    )

    # ── 3. 反向转换 OKF MD 链接 -> [[wikilink]] ──
    print("\n[3] okf_link_to_wikilink 反向转换")
    back = okf_link_to_wikilink("[nginx-502](/incidents/nginx-502.md)")
    check(
        back == "[[nginx-502]]",
        f"标准链接 -> [[slug]] (got {back!r})",
    )

    back = okf_link_to_wikilink("[超时调优](/concepts/nginx-timeout.md)")
    check(
        back == "[[nginx-timeout|超时调优]]",
        f"自定义显示 -> [[slug|显示]] (got {back!r})",
    )

    # 非 .md 链接保留
    back = okf_link_to_wikilink("[external](https://example.com)")
    check(
        back == "[external](https://example.com)",
        f"外部 URL 保留不变 (got {back!r})",
    )

    # 往返可逆
    orig = "see [[nginx-502]] and [[nginx-timeout|tuning]]"
    roundtrip = okf_link_to_wikilink(wikilink_to_okf(orig, slug_map))
    check(
        roundtrip == orig,
        f"双链往返可逆 (got {roundtrip!r})",
    )

    # ── 4. extract_description ──
    print("\n[4] extract_description 描述抽取")
    body = "# Nginx 502\n\n## 概述\nNginx 502 是网关错误。常见原因是上游服务宕机。\n\n## 成因\n"
    desc = extract_description(body)
    check(
        "网关错误" in desc,
        f"从概述章节抽取 (got {desc!r})",
    )

    body2 = "# Title\n\n这是首段内容。\n\n## 章节\n后续内容。"
    desc2 = extract_description(body2)
    check(
        "首段内容" in desc2,
        f"无概述时取首段 (got {desc2!r})",
    )

    long_body = "# T\n\n## 概述\n" + "A" * 300 + "\n"
    desc3 = extract_description(long_body, max_len=50)
    check(
        desc3.endswith("...") and len(desc3) <= 53,
        f"超长截断加省略号 (got len={len(desc3)}, ends_with_ellipsis={desc3.endswith('...')})",
    )

    # ── 5. derive_resource ──
    print("\n[5] derive_resource 资源 URI 推导")
    r = derive_resource({"sources": [{"doc_id": "abc123"}]})
    check(
        r == "opskg://doc/abc123",
        f"sources.doc_id -> opskg://doc/ (got {r!r})",
    )

    r = derive_resource({"properties": {"ip": "10.0.0.1"}})
    check(
        r == "host://10.0.0.1",
        f"properties.ip -> host:// (got {r!r})",
    )

    r = derive_resource({"slug": "nginx-502"})
    check(
        r == "opskg://wiki/nginx-502",
        f"兜底 opskg://wiki/{{slug}} (got {r!r})",
    )

    r = derive_resource({})
    check(
        r == "",
        f"无信息时返回空 (got {r!r})",
    )

    # ── 6. normalize_frontmatter_for_okf ──
    print("\n[6] normalize_frontmatter_for_okf frontmatter 规范化")
    meta = normalize_frontmatter_for_okf(
        {"slug": "nginx-502", "type": "incident", "title": "Nginx 502",
         "updated_at": "2026-07-10T10:00:00Z"},
        "# Nginx 502\n\n## 概述\n网关错误。\n",
        "nginx-502",
    )
    check(
        meta.get("description") == "网关错误。",
        f"补全 description (got {meta.get('description')!r})",
    )
    check(
        meta.get("resource") == "opskg://wiki/nginx-502",
        f"补全 resource (got {meta.get('resource')!r})",
    )
    check(
        meta.get("timestamp") == "2026-07-10T10:00:00Z",
        f"补全 timestamp=updated_at (got {meta.get('timestamp')!r})",
    )
    check(
        meta.get("type") == "incident",
        f"保留原 type (got {meta.get('type')!r})",
    )

    # type 缺失默认
    meta2 = normalize_frontmatter_for_okf({}, "body", "test")
    check(
        meta2.get("type") == "concept",
        f"type 缺失默认 concept (got {meta2.get('type')!r})",
    )
    check(
        meta2.get("title") == "test",
        f"title 缺失兜底 slug (got {meta2.get('title')!r})",
    )

    # 保留扩展字段
    meta3 = normalize_frontmatter_for_okf(
        {"type": "host", "review_status": "approved", "stale": False, "custom": "x"},
        "body", "h1",
    )
    check(
        meta3.get("review_status") == "approved"
        and meta3.get("custom") == "x",
        f"保留扩展字段（permissive）(got review_status={meta3.get('review_status')}, custom={meta3.get('custom')})",
    )

    # ── 7. type_dir_for / slug_from_concept_id ──
    print("\n[7] 目录映射工具")
    check(type_dir_for("incident") == "incidents", "incident->incidents")
    check(type_dir_for("runbook") == "runbooks", "runbook->runbooks")
    check(type_dir_for("service") == "services", "service->services")
    check(type_dir_for("host") == "hosts", "host->hosts")
    check(type_dir_for("concept") == "concepts", "concept->concepts")
    check(type_dir_for("entity") == "entities", "entity->entities")
    check(type_dir_for("unknown") == "concepts", "未知类型兜底 concepts")

    check(
        slug_from_concept_id("incidents/nginx-502") == "nginx-502",
        "slug_from_concept_id 取末段",
    )
    check(
        slug_from_concept_id("simple-slug") == "simple-slug",
        "slug_from_concept_id 无路径时返回原值",
    )

    # ── 8. export_bundle 端到端 ──
    print("\n[8] export_bundle 导出")
    vc = get_version_control()

    # 空 wiki 导出
    empty_dir = TMP_DIR / "empty_bundle"
    result = export_bundle(empty_dir)
    check(
        result.pages_exported == 0,
        f"空 wiki 导出 0 页 (got {result.pages_exported})",
    )
    check(
        (empty_dir / "index.md").exists(),
        "空 bundle 仍生成 index.md",
    )
    check(
        result.index_written is True,
        "index_written 标志为 True",
    )

    # 准备测试数据：2 个 wiki 页面 + 双链
    vc.save_version(
        doc_key="wiki:nginx-502",
        title="Nginx 502 故障",
        content=(
            "---\n"
            "slug: nginx-502\n"
            "title: Nginx 502 故障\n"
            "type: incident\n"
            "tags: [nginx, 502]\n"
            "sources:\n"
            "  - doc_id: doc-001\n"
            "    title: Nginx 指南\n"
            "created_at: 2026-07-10T10:00:00Z\n"
            "updated_at: 2026-07-10T10:00:00Z\n"
            "review_status: auto\n"
            "---\n\n"
            "# Nginx 502 故障\n\n"
            "## 概述\nNginx 502 是网关错误。参见 [[nginx-timeout]]。\n\n"
            "## 排查步骤\n1. 检查上游。\n\n"
            "## 来源\n- doc-001\n"
        ),
        author="test",
        change_summary="test seed",
    )
    vc.save_version(
        doc_key="wiki:nginx-timeout",
        title="Nginx 超时调优",
        content=(
            "---\n"
            "slug: nginx-timeout\n"
            "title: Nginx 超时调优\n"
            "type: concept\n"
            "tags: [nginx, timeout]\n"
            "created_at: 2026-07-10T11:00:00Z\n"
            "updated_at: 2026-07-10T11:00:00Z\n"
            "review_status: auto\n"
            "---\n\n"
            "# Nginx 超时调优\n\n"
            "## 概述\nproxy_read_timeout 调优。回链 [[nginx-502|故障页]]。\n\n"
            "## 来源\n- doc-002\n"
        ),
        author="test",
        change_summary="test seed 2",
    )

    bundle_dir = TMP_DIR / "test_bundle"
    result = export_bundle(bundle_dir)
    check(
        result.pages_exported == 2,
        f"导出 2 页 (got {result.pages_exported})",
    )
    check(
        result.index_written and result.log_written,
        f"index.md + log.md 生成 (index={result.index_written}, log={result.log_written})",
    )
    check(
        len(result.errors) == 0,
        f"无导出错误 (got {result.errors})",
    )

    # 目录结构
    check(
        (bundle_dir / "incidents" / "nginx-502.md").exists(),
        "incident 页面在 incidents/ 目录",
    )
    check(
        (bundle_dir / "concepts" / "nginx-timeout.md").exists(),
        "concept 页面在 concepts/ 目录",
    )
    check(
        (bundle_dir / "index.md").exists(),
        "根 index.md 存在",
    )
    check(
        (bundle_dir / "log.md").exists(),
        "log.md 存在",
    )

    # 链接转换验证
    incident_md = (bundle_dir / "incidents" / "nginx-502.md").read_text("utf-8")
    check(
        "[nginx-timeout](/concepts/nginx-timeout.md)" in incident_md,
        f"incident 页面双链已转换为 OKF 链接",
    )
    # 检查正文中无残留 [[wikilink]]（frontmatter 中的 sources 不含 wikilink）
    incident_body = incident_md.split("---", 2)[-1] if incident_md.startswith("---") else incident_md
    check(
        "[[nginx-timeout]]" not in incident_body,
        f"incident 页面正文无残留 [[wikilink]]",
    )

    concept_md = (bundle_dir / "concepts" / "nginx-timeout.md").read_text("utf-8")
    check(
        "[故障页](/incidents/nginx-502.md)" in concept_md,
        f"concept 页面双链（含显示文本）已转换",
    )

    # frontmatter 规范化验证
    check(
        "description:" in incident_md,
        "incident 页面 frontmatter 含 description",
    )
    check(
        "resource:" in incident_md,
        "incident 页面 frontmatter 含 resource",
    )
    check(
        "timestamp:" in incident_md,
        "incident 页面 frontmatter 含 timestamp",
    )

    # index.md 渐进披露
    index_md = (bundle_dir / "index.md").read_text("utf-8")
    check(
        "type: index" in index_md,
        "index.md frontmatter type=index",
    )
    check(
        "[Nginx 502 故障](/incidents/nginx-502.md)" in index_md,
        "index.md 含标准 MD 链接导航",
    )
    check(
        "/concepts/nginx-timeout.md" in index_md,
        "index.md 含 concept 类型条目",
    )

    # ── 9. log.md ──
    print("\n[9] log.md 变更日志")
    log_md = (bundle_dir / "log.md").read_text("utf-8")
    check(
        "type: log" in log_md,
        "log.md frontmatter type=log",
    )
    check(
        "nginx-502" in log_md,
        "log.md 含页面变更记录",
    )
    check(
        "test seed" in log_md,
        "log.md 含 change_summary",
    )

    # ── 10. bundle_summary / list_bundle_concepts ──
    print("\n[10] bundle 摘要与列举")
    summary = bundle_summary(bundle_dir)
    check(
        summary["total"] == 2,
        f"summary.total=2 (got {summary['total']})",
    )
    check(
        summary["by_type"].get("incident") == 1
        and summary["by_type"].get("concept") == 1,
        f"summary.by_type 类型分布 (got {summary['by_type']})",
    )
    check(
        summary["has_index"] is True and summary["has_log"] is True,
        f"has_index/has_log (got {summary['has_index']}, {summary['has_log']})",
    )
    check(
        summary["okf_version"] == "0.1",
        f"okf_version=0.1 (got {summary['okf_version']})",
    )

    concepts = list_bundle_concepts(bundle_dir)
    check(
        len(concepts) == 2,
        f"list_bundle_concepts 排除保留文件后 2 个 (got {len(concepts)})",
    )
    concept_ids = {c.concept_id for c in concepts}
    check(
        "incidents/nginx-502" in concept_ids
        and "concepts/nginx-timeout" in concept_ids,
        f"concept_id 为文件路径 (got {concept_ids})",
    )

    # ── 11. import_bundle 导入（permissive）──
    print("\n[11] import_bundle 导入")
    # 清空 VC，模拟全新环境
    vc.delete_all("wiki:nginx-502")
    vc.delete_all("wiki:nginx-timeout")

    import_result = import_bundle(bundle_dir)
    check(
        import_result.pages_imported == 2,
        f"导入 2 页 (got {import_result.pages_imported})",
    )
    check(
        len(import_result.errors) == 0,
        f"无导入错误 (got {import_result.errors})",
    )
    check(
        set(import_result.slugs) == {"nginx-502", "nginx-timeout"},
        f"导入 slug 列表 (got {import_result.slugs})",
    )

    # 验证导入内容：双链应反向转换回 [[wikilink]]
    imported = vc.get_latest("wiki:nginx-502")
    check(
        imported is not None,
        "导入页面存在于 VersionControl",
    )
    if imported:
        check(
            "[[nginx-timeout]]" in imported["content"],
            f"导入后双链反向转换为 [[wikilink]]",
        )
        check(
            "[[nginx-timeout]]" in imported["content"]
            and "](/concepts/" not in imported["content"],
            f"导入后无 OKF MD 链接残留",
        )
        # permissive 导入：保留原 review_status（auto），不强制覆盖
        check(
            "review_status" in imported["content"],
            f"导入页面保留 review_status 字段（permissive）",
        )

    # ── 12. import 容错：未知 type / 缺失字段 ──
    print("\n[12] import permissive consumption")
    # 构造一个不规范的 bundle
    bad_bundle = TMP_DIR / "bad_bundle"
    bad_bundle.mkdir()
    (bad_bundle / "unknown.md").write_text(
        "---\n"
        "title: 未知类型页面\n"
        # 故意缺 type
        "---\n\n"
        "# 未知类型\n\n内容。\n",
        encoding="utf-8",
    )

    bad_result = import_bundle(bad_bundle)
    check(
        bad_result.pages_imported == 1,
        f"容错导入 1 页（缺 type 不拒绝）(got {bad_result.pages_imported})",
    )
    check(
        any("type 缺失" in w for w in bad_result.warnings),
        f"缺失 type 产生 warning 而非 error (got {bad_result.warnings})",
    )
    # 验证补了默认 type
    imported_bad = vc.get_latest("wiki:unknown")
    if imported_bad:
        check(
            "type: concept" in imported_bad["content"],
            f"缺失 type 补默认 concept",
        )

    # ── 13. import overwrite 行为 ──
    print("\n[13] import overwrite 控制")
    # 再次导入正规 bundle，overwrite=False 应跳过
    skip_result = import_bundle(bundle_dir, overwrite=False)
    check(
        skip_result.pages_skipped >= 2,
        f"overwrite=False 跳过已存在 (got skipped={skip_result.pages_skipped})",
    )

    # overwrite=True 应覆盖
    over_result = import_bundle(bundle_dir, overwrite=True)
    check(
        over_result.pages_imported >= 2,
        f"overwrite=True 覆盖导入 (got imported={over_result.pages_imported})",
    )

    # ── 14. export_bundle_tarball ──
    print("\n[14] export_bundle_tarball tarball 分发")
    # 清理测试12引入的 unknown 页面，确保 tarball 只含 2 个正规页面
    vc.delete_all("wiki:unknown")
    tarball = TMP_DIR / "bundle.tar.gz"
    saved, tar_result = export_bundle_tarball(tarball)
    check(
        saved.exists() and saved.stat().st_size > 0,
        f"tarball 文件已生成 (size={saved.stat().st_size if saved.exists() else 0})",
    )
    check(
        tar_result.pages_exported == 2,
        f"tarball 内含 2 页 (got {tar_result.pages_exported})",
    )

    # 校验 tarball 结构
    import tarfile

    with tarfile.open(saved, "r:gz") as tar:
        names = tar.getnames()
    check(
        any("okf-bundle/index.md" in n for n in names),
        f"tarball 含 okf-bundle/index.md",
    )
    check(
        any("incidents/nginx-502.md" in n for n in names),
        f"tarball 含 incidents/nginx-502.md",
    )

    # ── 15. 端到端往返：导出 -> 导入 -> 内容一致 ──
    print("\n[15] 端到端往返一致性")
    # 清空，从 tarball 导入
    vc.delete_all("wiki:nginx-502")
    vc.delete_all("wiki:nginx-timeout")

    from app.knowledge.okf_adapter import import_bundle_tarball

    roundtrip_result = import_bundle_tarball(tarball, overwrite=True)
    check(
        roundtrip_result.pages_imported == 2,
        f"从 tarball 导入 2 页 (got {roundtrip_result.pages_imported})",
    )

    # 验证导入后能再次导出，且导出物结构一致
    bundle2_dir = TMP_DIR / "roundtrip_bundle"
    result2 = export_bundle(bundle2_dir)
    check(
        result2.pages_exported == 2,
        f"二次导出 2 页 (got {result2.pages_exported})",
    )
    check(
        (bundle2_dir / "incidents" / "nginx-502.md").exists(),
        f"二次导出目录结构一致",
    )

    # ── 总结 ──
    print("\n" + "=" * 70)
    print(f"总计: {PASS} PASS / {FAIL} FAIL")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
