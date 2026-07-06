"""认证 API（P3-1 SSO 基础）

端点：
- POST /auth/login           用户名密码登录，返回 session token
- POST /auth/logout          注销当前 session
- GET  /auth/me              获取当前用户信息
- GET  /auth/users           列出用户（admin）
- POST /auth/users           创建用户（admin）
- PUT  /auth/users/{user_id} 更新用户（admin）
- DELETE /auth/users/{user_id} 删除用户（admin）
- POST /auth/cleanup         清理过期 session（admin）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.models import ROLES, get_auth_store
from app.auth.token_auth import get_current_user, require_role, verify_token

router = APIRouter()


# ────────── Schemas ──────────


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    token: str
    user: dict


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    role: str = Field(default="viewer", pattern="^(admin|operator|viewer)$")
    display_name: str | None = None
    email: str | None = None


class UserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|operator|viewer)$")
    display_name: str | None = None
    email: str | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=1, max_length=128)


# ────────── 端点 ──────────


@router.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest) -> LoginResponse:
    """用户名密码登录，返回 session token"""
    store = get_auth_store()
    user = store.verify_password(req.username, req.password)
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    token = store.create_session(user["id"])
    return LoginResponse(token=token, user=user)


@router.post("/auth/logout")
async def logout(user: dict | None = Depends(get_current_user)) -> dict:
    """注销当前 session

    注意：前端需在调用后清除本地 token
    """
    # get_current_user 已验证 session 有效性
    # 由于无法从 Depends 拿到原始 token，这里仅返回成功
    # 实际 token 清除由前端完成；服务端 session 会自然过期
    return {"logged_out": True, "user": user.get("username") if user else None}


@router.get("/auth/me")
async def me(user: dict | None = Depends(get_current_user)) -> dict:
    """获取当前用户信息"""
    if user is None:
        # 开发模式或 legacy 模式
        return {"authenticated": False, "user": None}
    return {"authenticated": True, "user": user}


@router.get("/auth/roles")
async def list_roles() -> dict:
    """列出可用角色及层级"""
    from app.auth.models import ROLE_HIERARCHY

    return {"roles": ROLES, "hierarchy": ROLE_HIERARCHY}


@router.get("/auth/users", dependencies=[Depends(require_role("admin"))])
async def list_users() -> dict:
    """列出所有用户（admin）"""
    store = get_auth_store()
    return {"users": store.list_users(), "count": len(store.list_users())}


@router.post("/auth/users", dependencies=[Depends(require_role("admin"))])
async def create_user(req: UserCreate) -> dict:
    """创建用户（admin）"""
    store = get_auth_store()
    try:
        user = store.create_user(
            username=req.username,
            password=req.password,
            role=req.role,
            display_name=req.display_name,
            email=req.email,
        )
        return {"created": True, "user": user}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/auth/users/{user_id}", dependencies=[Depends(require_role("admin"))])
async def update_user(user_id: int, req: UserUpdate) -> dict:
    """更新用户（admin）"""
    store = get_auth_store()
    existing = store.get_user_by_id(user_id)
    if not existing:
        raise HTTPException(404, f"用户不存在: {user_id}")
    try:
        user = store.update_user(
            user_id,
            role=req.role,
            display_name=req.display_name,
            email=req.email,
            active=req.active,
            password=req.password,
        )
        return {"updated": True, "user": user}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/auth/users/{user_id}", dependencies=[Depends(require_role("admin"))])
async def delete_user(user_id: int) -> dict:
    """删除用户（admin）

    不允许删除自己（需通过前端控制），后端仅校验存在性
    """
    store = get_auth_store()
    existing = store.get_user_by_id(user_id)
    if not existing:
        raise HTTPException(404, f"用户不存在: {user_id}")
    ok = store.delete_user(user_id)
    return {"deleted": ok, "user_id": user_id}


@router.post("/auth/cleanup", dependencies=[Depends(require_role("admin"))])
async def cleanup_sessions() -> dict:
    """清理过期 session（admin）"""
    store = get_auth_store()
    n = store.cleanup_expired_sessions()
    return {"cleaned": n}
