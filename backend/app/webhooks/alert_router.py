"""告警路由规则引擎（S15-2）

在 WebhookManager.dispatch_event 投递前进行两层过滤：

1. 静默窗口（silence_windows）：维护期间整体不投递
2. 路由规则（alert_rules）：
   - severity 过滤（critical / warning / info）
   - payload_matchers 字段匹配（eq / ne / contains / regex / gt / lt / gte / lte）
   - target_subscription_ids 收窄投递范围
   - priority 数字越小优先级越高

设计要点：
- 无规则时行为不变（向后兼容）：返回原订阅列表
- 静默窗口检查在路由规则之前
- payload 中的 severity 字段可选，缺失时不按 severity 过滤
- 多条规则按 priority 排序依次评估，匹配的 target_subscription_ids 取并集去重
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import structlog

from app.storage.webhook_store import WebhookStore

logger = structlog.get_logger()


# 支持的 matcher 操作符
_SUPPORTED_OPS = {"eq", "ne", "contains", "regex", "gt", "lt", "gte", "lte"}


def _event_type_matches(pattern: str, event_type: str) -> bool:
    """检查 event_type 是否匹配规则模式（与 manager._event_matches 同语义）

    支持三种形式：
    - `*`                匹配所有事件
    - `incident.*`       前缀通配
    - `incident.created` 精确匹配
    """
    if not pattern:
        return False
    if pattern == "*":
        return True
    if pattern == event_type:
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return event_type.startswith(prefix + ".")
    return False


def _match_payload(matchers: list[dict], payload: dict) -> bool:
    """检查 payload 是否匹配所有 matchers（AND 关系）

    每条 matcher: {"field": "host", "op": "eq", "value": "prod-01"}
    支持的 op：
    - eq: 等于
    - ne: 不等于
    - contains: 包含（字符串子串或列表成员）
    - regex: 正则匹配
    - gt/lt/gte/lte: 数值比较

    Args:
        matchers: 匹配条件列表
        payload: 事件 payload（已是 dict）

    Returns:
        全部匹配返回 True；任一不匹配或 matcher 非法返回 False
    """
    if not matchers:
        return True
    for m in matchers:
        if not isinstance(m, dict):
            return False
        field = m.get("field")
        op = m.get("op")
        expected = m.get("value")
        if not field or not op:
            return False
        if op not in _SUPPORTED_OPS:
            return False
        # payload 中可能直接含字段，也可能嵌在 data 下（dispatch 时会包成 envelope，
        # 但路由发生在 envelope 包装之前，此处直接读 payload）
        actual = payload.get(field)
        if not _apply_op(op, actual, expected):
            return False
    return True


def _apply_op(op: str, actual: Any, expected: Any) -> bool:
    """应用单个匹配操作

    - 字段缺失（actual is None）：eq/ne 按字面比较，其他 op 返回 False
    - 数值比较要求双方都能转 float，否则返回 False
    - regex 用 re.search（部分匹配即可）
    """
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "contains":
        if actual is None:
            return False
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        if isinstance(actual, str):
            return isinstance(expected, str) and expected in actual
        # dict：检查 value 中是否包含 expected
        if isinstance(actual, dict):
            return expected in actual.values()
        return False
    if op == "regex":
        if not isinstance(actual, str) or not isinstance(expected, str):
            return False
        try:
            return re.search(expected, actual) is not None
        except re.error:
            return False
    # 数值比较
    if op in {"gt", "lt", "gte", "lte"}:
        try:
            a = float(actual)
            b = float(expected)
        except (TypeError, ValueError):
            return False
        if op == "gt":
            return a > b
        if op == "lt":
            return a < b
        if op == "gte":
            return a >= b
        if op == "lte":
            return a <= b
    return False


def _parse_iso(s: str) -> datetime:
    """解析 ISO8601 时间字符串为带时区的 datetime（容错处理 Z 后缀）"""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class AlertRouter:
    """告警路由规则引擎（S15-2）

    用法：
        router = AlertRouter(store)
        if router.is_silenced(event_type, payload):
            return 0  # 静默，不投递
        routed = router.route(event_type, payload, matched_subs)
    """

    def __init__(self, store: WebhookStore) -> None:
        self.store = store

    # ────────── 静默窗口 ──────────

    def is_silenced(self, event_type: str, payload: dict) -> bool:
        """检查事件是否被静默窗口拦截

        判定逻辑（任一启用窗口满足以下全部条件即静默）：
        1. 当前 UTC 时间 ∈ [start_time, end_time]
        2. event_type_pattern 匹配 event_type
        3. payload_matchers 全部匹配（空 matchers 视为匹配）
        """
        try:
            windows = self.store.list_silence_windows(enabled_only=True)
        except Exception as e:  # noqa: BLE001
            logger.error("alert_router.silence_list_failed", err=str(e))
            return False
        if not windows:
            return False
        now = datetime.now(timezone.utc)
        for w in windows:
            try:
                start = _parse_iso(w["start_time"])
                end = _parse_iso(w["end_time"])
            except (ValueError, KeyError):
                # 时间格式非法，跳过该窗口
                continue
            if not (start <= now <= end):
                continue
            if not _event_type_matches(w.get("event_type_pattern", ""), event_type):
                continue
            if not _match_payload(w.get("payload_matchers", []), payload):
                continue
            logger.info(
                "alert_router.silenced",
                event_type=event_type,
                window_id=w["id"],
                window_name=w.get("name"),
            )
            return True
        return False

    # ────────── 路由规则 ──────────

    def route(
        self,
        event_type: str,
        payload: dict,
        all_subs: list[dict],
    ) -> list[dict]:
        """应用路由规则，返回应投递的订阅列表

        算法：
        1. 查询所有启用的路由规则，按 priority 升序
        2. 对每条规则：
           a. event_type_pattern 匹配？
           b. severity 匹配（payload.severity 字段；规则 severity 为空则不过滤）？
           c. payload_matchers 全部匹配？
           d. 匹配且 target_subscription_ids 非空 → 收窄到这些订阅
           e. 匹配且 target_subscription_ids 为空 → 保留所有订阅（不过滤）
        3. 多条规则匹配时，命中订阅取并集去重
        4. 无任何规则匹配 → 返回原 all_subs（向后兼容）

        Args:
            event_type: 事件类型
            payload: 事件 payload
            all_subs: 经事件类型订阅匹配后的候选订阅列表

        Returns:
            最终应投递的订阅列表（保持原 all_subs 顺序）
        """
        try:
            rules = self.store.list_alert_rules(enabled_only=True)
        except Exception as e:  # noqa: BLE001
            logger.error("alert_router.rules_list_failed", err=str(e))
            return list(all_subs)

        # 无规则 → 向后兼容，原样返回
        if not rules:
            return list(all_subs)

        # payload.severity 可选
        payload_severity = payload.get("severity") if isinstance(payload, dict) else None

        # 收集命中订阅的 id 集合；None 表示"匹配所有订阅"
        matched_ids: set[str] | None = None  # None = 全部
        any_rule_matched = False

        sub_id_set = {s.get("id") for s in all_subs if s.get("id")}

        for rule in rules:
            if not _event_type_matches(rule.get("event_type_pattern", ""), event_type):
                continue
            # severity 过滤：规则指定了 severity 时，payload.severity 必须等于
            rule_sev = rule.get("severity", "")
            if rule_sev:
                if not payload_severity or payload_severity != rule_sev:
                    continue
            if not _match_payload(rule.get("payload_matchers", []), payload):
                continue

            # 规则命中
            any_rule_matched = True
            targets = rule.get("target_subscription_ids", []) or []
            if not targets:
                # 空目标 = 不过滤，保留所有候选订阅
                matched_ids = None
                break  # 已决定保留全部，无需继续
            # 收窄到 targets，但只保留候选集合内的（订阅存在且匹配事件类型）
            target_set = {t for t in targets if t in sub_id_set}
            if matched_ids is None:
                matched_ids = set()
            matched_ids |= target_set

        # 无规则命中 → 向后兼容，返回原订阅列表
        if not any_rule_matched:
            return list(all_subs)

        # matched_ids is None 表示命中规则但目标是"所有订阅"
        if matched_ids is None:
            return list(all_subs)

        # 保持原顺序去重
        return [s for s in all_subs if s.get("id") in matched_ids]

    # ────────── 测试用：dry-run 评估 ──────────

    def evaluate(
        self,
        event_type: str,
        payload: dict,
        all_subs: list[dict],
    ) -> dict[str, Any]:
        """评估事件路由结果（dry-run，供 API 测试端点使用）

        Returns:
            {
                "silenced": bool,
                "silenced_by": dict | None,  # 命中的静默窗口
                "matched_subscription_count": int,  # 经事件订阅匹配后的候选数
                "routed_subscription_count": int,   # 经路由规则后的最终数
                "routed_subscription_ids": list[str],
                "matched_rules": list[dict],  # 命中的路由规则（id/name/priority）
            }
        """
        # 静默检查
        silenced_by = None
        try:
            windows = self.store.list_silence_windows(enabled_only=True)
        except Exception:  # noqa: BLE001
            windows = []
        now = datetime.now(timezone.utc)
        for w in windows:
            try:
                start = _parse_iso(w["start_time"])
                end = _parse_iso(w["end_time"])
            except (ValueError, KeyError):
                continue
            if not (start <= now <= end):
                continue
            if not _event_type_matches(w.get("event_type_pattern", ""), event_type):
                continue
            if not _match_payload(w.get("payload_matchers", []), payload):
                continue
            silenced_by = w
            break

        if silenced_by:
            return {
                "silenced": True,
                "silenced_by": {
                    "id": silenced_by["id"],
                    "name": silenced_by["name"],
                },
                "matched_subscription_count": len(all_subs),
                "routed_subscription_count": 0,
                "routed_subscription_ids": [],
                "matched_rules": [],
            }

        # 路由评估
        routed = self.route(event_type, payload, all_subs)

        # 收集命中规则（用于调试）
        matched_rules: list[dict] = []
        try:
            rules = self.store.list_alert_rules(enabled_only=True)
        except Exception:  # noqa: BLE001
            rules = []
        payload_severity = payload.get("severity") if isinstance(payload, dict) else None
        for rule in rules:
            if not _event_type_matches(rule.get("event_type_pattern", ""), event_type):
                continue
            rule_sev = rule.get("severity", "")
            if rule_sev and (not payload_severity or payload_severity != rule_sev):
                continue
            if not _match_payload(rule.get("payload_matchers", []), payload):
                continue
            matched_rules.append(
                {
                    "id": rule["id"],
                    "name": rule["name"],
                    "priority": rule.get("priority"),
                    "target_subscription_ids": rule.get("target_subscription_ids", []),
                }
            )

        return {
            "silenced": False,
            "silenced_by": None,
            "matched_subscription_count": len(all_subs),
            "routed_subscription_count": len(routed),
            "routed_subscription_ids": [s["id"] for s in routed if s.get("id")],
            "matched_rules": matched_rules,
        }
