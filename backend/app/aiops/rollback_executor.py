"""回滚执行器（P2-3.7）

将 suggest_rollback 的建议升级为可执行操作，支持两种执行后端：
- ArgoCD：调用 Application rollback API（POST /api/v1/applications/{name}/rollback）
- Jenkins：触发 rollback job（POST /job/{job_name}/buildWithParameters）
- dry_run：安全默认，不调用任何外部 API，仅返回"将执行什么"的预览

设计原则：
- 配置缺失返回结构化 error，不抛异常（调用方无需 try/except）
- 真实 API 调用用 httpx.AsyncClient 异步执行
- 全程 structlog 日志记录（rollback_executed / rollback_failed）
- dry_run 是安全默认，不产生任何副作用
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

# 支持的执行后端
_SUPPORTED_TARGETS = ("dry_run", "argocd", "jenkins")


class RollbackExecutor:
    """回滚执行器：将回滚计划下发到 ArgoCD / Jenkins 或仅做 dry_run 预览"""

    def __init__(self) -> None:
        self.logger = logger.bind(component="rollback_executor")

    async def execute(self, rollback_plan: dict, target: str = "argocd") -> dict:
        """执行回滚计划

        Args:
            rollback_plan: 回滚计划（来自 suggest_rollback 或自定义），关键字段：
                - change_id: 变更 ID（作为回滚目标 revision 的兜底）
                - rollback_to: 显式指定回滚到的 revision/版本（优先于 change_id）
                - app_name: ArgoCD 应用名（可选，兜底用 settings.argocd_app_name）
                - job_name: Jenkins job 名（可选，兜底用 settings.jenkins_rollback_job）
                - service: 服务名（Jenkins 参数）
                - incident_id: incident ID（追踪用）
                - reasoning: 回滚原因（日志记录）
            target: 执行后端，dry_run / argocd / jenkins

        Returns:
            成功：{"success": True, "provider": target, "action_id": ..., "details": ...}
            失败：{"success": False, "error": ..., "provider": target}
        """
        if target not in _SUPPORTED_TARGETS:
            self.logger.warning(
                "rollback_failed",
                provider=target,
                error=f"不支持的 target: {target}",
            )
            return {
                "success": False,
                "error": f"不支持的 target: {target}，支持: {list(_SUPPORTED_TARGETS)}",
                "provider": target,
            }

        if target == "dry_run":
            return self._dry_run(rollback_plan)
        if target == "argocd":
            return await self._execute_argocd(rollback_plan)
        return await self._execute_jenkins(rollback_plan)

    # ────────── dry_run 预览 ──────────

    def _dry_run(self, plan: dict) -> dict:
        """dry_run 模式：不调用任何外部 API，仅返回预览"""
        change_id = plan.get("change_id", "")
        rollback_to = plan.get("rollback_to") or change_id
        service = plan.get("service", "")
        incident_id = plan.get("incident_id", "")

        # 预览 ArgoCD 路径
        settings = get_settings()
        argocd_app = plan.get("app_name") or settings.argocd_app_name
        argocd_preview = None
        if settings.argocd_url and argocd_app:
            argocd_preview = {
                "url": f"{settings.argocd_url.rstrip('/')}/api/v1/applications/{argocd_app}/rollback",
                "method": "POST",
                "app_name": argocd_app,
                "revision": rollback_to,
            }

        # 预览 Jenkins 路径
        jenkins_job = plan.get("job_name") or settings.jenkins_rollback_job
        jenkins_preview = None
        if settings.jenkins_url and jenkins_job:
            jenkins_preview = {
                "url": f"{settings.jenkins_url.rstrip('/')}/job/{jenkins_job}/buildWithParameters",
                "method": "POST",
                "job_name": jenkins_job,
                "parameters": {
                    "CHANGE_ID": change_id,
                    "ROLLBACK_TO": rollback_to,
                    "SERVICE": service,
                },
            }

        self.logger.info(
            "rollback_executed",
            provider="dry_run",
            change_id=change_id,
            rollback_to=rollback_to,
            incident_id=incident_id,
        )
        return {
            "success": True,
            "provider": "dry_run",
            "action_id": f"dry_run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "details": {
                "mode": "dry_run",
                "message": "dry_run 模式：未调用任何外部 API",
                "change_id": change_id,
                "rollback_to": rollback_to,
                "service": service,
                "incident_id": incident_id,
                "argocd_preview": argocd_preview,
                "jenkins_preview": jenkins_preview,
            },
        }

    # ────────── ArgoCD 后端 ──────────

    async def _execute_argocd(self, plan: dict) -> dict:
        """调用 ArgoCD Application rollback API"""
        settings = get_settings()
        if not settings.argocd_url or not settings.argocd_token:
            missing = []
            if not settings.argocd_url:
                missing.append("argocd_url")
            if not settings.argocd_token:
                missing.append("argocd_token")
            self.logger.warning(
                "rollback_failed",
                provider="argocd",
                error="配置缺失",
                missing=missing,
            )
            return {
                "success": False,
                "error": f"ArgoCD 配置缺失: {missing}，请在 Settings 中配置 OPSKG_ARGOCD_URL / OPSKG_ARGOCD_TOKEN",
                "provider": "argocd",
            }

        app_name = plan.get("app_name") or settings.argocd_app_name
        if not app_name:
            self.logger.warning(
                "rollback_failed",
                provider="argocd",
                error="未指定 ArgoCD 应用名",
            )
            return {
                "success": False,
                "error": "未指定 ArgoCD 应用名（rollback_plan.app_name 或 settings.argocd_app_name 均为空）",
                "provider": "argocd",
            }

        change_id = plan.get("change_id", "")
        revision = plan.get("rollback_to") or change_id
        if not revision:
            self.logger.warning(
                "rollback_failed",
                provider="argocd",
                error="未指定回滚目标 revision",
            )
            return {
                "success": False,
                "error": "未指定回滚目标 revision（rollback_plan.rollback_to 或 change_id 均为空）",
                "provider": "argocd",
            }

        url = f"{settings.argocd_url.rstrip('/')}/api/v1/applications/{app_name}/rollback"
        headers = {"Authorization": f"Bearer {settings.argocd_token}"}
        payload = {"name": app_name, "revision": revision}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                resp_body = self._safe_json(resp)
        except httpx.HTTPStatusError as e:
            self.logger.warning(
                "rollback_failed",
                provider="argocd",
                app=app_name,
                revision=revision,
                status_code=e.response.status_code,
                error=str(e),
            )
            return {
                "success": False,
                "error": f"ArgoCD API 返回 {e.response.status_code}: {self._safe_text(e.response)}",
                "provider": "argocd",
            }
        except Exception as e:  # noqa: BLE001
            self.logger.warning(
                "rollback_failed",
                provider="argocd",
                app=app_name,
                revision=revision,
                error=str(e),
            )
            return {
                "success": False,
                "error": f"ArgoCD 调用异常: {e}",
                "provider": "argocd",
            }

        action_id = resp_body.get("metadata", {}).get("name") or resp_body.get(
            "metadata", {}
        ).get("uid", f"argocd-{app_name}-{revision}")
        self.logger.info(
            "rollback_executed",
            provider="argocd",
            app=app_name,
            revision=revision,
            action_id=action_id,
        )
        return {
            "success": True,
            "provider": "argocd",
            "action_id": action_id,
            "details": {
                "app_name": app_name,
                "revision": revision,
                "url": url,
                "response": resp_body,
            },
        }

    # ────────── Jenkins 后端 ──────────

    async def _execute_jenkins(self, plan: dict) -> dict:
        """触发 Jenkins rollback job"""
        settings = get_settings()
        if not settings.jenkins_url or not settings.jenkins_user or not settings.jenkins_token:
            missing = []
            if not settings.jenkins_url:
                missing.append("jenkins_url")
            if not settings.jenkins_user:
                missing.append("jenkins_user")
            if not settings.jenkins_token:
                missing.append("jenkins_token")
            self.logger.warning(
                "rollback_failed",
                provider="jenkins",
                error="配置缺失",
                missing=missing,
            )
            return {
                "success": False,
                "error": f"Jenkins 配置缺失: {missing}，请在 Settings 中配置 OPSKG_JENKINS_URL / OPSKG_JENKINS_USER / OPSKG_JENKINS_TOKEN",
                "provider": "jenkins",
            }

        job_name = plan.get("job_name") or settings.jenkins_rollback_job
        if not job_name:
            self.logger.warning(
                "rollback_failed",
                provider="jenkins",
                error="未指定 Jenkins job 名",
            )
            return {
                "success": False,
                "error": "未指定 Jenkins job 名（rollback_plan.job_name 或 settings.jenkins_rollback_job 均为空）",
                "provider": "jenkins",
            }

        change_id = plan.get("change_id", "")
        rollback_to = plan.get("rollback_to") or change_id
        service = plan.get("service", "")
        incident_id = plan.get("incident_id", "")

        url = f"{settings.jenkins_url.rstrip('/')}/job/{job_name}/buildWithParameters"
        auth = (settings.jenkins_user, settings.jenkins_token)
        # Jenkins buildWithParameters 通过 query string 传参
        params = {
            "CHANGE_ID": change_id,
            "ROLLBACK_TO": rollback_to,
            "SERVICE": service,
            "INCIDENT_ID": incident_id,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, params=params, auth=auth)
                resp.raise_for_status()
                # Jenkins 触发成功通常返回 201，Location header 含 queue item URL
                queue_url = resp.headers.get("Location", "")
        except httpx.HTTPStatusError as e:
            self.logger.warning(
                "rollback_failed",
                provider="jenkins",
                job=job_name,
                status_code=e.response.status_code,
                error=str(e),
            )
            return {
                "success": False,
                "error": f"Jenkins API 返回 {e.response.status_code}: {self._safe_text(e.response)}",
                "provider": "jenkins",
            }
        except Exception as e:  # noqa: BLE001
            self.logger.warning(
                "rollback_failed",
                provider="jenkins",
                job=job_name,
                error=str(e),
            )
            return {
                "success": False,
                "error": f"Jenkins 调用异常: {e}",
                "provider": "jenkins",
            }

        action_id = queue_url or f"jenkins-{job_name}-{change_id}"
        self.logger.info(
            "rollback_executed",
            provider="jenkins",
            job=job_name,
            change_id=change_id,
            rollback_to=rollback_to,
            action_id=action_id,
        )
        return {
            "success": True,
            "provider": "jenkins",
            "action_id": action_id,
            "details": {
                "job_name": job_name,
                "change_id": change_id,
                "rollback_to": rollback_to,
                "service": service,
                "queue_url": queue_url,
                "url": url,
            },
        }

    # ────────── 工具方法 ──────────

    @staticmethod
    def _safe_json(resp: httpx.Response) -> dict:
        """安全解析 JSON 响应，失败时返回空 dict"""
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _safe_text(resp: httpx.Response, max_len: int = 500) -> str:
        """安全提取响应文本，截断过长内容"""
        try:
            text = resp.text
        except Exception:  # noqa: BLE001
            return ""
        return text[:max_len] if len(text) > max_len else text


# 全局单例
_executor: RollbackExecutor | None = None


def get_rollback_executor() -> RollbackExecutor:
    """获取 RollbackExecutor 全局单例"""
    global _executor
    if _executor is None:
        _executor = RollbackExecutor()
    return _executor
