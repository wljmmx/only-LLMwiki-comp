"""Alertmanager webhook payload 适配器（P2-2.5）

将 Prometheus Alertmanager v4 标准 webhook payload 转换为 OpsKG Event dict 列表，
复用 EventCorrelator.ingest() 接入关联引擎。

Alertmanager v4 标准 payload 结构：
    {
      "version": "4",
      "groupKey": "...",
      "status": "firing",
      "receiver": "...",
      "groupLabels": {...},
      "commonLabels": {...},
      "commonAnnotations": {...},
      "externalURL": "...",
      "alerts": [
        {
          "status": "firing",
          "labels": {"alertname": "...", "instance": "...", "job": "...", "severity": "..."},
          "annotations": {"summary": "...", "description": "..."},
          "startsAt": "2026-07-13T08:23:45.123Z",
          "endsAt": "2026-07-13T08:28:45.123Z",
          "generatorURL": "...",
          "fingerprint": "a1b2c3d4e5f6a7b8"
        }
      ]
    }

字段映射约定：
    labels.instance    → host     (剥离 :port 后缀)
    labels.job         → service
    labels.alertname   → component + attributes.alertname
    labels.severity    → severity (词表已兼容 info/warning/critical；none→warning)
    labels.*           → tags     (其余 label 原样保留)
    annotations        → attributes.annotations
    annotations.summary + description → message (拼接)
    startsAt           → timestamp
    endsAt             → attributes.endsAt
    generatorURL       → attributes.generatorURL
    fingerprint        → attributes.fingerprint (Alertmanager 稳定哈希，用于跨实例去重)
    status             → attributes.status (firing/resolved)
    source             = "alertmanager"
"""
from __future__ import annotations

from typing import Any

# Alertmanager severity → OpsKG severity 词表
# OpsKG 词表：info|low|warning|high|critical|fatal
_SEVERITY_MAP: dict[str, str] = {
    "info": "info",
    "debug": "info",
    "low": "low",
    "minor": "low",
    "warning": "warning",
    "warn": "warning",
    "moderate": "warning",
    "high": "high",
    "major": "high",
    "error": "high",
    "critical": "critical",
    "severe": "critical",
    "fatal": "fatal",
    "emergency": "fatal",
    # 兜底
    "none": "warning",
    "ok": "info",
    "resolved": "info",
}


def _map_severity(am_sev: str | None) -> str:
    """Alertmanager severity → OpsKG severity

    Alertmanager 默认无 severity 标签，Prometheus best practice 用 info/warning/critical。
    缺省或未知值降级为 warning（OpsKG 默认级别）。
    """
    if not am_sev:
        return "warning"
    return _SEVERITY_MAP.get(am_sev.lower(), "warning")


def _strip_port(instance: str | None) -> str:
    """labels.instance 形如 'host:9100' → 'host'

    Alertmanager 通常带 :port，OpsKG host 字段不含端口。
    """
    if not instance:
        return ""
    # IPv6 形如 [::1]:9100 — 保留 [:host]
    if instance.startswith("["):
        # [::1]:9100 → 取 ] 之前部分 + 去括号
        end = instance.find("]")
        if end > 0:
            return instance[1:end]
        return instance
    # host:9100 → 取 : 之前部分（仅在含 : 且非纯 IPv4 时剥离）
    if ":" in instance:
        # 简单判断：含多个 : 视为 IPv6 不剥离；单个 : 视为 host:port
        if instance.count(":") == 1:
            return instance.split(":", 1)[0]
    return instance


def _build_message(annotations: dict[str, str], labels: dict[str, str]) -> str:
    """拼接 message：summary + description，缺省时回退到 alertname"""
    summary = annotations.get("summary", "").strip()
    description = annotations.get("description", "").strip()
    parts = [p for p in (summary, description) if p]
    if parts:
        return " — ".join(parts)
    # 回退：alertname + runbook_url
    alertname = labels.get("alertname", "").strip()
    return alertname or "(no message)"


def _convert_alert(alert: dict[str, Any]) -> dict[str, Any]:
    """单个 alert → OpsKG Event dict

    Returns:
        {
            "host": str,
            "service": str,
            "component": str,
            "severity": str,
            "message": str,
            "tags": {...},          # 非保留 label 原样保留
            "source": "alertmanager",
            "timestamp": str,        # ISO8601 from startsAt
            "attributes": {
                "alertname": str,
                "status": str,       # firing/resolved
                "fingerprint": str,
                "generatorURL": str,
                "endsAt": str,
                "annotations": {...},
            },
        }
    """
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}

    # 从 labels 抽取保留字段，其余原样进 tags
    alertname = labels.get("alertname", "")
    instance = labels.get("instance", "")
    job = labels.get("job", "")
    severity = _map_severity(labels.get("severity"))

    # 其余 label 进 tags（排除已抽取的 4 个保留字段）
    reserved = {"alertname", "instance", "job", "severity"}
    extra_tags = {k: v for k, v in labels.items() if k not in reserved}

    attributes: dict[str, Any] = {
        "alertname": alertname,
        "status": alert.get("status", "firing"),
        "fingerprint": alert.get("fingerprint", ""),
        "generatorURL": alert.get("generatorURL", ""),
        "annotations": annotations,
    }
    # endsAt 缺省时 Alertmanager 自动填 +4m（firing 状态），有值则保留
    ends_at = alert.get("endsAt")
    if ends_at:
        attributes["endsAt"] = ends_at

    return {
        "host": _strip_port(instance),
        "service": job,
        "component": alertname,
        "severity": severity,
        "message": _build_message(annotations, labels),
        "tags": extra_tags,
        "source": "alertmanager",
        "timestamp": alert.get("startsAt") or "",
        "attributes": attributes,
    }


def alertmanager_to_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Alertmanager v4 webhook payload → OpsKG Event dict 列表

    Args:
        payload: Alertmanager 标准 webhook payload（含 alerts 数组）

    Returns:
        list[Event dict]，可直接传给 EventCorrelator.ingest()

    Raises:
        ValueError: payload 结构非法（缺 alerts 或非 list）

    Note:
        - status="resolved" 的 alert 也转换为事件（attributes.status=resolved），
          关联引擎可据此触发 incident 自动迁移（由端点层处理）
        - 空 alerts 数组返回空列表（端点层判断）
    """
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 dict")

    alerts = payload.get("alerts")
    if alerts is None:
        # 容错：有些 receiver 直接发单 alert 不包 alerts
        if "labels" in payload:
            alerts = [payload]
        else:
            raise ValueError("payload 缺少 alerts 字段")
    if not isinstance(alerts, list):
        raise ValueError("alerts 必须是 list")

    return [_convert_alert(a) for a in alerts if isinstance(a, dict)]


def should_resolve_incident(event: dict[str, Any]) -> bool:
    """判断事件是否表示告警已恢复（用于触发 incident 自动迁移）

    Alertmanager firing → resolved 时会重发同 fingerprint 的 alert，
    status=resolved 且 endsAt 已填充。
    """
    attrs = event.get("attributes") or {}
    return attrs.get("status") == "resolved"
