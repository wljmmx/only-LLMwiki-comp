"""认证 API（P3-1 SSO 基础）

端点：
- POST /auth/login           用户名密码登录，返回 session token
- POST /auth/logout          注销当前 session（P0-4: 服务端删除 session）
- POST /auth/change-password 修改自己的密码（P0-9: 强制改密）
- GET  /auth/me              获取当前用户信息
- GET  /auth/users           列出用户（admin）
- POST /auth/users           创建用户（admin）
- PUT  /auth/users/{user_id} 更新用户（admin）
- DELETE /auth/users/{user_id} 删除用户（admin）
- POST /auth/cleanup         清理过期 session（admin）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.models import ROLES, get_auth_store
from app.auth.token_auth import get_current_user, require_role
from app.middleware.rate_limit import apply_rate_limit

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


class ChangePasswordRequest(BaseModel):
    """P0-9: 修改自己的密码"""
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


# ────────── 端点 ──────────


@router.post("/auth/login", response_model=LoginResponse)
@apply_rate_limit("5/minute")
async def login(request: Request, req: LoginRequest) -> LoginResponse:
    """用户名密码登录，返回 session token

    P0-5: 登录限流 5次/分钟/IP（防暴力破解）
    P0-5: 账户锁定后返回 423 Locked
    P0-9: 返回 must_change_password 标志供前端引导改密
    """
    store = get_auth_store()
    user = store.verify_password(req.username, req.password)
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    token = store.create_session(user["id"])
    return LoginResponse(token=token, user=user)


@router.post("/auth/logout")
async def logout(
    request: Request,
    user: dict | None = Depends(get_current_user),
) -> dict:
    """注销当前 session（P0-4: 服务端删除 session 记录）

    从 Authorization header 提取 token，调用 revoke_session 注销。
    """
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]

    revoked = False
    if token:
        store = get_auth_store()
        revoked = store.revoke_session(token)

    return {
        "logged_out": True,
        "session_revoked": revoked,
        "user": user.get("username") if user else None,
    }


@router.post("/auth/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user: dict | None = Depends(get_current_user),
) -> dict:
    """P0-9: 修改自己的密码

    - 验证旧密码
    - 新密码最少 8 位
    - 清除 must_change_password 标志
    - 重置失败计数与锁定状态
    """
    if user is None:
        raise HTTPException(401, "需要登录后才能修改密码")

    store = get_auth_store()
    # 验证旧密码
    verified = store.verify_password(user["username"], req.old_password)
    if not verified:
        raise HTTPException(401, "旧密码错误")

    # 新密码不能与旧密码相同
    if req.old_password == req.new_password:
        raise HTTPException(400, "新密码不能与旧密码相同")

    # 更新密码（update_user 会自动清除 must_change_password + 重置锁定）
    store.update_user(user["id"], password=req.new_password)
    logger_msg = "auth.password_changed"
    import structlog
    structlog.get_logger().info(logger_msg, user_id=user["id"], username=user["username"])
    return {"changed": True, "must_change_password": False}


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
