"""模板管理 API（P1-3）。

端点：
- GET    /templates
- GET    /templates/{slug}
- POST   /templates
- PUT    /templates/{slug}
- DELETE /templates/{slug}
- POST   /templates/{slug}/render
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_token
from app.templates import get_template_manager

router = APIRouter()


@router.get("/templates")
async def list_templates(category: str | None = None) -> dict:
    """列出模板"""
    mgr = get_template_manager()
    templates = mgr.list(category)
    return {"templates": templates, "count": len(templates)}


@router.get("/templates/{slug}")
async def get_template(slug: str) -> dict:
    """获取模板"""
    mgr = get_template_manager()
    tpl = mgr.get(slug)
    if not tpl:
        raise HTTPException(404, f"模板不存在: {slug}")
    return tpl


@router.post("/templates", dependencies=[Depends(verify_token)])
async def create_template(
    slug: str,
    name: str,
    content: str,
    category: str = "custom",
    description: str = "",
) -> dict:
    """创建自定义模板"""
    mgr = get_template_manager()
    try:
        return mgr.create(slug, name, content, category, description)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.put("/templates/{slug}", dependencies=[Depends(verify_token)])
async def update_template(
    slug: str,
    name: str | None = None,
    content: str | None = None,
    category: str | None = None,
    description: str | None = None,
) -> dict:
    """更新模板"""
    mgr = get_template_manager()
    try:
        result = mgr.update(slug, name, content, category, description)
        if not result:
            raise HTTPException(404, f"模板不存在: {slug}")
        return result
    except ValueError as e:
        raise HTTPException(403, str(e))


@router.delete("/templates/{slug}", dependencies=[Depends(verify_token)])
async def delete_template(slug: str) -> dict:
    """删除模板（仅自定义）"""
    mgr = get_template_manager()
    try:
        ok = mgr.delete(slug)
        if not ok:
            raise HTTPException(404, f"模板不存在: {slug}")
        return {"deleted": True, "slug": slug}
    except ValueError as e:
        raise HTTPException(403, str(e))


@router.post("/templates/{slug}/render")
async def render_template(slug: str, variables: dict) -> dict:
    """渲染模板"""
    mgr = get_template_manager()
    try:
        rendered = mgr.render(slug, variables)
        return {"slug": slug, "rendered": rendered, "length": len(rendered)}
    except ValueError as e:
        raise HTTPException(404, str(e))
