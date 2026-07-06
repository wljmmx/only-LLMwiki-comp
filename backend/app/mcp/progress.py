"""P2-5.5 MCP 工具进度通知机制（SSE 传输配套）。

提供基于 contextvars 的进度回调上下文：
- 工具内部调用 `emit_progress(message, current, total)` 推送进度
- 无回调时为 no-op，不影响普通 JSON-RPC 调用路径
- SSE 端点设置回调后，工具执行中的进度会被实时推送到客户端

MCP 协议规范：进度通知通过 `notifications/progress` 方法推送，
params 含 `progressToken` / `progress` / `total` / `message`。
"""
from __future__ import annotations

import contextvars
from typing import Callable, Optional

# 进度回调：签名 (message: str, current: int, total: int) -> None
# 不同请求/线程互相隔离（contextvars 自动隔离 async + thread）
_progress_callback: contextvars.ContextVar[Optional[Callable[[str, int, int], None]]] = (
    contextvars.ContextVar("_mcp_progress_callback", default=None)
)

# 当前请求的 progressToken（来自 JSON-RPC 请求 _meta.progressToken）
_progress_token: contextvars.ContextVar[Optional[object]] = contextvars.ContextVar(
    "_mcp_progress_token", default=None
)


def set_progress_callback(
    cb: Optional[Callable[[str, int, int], None]],
    token: Optional[object] = None,
) -> None:
    """设置当前上下文的进度回调与 progressToken

    Args:
        cb: 回调函数 (message, current, total) -> None
        token: MCP 请求的 progressToken（_meta.progressToken），透传给客户端
    """
    _progress_callback.set(cb)
    _progress_token.set(token)


def get_progress_token() -> Optional[object]:
    """获取当前上下文的 progressToken（供 SSE 端点构造通知时使用）"""
    return _progress_token.get()


def emit_progress(message: str, current: int = 0, total: int = 0) -> None:
    """工具内部调用以推送进度通知

    无回调时为 no-op，不影响普通 JSON-RPC 调用路径。

    Args:
        message: 进度描述（如 "检索文档中..."）
        current: 当前进度（如 3）
        total: 总量（如 10），0 表示未知
    """
    cb = _progress_callback.get()
    if cb is not None:
        try:
            cb(message, current, total)
        except Exception:
            # 进度回调失败不应影响工具主流程
            pass


def reset_progress_context() -> None:
    """重置当前上下文的进度回调与 token（请求结束时调用）"""
    _progress_callback.set(None)
    _progress_token.set(None)
