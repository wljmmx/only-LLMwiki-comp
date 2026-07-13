"""回滚执行器单元测试（P2-3.7）

覆盖：
- RollbackExecutor.execute: dry_run / argocd / jenkins / 不支持 target
- ArgoCD 后端：配置缺失 / 成功 / 失败
- Jenkins 后端：配置缺失 / 成功 / 失败
- MCP 工具 _tool_execute_rollback 调用路径（dry_run）
- MCP 工具权限要求 admin

httpx 调用全程 mock，不真实联网。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.aiops.rollback_executor import RollbackExecutor, get_rollback_executor

# ═══════════════ 公共 fixture / 辅助 ═══════════════


def _mock_settings(
    *,
    argocd_url: str = "",
    argocd_token: str = "",
    argocd_app_name: str = "",
    jenkins_url: str = "",
    jenkins_user: str = "",
    jenkins_token: str = "",
    jenkins_rollback_job: str = "rollback",
    rollback_default_target: str = "dry_run",
):
    """构造 mock Settings 对象"""
    m = MagicMock()
    m.argocd_url = argocd_url
    m.argocd_token = argocd_token
    m.argocd_app_name = argocd_app_name
    m.jenkins_url = jenkins_url
    m.jenkins_user = jenkins_user
    m.jenkins_token = jenkins_token
    m.jenkins_rollback_job = jenkins_rollback_job
    m.rollback_default_target = rollback_default_target
    return m


def _mock_httpx_response(
    *,
    status_code: int = 200,
    json_body: dict | None = None,
    text: str = "",
    headers: dict | None = None,
):
    """构造 mock httpx.Response"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or (json.dumps(json_body) if json_body else "")
    resp.headers = headers or {}
    resp.json.return_value = json_body or {}
    if status_code >= 400:
        # raise_for_status 在 4xx/5xx 时抛 HTTPStatusError
        err = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
        resp.raise_for_status.side_effect = err
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _mock_async_client(post_return):
    """构造 mock httpx.AsyncClient（async context manager）

    post_return: _mock_httpx_response 返回的 mock response
    """
    client = AsyncMock()
    client.post = AsyncMock(return_value=post_return)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _run_async(coro):
    """同步运行 async 协程"""
    return asyncio.run(coro)


SAMPLE_PLAN = {
    "change_id": "ch-deploy-001",
    "rollback_to": "v1.2.3",
    "app_name": "my-app",
    "job_name": "rollback-job",
    "service": "api-server",
    "incident_id": "inc-42",
    "reasoning": "部署后 5xx 飙升",
}


# ═══════════════ 1. dry_run 模式 ═══════════════


class TestDryRun:
    def test_dry_run_returns_preview_without_api_call(self):
        """dry_run 模式返回预览，不调用任何外部 API"""
        with patch(
            "app.aiops.rollback_executor.get_settings",
            return_value=_mock_settings(),
        ):
            executor = RollbackExecutor()
            result = _run_async(executor.execute(SAMPLE_PLAN, "dry_run"))

        assert result["success"] is True
        assert result["provider"] == "dry_run"
        assert "action_id" in result
        assert result["details"]["mode"] == "dry_run"
        assert "未调用任何外部 API" in result["details"]["message"]
        assert result["details"]["change_id"] == "ch-deploy-001"
        assert result["details"]["rollback_to"] == "v1.2.3"
        # dry_run 不应有 argocd/jenkins 预览（配置为空）
        assert result["details"]["argocd_preview"] is None
        assert result["details"]["jenkins_preview"] is None

    def test_dry_run_shows_previews_when_configured(self):
        """dry_run 模式在配置存在时显示预览（但仍不调用 API）"""
        settings = _mock_settings(
            argocd_url="https://argocd.example.com",
            argocd_token="tok",
            argocd_app_name="default-app",
            jenkins_url="https://jenkins.example.com",
            jenkins_user="bot",
            jenkins_token="jt",
        )
        with patch(
            "app.aiops.rollback_executor.get_settings", return_value=settings
        ):
            executor = RollbackExecutor()
            result = _run_async(executor.execute(SAMPLE_PLAN, "dry_run"))

        assert result["success"] is True
        assert result["details"]["argocd_preview"] is not None
        assert "argocd.example.com" in result["details"]["argocd_preview"]["url"]
        assert result["details"]["jenkins_preview"] is not None
        assert "jenkins.example.com" in result["details"]["jenkins_preview"]["url"]


# ═══════════════ 2. ArgoCD 后端 ═══════════════


class TestArgocdBackend:
    def test_argocd_missing_config_returns_error(self):
        """argocd 模式配置缺失时返回明确 error"""
        with patch(
            "app.aiops.rollback_executor.get_settings",
            return_value=_mock_settings(),  # 全部为空
        ):
            executor = RollbackExecutor()
            result = _run_async(executor.execute(SAMPLE_PLAN, "argocd"))

        assert result["success"] is False
        assert result["provider"] == "argocd"
        assert "配置缺失" in result["error"]
        assert "argocd_url" in result["error"]
        assert "argocd_token" in result["error"]

    def test_argocd_success(self):
        """argocd 模式 httpx mock 成功响应 → success=True"""
        settings = _mock_settings(
            argocd_url="https://argocd.example.com",
            argocd_token="secret-token",
            argocd_app_name="default-app",
        )
        mock_resp = _mock_httpx_response(
            status_code=200,
            json_body={"metadata": {"name": "my-app-v123", "uid": "uid-abc"}},
        )
        mock_client = _mock_async_client(mock_resp)

        with patch(
            "app.aiops.rollback_executor.get_settings", return_value=settings
        ), patch(
            "app.aiops.rollback_executor.httpx.AsyncClient", return_value=mock_client
        ):
            executor = RollbackExecutor()
            result = _run_async(executor.execute(SAMPLE_PLAN, "argocd"))

        assert result["success"] is True
        assert result["provider"] == "argocd"
        assert result["action_id"] == "my-app-v123"
        assert result["details"]["app_name"] == "my-app"
        assert result["details"]["revision"] == "v1.2.3"
        # 验证 httpx.post 调用参数
        mock_client.post.assert_awaited_once()
        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert "argocd.example.com" in url
        assert "my-app" in url
        assert "rollback" in url

    def test_argocd_http_error(self):
        """argocd 模式 httpx mock 失败响应 → success=False + error"""
        settings = _mock_settings(
            argocd_url="https://argocd.example.com",
            argocd_token="secret-token",
            argocd_app_name="default-app",
        )
        mock_resp = _mock_httpx_response(
            status_code=404, json_body={"error": "not found"}, text="not found"
        )
        mock_client = _mock_async_client(mock_resp)

        with patch(
            "app.aiops.rollback_executor.get_settings", return_value=settings
        ), patch(
            "app.aiops.rollback_executor.httpx.AsyncClient", return_value=mock_client
        ):
            executor = RollbackExecutor()
            result = _run_async(executor.execute(SAMPLE_PLAN, "argocd"))

        assert result["success"] is False
        assert result["provider"] == "argocd"
        assert "404" in result["error"]

    def test_argocd_missing_app_name(self):
        """argocd 模式未指定 app_name → error"""
        settings = _mock_settings(
            argocd_url="https://argocd.example.com",
            argocd_token="secret-token",
            # argocd_app_name 为空
        )
        plan = {"change_id": "ch-1", "rollback_to": "v1"}
        with patch(
            "app.aiops.rollback_executor.get_settings", return_value=settings
        ):
            executor = RollbackExecutor()
            result = _run_async(executor.execute(plan, "argocd"))

        assert result["success"] is False
        assert "应用名" in result["error"]


# ═══════════════ 3. Jenkins 后端 ═══════════════


class TestJenkinsBackend:
    def test_jenkins_missing_config_returns_error(self):
        """jenkins 模式配置缺失时返回明确 error"""
        with patch(
            "app.aiops.rollback_executor.get_settings",
            return_value=_mock_settings(),
        ):
            executor = RollbackExecutor()
            result = _run_async(executor.execute(SAMPLE_PLAN, "jenkins"))

        assert result["success"] is False
        assert result["provider"] == "jenkins"
        assert "配置缺失" in result["error"]
        assert "jenkins_url" in result["error"]
        assert "jenkins_user" in result["error"]
        assert "jenkins_token" in result["error"]

    def test_jenkins_success(self):
        """jenkins 模式 httpx mock 成功响应 → success=True"""
        settings = _mock_settings(
            jenkins_url="https://jenkins.example.com",
            jenkins_user="bot",
            jenkins_token="jt-secret",
            jenkins_rollback_job="default-rollback",
        )
        mock_resp = _mock_httpx_response(
            status_code=201,
            text="",
            headers={"Location": "https://jenkins.example.com/queue/item/123/"},
        )
        mock_client = _mock_async_client(mock_resp)

        with patch(
            "app.aiops.rollback_executor.get_settings", return_value=settings
        ), patch(
            "app.aiops.rollback_executor.httpx.AsyncClient", return_value=mock_client
        ):
            executor = RollbackExecutor()
            result = _run_async(executor.execute(SAMPLE_PLAN, "jenkins"))

        assert result["success"] is True
        assert result["provider"] == "jenkins"
        assert "queue/item/123" in result["action_id"]
        assert result["details"]["job_name"] == "rollback-job"
        assert result["details"]["change_id"] == "ch-deploy-001"
        # 验证 httpx.post 调用参数
        mock_client.post.assert_awaited_once()
        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert "jenkins.example.com" in url
        assert "rollback-job" in url

    def test_jenkins_http_error(self):
        """jenkins 模式 httpx mock 失败响应 → success=False"""
        settings = _mock_settings(
            jenkins_url="https://jenkins.example.com",
            jenkins_user="bot",
            jenkins_token="jt-secret",
        )
        mock_resp = _mock_httpx_response(
            status_code=500, text="internal error"
        )
        mock_client = _mock_async_client(mock_resp)

        with patch(
            "app.aiops.rollback_executor.get_settings", return_value=settings
        ), patch(
            "app.aiops.rollback_executor.httpx.AsyncClient", return_value=mock_client
        ):
            executor = RollbackExecutor()
            result = _run_async(executor.execute(SAMPLE_PLAN, "jenkins"))

        assert result["success"] is False
        assert result["provider"] == "jenkins"
        assert "500" in result["error"]


# ═══════════════ 4. 不支持的 target ═══════════════


class TestUnsupportedTarget:
    def test_unsupported_target_returns_error(self):
        """不支持的 target → error"""
        executor = RollbackExecutor()
        result = _run_async(executor.execute(SAMPLE_PLAN, "helm"))

        assert result["success"] is False
        assert result["provider"] == "helm"
        assert "不支持" in result["error"]
        assert "helm" in result["error"]


# ═══════════════ 5. MCP 工具调用路径 ═══════════════


class TestMcpToolExecuteRollback:
    def test_mcp_tool_dry_run(self):
        """MCP 工具 _tool_execute_rollback dry_run 调用路径"""
        from app.mcp.protocol import _tool_execute_rollback

        args = {
            "rollback_plan": {
                "change_id": "ch-mcp-1",
                "service": "web",
                "incident_id": "inc-99",
            },
            "target": "dry_run",
        }
        result_str = _tool_execute_rollback(args)
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["provider"] == "dry_run"
        assert result["details"]["change_id"] == "ch-mcp-1"

    def test_mcp_tool_default_target_is_dry_run(self):
        """MCP 工具未指定 target 时默认 dry_run（安全默认）"""
        from app.mcp.protocol import _tool_execute_rollback

        # 不传 target，应使用 settings.rollback_default_target（默认 dry_run）
        args = {"rollback_plan": {"change_id": "ch-default"}}
        result_str = _tool_execute_rollback(args)
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["provider"] == "dry_run"

    def test_mcp_tool_empty_plan_returns_error(self):
        """MCP 工具 rollback_plan 为空时返回 error"""
        from app.mcp.protocol import _tool_execute_rollback

        result_str = _tool_execute_rollback({"rollback_plan": {}})
        result = json.loads(result_str)
        # 空 dict 会被 rollback_plan 校验拦截
        assert "error" in result or result.get("success") is False

    def test_mcp_tool_invalid_plan_type_returns_error(self):
        """MCP 工具 rollback_plan 非对象时返回 error"""
        from app.mcp.protocol import _tool_execute_rollback

        result_str = _tool_execute_rollback({"rollback_plan": "not-a-dict"})
        result = json.loads(result_str)
        assert "error" in result
        assert "rollback_plan" in result["error"]


# ═══════════════ 6. MCP 工具权限 ═══════════════


class TestExecuteRollbackPermission:
    def test_execute_rollback_requires_admin(self):
        """execute_rollback 工具权限要求 admin"""
        from app.mcp.protocol import TOOL_REQUIRED_ROLES, _check_tool_permission

        assert TOOL_REQUIRED_ROLES.get("execute_rollback") == "admin"

        # admin 用户可调用
        admin_user = {"role": "admin", "username": "admin1"}
        assert _check_tool_permission("execute_rollback", admin_user) is True

        # operator 用户不可调用
        operator_user = {"role": "operator", "username": "op1"}
        assert _check_tool_permission("execute_rollback", operator_user) is False

        # viewer 用户不可调用
        viewer_user = {"role": "viewer", "username": "viewer1"}
        assert _check_tool_permission("execute_rollback", viewer_user) is False

    def test_suggest_rollback_still_readonly(self):
        """suggest_rollback 行为未改变（仍为只读，viewer 可用）"""
        from app.mcp.protocol import _TOOL_ANNOTATIONS, TOOL_REQUIRED_ROLES

        assert TOOL_REQUIRED_ROLES.get("suggest_rollback") == "viewer"
        assert _TOOL_ANNOTATIONS.get("suggest_rollback", {}).get("readOnlyHint") is True


# ═══════════════ 7. 单例 ═══════════════


class TestSingleton:
    def test_get_rollback_executor_singleton(self):
        """get_rollback_executor 返回单例"""
        ex1 = get_rollback_executor()
        ex2 = get_rollback_executor()
        assert ex1 is ex2
        assert isinstance(ex1, RollbackExecutor)
