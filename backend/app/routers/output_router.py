"""输出文档生成 API（P4-4）。

端点:
- GET  /output/templates          — 列出可用模板
- GET  /output/templates/{id}     — 获取模板详情
- POST /output/generate           — 生成标准化文档
- GET  /output/docs               — 列出已生成文档
- GET  /output/docs/{doc_id}      — 获取已生成文档
- GET  /output/docs/{doc_id}/download — 下载已生成文档
- POST /output/experience/distill — 经验蒸馏
- POST /output/index/rebuild      — 使用 IndexGenerator 重建目录树
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.auth import verify_token
from app.output import DocumentGenerator, list_templates, load_template

router = APIRouter()


class GenerateRequest(BaseModel):
    """POST /output/generate 请求体"""
    template_id: str = Field(..., description="模板 ID")
    system_name: str = Field("", description="目标系统名称")
    custom_title: str = Field("", description="自定义标题")
    use_llm: bool = Field(True, description="是否使用 LLM")


class DistillRequest(BaseModel):
    """POST /output/experience/distill 请求体"""
    wiki_slugs: list[str] = Field(default_factory=list, description="限定 Wiki 页面 slug 列表（空=全部）")
    use_llm: bool = Field(True, description="是否使用 LLM")


# ── 模板 API ──

@router.get("/output/templates")
async def output_templates() -> dict:
    """列出所有可用文档模板"""
    templates = list_templates()
    return {"count": len(templates), "templates": templates}


@router.get("/output/templates/{template_id}")
async def output_template_detail(template_id: str) -> dict:
    """获取模板详情"""
    template = load_template(template_id)
    if template is None:
        raise HTTPException(404, f"模板不存在: {template_id}")
    return {
        "template_id": template.template_id,
        "name": template.name,
        "description": template.description,
        "document_type": template.document_type,
        "target_audience": template.target_audience,
        "sections": [
            {
                "title": s.title,
                "section_type": s.section_type,
                "required": s.required,
                "description": s.description,
                "max_items": s.max_items,
            }
            for s in template.sections
        ],
        "style": template.style,
    }


# ── 文档生成 API ──

@router.post("/output/generate", dependencies=[Depends(verify_token)])
async def output_generate(body: GenerateRequest) -> dict:
    """生成标准化输出文档

    根据选定的模板，从 Wiki 知识库中提取相关内容，自动编排生成标准化文档。
    """
    from app.knowledge import list_wiki_pages

    # 加载模板
    template = load_template(body.template_id)
    if template is None:
        raise HTTPException(400, f"模板不存在: {body.template_id}")

    # 收集 Wiki 页面
    wiki_pages = list_wiki_pages(limit=200)
    wiki_summaries = [
        {
            "slug": p.get("slug", ""),
            "title": p.get("title", ""),
            "type": p.get("type", "concept"),
            "tags": p.get("tags", []),
            "body_md": p.get("content", ""),
        }
        for p in wiki_pages
    ]

    if not wiki_summaries:
        raise HTTPException(400, "Wiki 知识库为空，无法生成文档")

    # 生成
    from app.core.llm.base import get_llm
    llm = get_llm()
    llm_call = llm.complete if llm else None

    generator = DocumentGenerator(llm_call=llm_call)
    result = await generator.generate(
        template_id=body.template_id,
        wiki_pages=wiki_summaries,
        system_name=body.system_name,
        custom_title=body.custom_title,
        use_llm=body.use_llm and llm_call is not None,
    )

    if not result.success:
        raise HTTPException(500, result.error or "文档生成失败")

    return {
        "doc_id": result.document.doc_id,
        "title": result.document.title,
        "content": result.document.content,
        "generated_at": result.document.generated_at,
        "llm_used": result.llm_used,
        "stats": result.document.stats,
        "sources": [
            {"slug": s.slug, "title": s.title, "section": s.section}
            for s in result.document.sources[:20]
        ],
    }


# ── 已生成文档 API ──

@router.get("/output/docs")
async def output_docs() -> dict:
    """列出所有已生成文档"""
    generator = DocumentGenerator()
    docs = generator.list_generated()
    return {"count": len(docs), "docs": docs}


@router.get("/output/docs/{doc_id}")
async def output_doc_get(doc_id: str) -> dict:
    """获取已生成文档"""
    generator = DocumentGenerator()
    doc = generator.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    return {
        "doc_id": doc.doc_id,
        "content": doc.content,
        "generated_at": doc.generated_at,
        "file_path": doc.file_path,
    }


@router.get("/output/docs/{doc_id}/download")
async def output_doc_download(doc_id: str):
    """下载已生成文档"""
    generator = DocumentGenerator()
    doc = generator.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    return PlainTextResponse(
        content=doc.content,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={doc_id}.md"},
    )


# ── 经验蒸馏 API ──

@router.post("/output/experience/distill", dependencies=[Depends(verify_token)])
async def output_experience_distill(body: DistillRequest) -> dict:
    """从 Wiki 页面蒸馏运维经验模式

    批量分析 Wiki 页面，识别重复故障模式、最佳实践、反模式等。
    结果可作为经验页面保存到 Wiki。
    """
    from app.knowledge import (
        ExperienceDistiller,
        list_wiki_pages,
        rebuild_index,
    )
    from app.core.llm.base import get_llm
    from app.storage import get_version_control

    # 收集页面摘要
    all_pages = list_wiki_pages(limit=200)
    if body.wiki_slugs:
        all_pages = [p for p in all_pages if p.get("slug") in body.wiki_slugs]

    summaries = [
        {
            "slug": p.get("slug", ""),
            "title": p.get("title", ""),
            "type": p.get("type", "concept"),
            "tags": p.get("tags", []),
            "summary": (p.get("content", "") or "")[:500],
            "entities": [],
            "incident_count": 1 if p.get("type") == "incident" else 0,
        }
        for p in all_pages
    ]

    if len(summaries) < 3:
        raise HTTPException(400, "需要至少 3 个 Wiki 页面才能蒸馏经验")

    # 蒸馏
    llm = get_llm()
    llm_call = llm.complete if llm else None
    distiller = ExperienceDistiller(llm_call=llm_call)
    result = await distiller.distill(
        summaries,
        use_llm=body.use_llm and llm_call is not None,
    )

    # 保存经验页面到 Wiki
    vc = get_version_control()
    saved_slugs: list[str] = []
    for ep in result.experience_pages:
        slug = ep["slug"]
        content = f"---\nslug: {slug}\ntitle: {ep['title']}\ntype: concept\ntags: {ep['tags']}\nreview_status: auto\ncreated_at: {result.generated_at}\nupdated_at: {result.generated_at}\n---\n\n{ep['body_md']}"
        vc.save_version(
            doc_key=f"wiki:{slug}",
            title=ep["title"],
            content=content,
            author="experience_distiller",
            change_summary="经验蒸馏自动生成",
        )
        saved_slugs.append(slug)

    # 重建索引
    rebuild_index()

    return {
        "generated_at": result.generated_at,
        "source_pages": result.source_page_count,
        "insights_count": len(result.insights),
        "saved_slugs": saved_slugs,
        "stats": result.stats,
        "insights": [
            {
                "type": i.insight_type,
                "title": i.title,
                "slug": i.slug,
                "severity": i.severity,
                "source_slugs": i.source_slugs,
            }
            for i in result.insights
        ],
    }


# ── 目录树生成 API ──

@router.post("/output/index/rebuild", dependencies=[Depends(verify_token)])
async def output_index_rebuild() -> dict:
    """使用 IndexGenerator 重建 Wiki 目录树"""
    from app.knowledge import IndexGenerator, list_wiki_pages
    from app.core.llm.base import get_llm

    pages = list_wiki_pages(limit=200)
    summaries = [
        {
            "slug": p.get("slug", ""),
            "title": p.get("title", ""),
            "type": p.get("type", "concept"),
            "tags": p.get("tags", []),
            "summary": (p.get("content", "") or "")[:300],
        }
        for p in pages
    ]

    llm = get_llm()
    llm_call = llm.complete if llm else None
    generator = IndexGenerator(llm_call=llm_call)
    tree = await generator.generate(summaries)

    # 保存为 index 页面
    from app.storage import get_version_control
    index_md = tree.to_markdown()
    vc = get_version_control()
    vc.save_version(
        doc_key="wiki:index",
        title="Wiki 知识库目录",
        content=index_md,
        author="index_generator",
        change_summary="IndexGenerator 自动重建",
    )

    return {
        "total_pages": tree.total_pages,
        "root_count": len(tree.roots),
        "orphan_count": len(tree.orphan_pages),
        "stats": tree.stats,
        "index_md": index_md[:2000],
    }