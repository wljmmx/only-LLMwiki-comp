"""OKF (Open Knowledge Format) v0.1 API（P0-2）。

把 OpsKG wiki 适配为标准 OKF bundle，支持导出/导入/校验/摘要。

端点：
- GET  /okf/export                导出整个 wiki 为 OKF bundle tarball
- GET  /okf/preview               预览 bundle 摘要统计（不下载）
- POST /okf/import                上传 tarball 导入为 wiki
- POST /okf/import/dir            从本地目录导入（开发/运维用）
- POST /okf/validate              校验上传的 bundle 是否符合 OKF v0.1 三硬性约束
- GET  /okf/version               返回 OKF 版本信息
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.auth import require_role, verify_token
from app.knowledge import (
    OKF_VERSION,
    bundle_summary,
    export_bundle,
    export_bundle_tarball,
    import_bundle,
    import_bundle_tarball,
    list_bundle_concepts,
)

router = APIRouter()


class OKFImportOptions(BaseModel):
    """导入选项"""

    overwrite: bool = Field(
        False, description="是否覆盖已存在的 wiki 页面"
    )
    author: str = Field("okf-import", description="导入作者标识")


class OKFVersionInfo(BaseModel):
    """OKF 版本信息"""

    okf_version: str
    adapter: str = "opskg-okf-adapter"
    supported_constraints: list[str]


@router.get("/okf/version")
async def get_okf_version() -> OKFVersionInfo:
    """返回 OKF 规范版本与适配器信息"""
    return OKFVersionInfo(
        okf_version=OKF_VERSION,
        adapter="opskg-okf-adapter",
        supported_constraints=[
            "concept_file_has_yaml_frontmatter",
            "concept_file_has_nonempty_type",
            "reserved_files_follow_roles",
        ],
    )


@router.get("/okf/export", dependencies=[Depends(verify_token)])
async def export_okf_bundle() -> Response:
    """导出整个 wiki 为 OKF bundle tarball (.tar.gz)

    生产环境用 tarball 作为 OKF bundle 的分发单位。
    bundle 内含：
    - index.md（根导航，渐进披露）
    - log.md（变更审计日志）
    - {incidents,runbooks,services,hosts,concepts,entities}/{slug}.md
    """
    with tempfile.TemporaryDirectory() as tmp:
        tarball_path = Path(tmp) / "opskg-okf-bundle.tar.gz"
        try:
            saved_path, result = export_bundle_tarball(tarball_path)
        except Exception as e:
            raise HTTPException(500, f"OKF 导出失败: {e}")

        if result.errors:
            # 错误不阻断导出，但在响应头中提示
            pass

        content = saved_path.read_bytes()
        return Response(
            content=content,
            media_type="application/gzip",
            headers={
                "Content-Disposition": (
                    'attachment; filename="opskg-okf-bundle.tar.gz"'
                ),
                "X-OKF-Pages-Exported": str(result.pages_exported),
                "X-OKF-Index-Written": str(int(result.index_written)),
                "X-OKF-Log-Written": str(int(result.log_written)),
                "X-OKF-Errors": str(len(result.errors)),
            },
        )


@router.get("/okf/preview", dependencies=[Depends(verify_token)])
async def preview_okf_bundle() -> dict:
    """预览导出 bundle 的摘要统计（不实际下载）

    返回概念数、类型分布、index/log 是否存在、字段完整度。
    """
    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = Path(tmp) / "preview-bundle"
        result = export_bundle(bundle_dir)
        summary = bundle_summary(bundle_dir)
        summary["export_errors"] = result.errors
        summary["pages_exported"] = result.pages_exported
        return summary


@router.post("/okf/import", dependencies=[Depends(verify_token)])
async def import_okf_bundle(
    file: UploadFile = File(...),
    overwrite: bool = False,
    author: str = "okf-import",
) -> dict:
    """上传 tarball 导入为 OpsKG wiki

    permissive consumption：容忍未知 type / 缺失字段 / 断链。

    Args:
        file: tarball 文件（.tar.gz）
        overwrite: 是否覆盖已存在页面
        author: 导入作者标识
    """
    if not file.filename or not (
        file.filename.endswith(".tar.gz") or file.filename.endswith(".tgz")
    ):
        raise HTTPException(400, "仅支持 .tar.gz / .tgz 格式的 OKF bundle")

    with tempfile.TemporaryDirectory() as tmp:
        tarball_path = Path(tmp) / "upload.tar.gz"
        content = await file.read()
        tarball_path.write_bytes(content)
        try:
            result = import_bundle_tarball(
                tarball_path, overwrite=overwrite, author=author
            )
        except Exception as e:
            raise HTTPException(500, f"OKF 导入失败: {e}")

        return {
            "pages_imported": result.pages_imported,
            "pages_skipped": result.pages_skipped,
            "slugs": result.slugs,
            "errors": result.errors,
            "warnings": result.warnings,
        }


@router.post(
    "/okf/import/dir",
    dependencies=[Depends(require_role("admin"))],
)
async def import_okf_bundle_from_dir(payload: dict) -> dict:
    """从服务器本地目录导入 OKF bundle（仅 admin，运维场景用）

    Body:
        {"path": "/path/to/bundle", "overwrite": false, "author": "okf-import"}
    """
    bundle_path = payload.get("path")
    if not bundle_path:
        raise HTTPException(400, "缺少 path 参数")
    bundle_dir = Path(bundle_path)
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise HTTPException(404, f"目录不存在: {bundle_path}")

    overwrite = bool(payload.get("overwrite", False))
    author = payload.get("author", "okf-import")

    result = import_bundle(bundle_dir, overwrite=overwrite, author=author)
    return {
        "pages_imported": result.pages_imported,
        "pages_skipped": result.pages_skipped,
        "slugs": result.slugs,
        "errors": result.errors,
        "warnings": result.warnings,
    }


@router.post("/okf/validate", dependencies=[Depends(verify_token)])
async def validate_okf_bundle(file: UploadFile = File(...)) -> dict:
    """校验上传的 tarball 是否符合 OKF v0.1 三硬性约束

    三约束：
    1. 每个非保留概念文件含可解析 YAML frontmatter
    2. frontmatter 含非空 `type` 字段
    3. 保留文件 index.md / log.md 守职责（存在时）

    输出与 `okf validate` CLI 兼容的 JSON 结构。
    """
    if not file.filename or not (
        file.filename.endswith(".tar.gz") or file.filename.endswith(".tgz")
    ):
        raise HTTPException(400, "仅支持 .tar.gz / .tgz 格式")

    import tarfile

    with tempfile.TemporaryDirectory() as tmp:
        tarball_path = Path(tmp) / "validate.tar.gz"
        content = await file.read()
        tarball_path.write_bytes(content)

        bundle_dir = Path(tmp) / "extracted"
        try:
            with tarfile.open(tarball_path, "r:gz") as tar:
                tar.extractall(bundle_dir)
        except Exception as e:
            raise HTTPException(400, f"tarball 解压失败: {e}")

        # 找到 bundle 根（含 .md 的顶层目录）
        bundle_root = bundle_dir
        subdirs = [d for d in bundle_dir.iterdir() if d.is_dir()]
        if subdirs:
            bundle_root = subdirs[0]

        # 校验
        validation = _validate_bundle(bundle_root)
        return validation


def _validate_bundle(bundle_dir: Path) -> dict:
    """执行 OKF v0.1 三硬性约束校验

    Returns:
        与 `okf validate` CLI 兼容的 JSON：
        {valid, errors, warnings, findings}
    """
    findings: list[dict] = []
    errors = 0
    warnings = 0

    concepts = list_bundle_concepts(bundle_dir)
    if not concepts:
        findings.append(
            {
                "level": "warn",
                "code": "empty_bundle",
                "message": "bundle 中无概念文件",
            }
        )
        warnings += 1

    import yaml as _yaml

    for c in concepts:
        rel = c.rel_path
        # 约束 1: 可解析 YAML frontmatter
        if not c.frontmatter:
            findings.append(
                {
                    "level": "error",
                    "code": "missing_frontmatter",
                    "file": rel,
                    "message": "概念文件缺少可解析的 YAML frontmatter",
                }
            )
            errors += 1
            continue

        # 约束 2: type 非空
        type_val = c.frontmatter.get("type")
        if not type_val or not str(type_val).strip():
            findings.append(
                {
                    "level": "error",
                    "code": "missing_type",
                    "file": rel,
                    "message": "frontmatter 缺少非空 type 字段",
                }
            )
            errors += 1

        # 推荐字段缺失（warn，不阻断）
        for field in ("title", "description", "resource", "tags", "timestamp"):
            if not c.frontmatter.get(field):
                findings.append(
                    {
                        "level": "warn",
                        "code": f"missing_recommended_{field}",
                        "file": rel,
                        "message": f"缺少推荐字段: {field}",
                    }
                )
                warnings += 1

    # 约束 3: 保留文件守职责（存在时检查）
    index_path = bundle_dir / "index.md"
    if index_path.exists():
        try:
            content = index_path.read_text(encoding="utf-8")
            meta, _ = _split_frontmatter(content)
            if meta.get("type") != "index":
                findings.append(
                    {
                        "level": "warn",
                        "code": "index_wrong_type",
                        "file": "index.md",
                        "message": f"index.md 的 type 应为 'index'，实际为 '{meta.get('type')}'",
                    }
                )
                warnings += 1
        except Exception as e:
            findings.append(
                {
                    "level": "error",
                    "code": "index_unparseable",
                    "file": "index.md",
                    "message": f"index.md 解析失败: {e}",
                }
            )
            errors += 1

    log_path = bundle_dir / "log.md"
    if log_path.exists():
        try:
            content = log_path.read_text(encoding="utf-8")
            meta, _ = _split_frontmatter(content)
            if meta.get("type") != "log":
                findings.append(
                    {
                        "level": "warn",
                        "code": "log_wrong_type",
                        "file": "log.md",
                        "message": f"log.md 的 type 应为 'log'，实际为 '{meta.get('type')}'",
                    }
                )
                warnings += 1
        except Exception as e:
            findings.append(
                {
                    "level": "error",
                    "code": "log_unparseable",
                    "file": "log.md",
                    "message": f"log.md 解析失败: {e}",
                }
            )
            errors += 1

    return {
        "bundle": str(bundle_dir.name),
        "okf_version": OKF_VERSION,
        "valid": errors == 0,
        "errors": errors,
        "warnings": warnings,
        "findings": findings,
        "concept_count": len(concepts),
    }


def _split_frontmatter(md: str) -> tuple[dict, str]:
    """拆分 frontmatter 与正文"""
    if not md.startswith("---"):
        return {}, md
    parts = md.split("---", 2)
    if len(parts) < 3:
        return {}, md
    try:
        import yaml as _yaml

        meta = _yaml.safe_load(parts[1]) or {}
    except Exception:
        meta = {}
    return meta, parts[2].lstrip("\n")
