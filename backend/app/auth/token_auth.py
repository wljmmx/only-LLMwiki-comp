"""简单 Token 认证（P0-2）

单用户 Token 认证，适合个人使用场景。
- 通过环境变量 OPSKG_API_TOKEN 配置
- 未配置 Token 时认证关闭（开发模式）
- Header: Authorization: Bearer <token>
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> str:
    """验证 Bearer Token

    - 未配置 OPSKG_API_TOKEN 时放行（开发模式）
    - 配置后必须匹配
    """
    settings = get_settings()
    expected_token = settings.api_token

    # 未配置 Token → 开发模式，放行
    if not expected_token:
        return "anonymous"

    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            401, "未提供认证凭证", headers={"WWW-Authenticate": "Bearer"}
        )

    if not secrets.compare_digest(credentials.credentials, expected_token):
        raise HTTPException(401, "认证失败：Token 无效")

    return "user"


def generate_token() -> str:
    """生成随机 Token（用于初始化）"""
    return secrets.token_urlsafe(32)
