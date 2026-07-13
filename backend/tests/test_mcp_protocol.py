"""MCP 协议回归测试（P2-3.7 回归保护）

验证 MCP 协议在 P2-3.7 回滚执行链路改造后行为正确：
- TOOLS 列表包含 execute_rollback 工具
- suggest_rollback 仍为 readOnlyHint（行为未改变）
- execute_rollback 标注为 destructiveHint
- TOOL_REQUIRED_ROLES 中 execute_rollback 要求 admin
- handle_request tools/call 能正确路由 execute_rollback
- 权限校验：非 admin 用户调用 execute_rollback 被拒绝

这是 P2-3.7 的回归保护测试，确保新增 execute_rollback 不破坏现有 MCP 协议。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.protocol import (
    _TOOL_ANNOTATIONS,
    TOOL_HANDLERS,
    TOOL_REQUIRED_ROLES,
    TOOLS,
    _check_tool_permission,
    _tool_execute_rollback,
    _tool_suggest_rollback,
    handle_request,
)

# ═══════════════ 工具注册回归测试 ═══════════════


class TestToolRegistration:
    """验证 execute_rollback 正确注册，suggest_rollback 未被修改"""

    def test_execute_rollback_in_tools_list(self):
        """TOOLS 列表包含 execute_rollback"""
        names = [t["name"] for t in TOOLS]
        assert "execute_rollback" in names

    def test_execute_rollback_in_tool_handlers(self):
        """TOOL_HANDLERS 包含 execute_rollback 处理器"""
        assert "execute_rollback" in TOOL_HANDLERS
        assert TOOL_HANDLERS["execute_rollback"] is _tool_execute_rollback

    def test_execute_rollback_requires_admin(self):
        """execute_rollback 权限要求 admin"""
        assert TOOL_REQUIRED_ROLES["execute_rollback"] == "admin"

    def test_execute_rollback_is_destructive(self):
        """execute_rollback 标注为 destructiveHint"""
        ann = _TOOL_ANNOTATIONS.get("execute_rollback", {})
        assert ann.get("destructiveHint") is True

    def test_execute_rollback_schema_has_target_enum(self):
        """execute_rollback inputSchema 含 target enum"""
        tool = next(t for t in TOOLS if t["name"] == "execute_rollback")
        target_prop = tool["inputSchema"]["properties"]["target"]
        assert target_prop["type"] == "string"
        assert set(target_prop["enum"]) == {"dry_run", "argocd", "jenkins"}
        assert target_prop["default"] == "dry_run"

    def test_execute_rollback_schema_requires_rollback_plan(self):
        """execute_rollback inputSchema required 含 rollback_plan"""
        tool = next(t for t in TOOLS if t["name"] == "execute_rollback")
        assert "rollback_plan" in tool["inputSchema"]["required"]

    def test_suggest_rollback_still_readonly(self):
        """suggest_rollback 仍为 readOnlyHint（未被改为破坏性）"""
        assert _TOOL_ANNOTATIONS["suggest_rollback"]["readOnlyHint"] is True
        assert "destructiveHint" not in _TOOL_ANNOTATIONS.get("suggest_rollback", {})

    def test_suggest_rollback_still_viewer(self):
        """suggest_rollback 权限仍为 viewer（未升级为 admin）"""
        assert TOOL_REQUIRED_ROLES["suggest_rollback"] == "viewer"

    def test_suggest_rollback_handler_unchanged(self):
        """suggest_rollback 处理器未改变"""
        assert TOOL_HANDLERS["suggest_rollback"] is _tool_suggest_rollback


# ═══════════════ 权限校验回归测试 ═══════════════


class TestPermissionCheck:
    """验证 execute_rollback 权限校验"""

    def test_admin_can_call_execute_rollback(self):
        admin = {"role": "admin", "username": "admin1"}
        assert _check_tool_permission("execute_rollback", admin) is True

    def test_operator_cannot_call_execute_rollback(self):
        operator = {"role": "operator", "username": "op1"}
        assert _check_tool_permission("execute_rollback", operator) is False

    def test_viewer_cannot_call_execute_rollback(self):
        viewer = {"role": "viewer", "username": "viewer1"}
        assert _check_tool_permission("execute_rollback", viewer) is False

    def test_suggest_rollback_viewer_allowed(self):
        """suggest_rollback 仍允许 viewer 调用"""
        viewer = {"role": "viewer", "username": "viewer1"}
        assert _check_tool_permission("suggest_rollback", viewer) is True


# ═══════════════ handle_request 路由测试 ═══════════════


class TestHandleRequestRouting:
    """验证 handle_request 能正确路由 execute_rollback"""

    def test_handle_request_execute_rollback_dry_run(self):
        """handle_request 路由 execute_rollback（dry_run，未登录 dev 模式放行）"""
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "execute_rollback",
                "arguments": {
                    "rollback_plan": {"change_id": "ch-test"},
                    "target": "dry_run",
                },
            },
        }
        resp = handle_request(req, user=None)
        assert resp["id"] == 1
        assert "result" in resp
        content = resp["result"]["content"][0]["text"]
        result = json.loads(content)
        assert result["success"] is True
        assert result["provider"] == "dry_run"

    def test_handle_request_execute_rollback_permission_denied_for_viewer(self):
        """非 admin 用户调用 execute_rollback 被拒绝（严格模式）"""
        from app.config import get_settings

        # 临时开启严格模式
        settings = get_settings()
        original_strict = settings.mcp_permission_strict
        settings.mcp_permission_strict = True
        try:
            viewer = {"role": "viewer", "username": "v1"}
            req = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "execute_rollback",
                    "arguments": {
                        "rollback_plan": {"change_id": "ch-test"},
                        "target": "dry_run",
                    },
                },
            }
            resp = handle_request(req, user=viewer)
            # 应返回错误（权限不足）
            assert "error" in resp
            assert resp["error"]["code"] == -32603
        finally:
            settings.mcp_permission_strict = original_strict

    def test_handle_request_suggest_rollback_unchanged(self):
        """suggest_rollback 通过 handle_request 仍正常工作"""
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "suggest_rollback",
                "arguments": {"incident_id": "inc-nonexistent"},
            },
        }
        resp = handle_request(req, user=None)
        assert resp["id"] == 3
        assert "result" in resp
        content = resp["result"]["content"][0]["text"]
        result = json.loads(content)
        # suggest_rollback 对不存在的 incident 返回 suggested=False
        assert result["suggested"] is False
