"""时序异常检测（S15-3）

支持两种算法：
- Z-score: 基于均值和标准差，适合正态分布数据
- EWMA: 指数加权移动平均，适合非平稳数据

异常 → event 桥接：检测到异常时自动通过 webhook 系统 dispatch_event。
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

import structlog

from app.aiops.timeseries_store import TimeseriesStore

logger = structlog.get_logger()

# 支持的算法目录
SUPPORTED_ALGORITHMS: dict[str, str] = {
    "zscore": "Z-score 基于均值与标准差，适合正态分布数据",
    "ewma": "EWMA 指数加权移动平均，适合非平稳数据",
}

# std==0 且 value != mean 时使用的代表值（避免 inf 破坏 JSON 序列化）
_ZERO_STD_SENTINEL = 9999.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnomalyDetector:
    """时序异常检测（S15-3）

    支持两种算法：
    - Z-score: 基于均值和标准差，适合正态分布数据
    - EWMA: 指数加权移动平均，适合非平稳数据
    """

    def __init__(self, store: TimeseriesStore) -> None:
        self.store = store

    # ────────── Z-score ──────────

    def detect_zscore(
        self,
        metric_name: str,
        value: float,
        window: int = 100,
        threshold: float = 3.0,
        labels: dict | None = None,
    ) -> dict[str, Any]:
        """Z-score 异常检测

        算法：
        1. 获取最近 window 个历史值
        2. 计算均值 mean 和标准差 std
        3. 计算 z = (value - mean) / std
        4. 如果 |z| > threshold，判定为异常
        """
        history = self.store.get_latest_values(
            metric_name, count=window, labels=labels
        )
        return self._zscore_core(value, history, threshold, metric_name)

    @staticmethod
    def _zscore_core(
        value: float,
        history: list[float],
        threshold: float,
        metric_name: str = "",
    ) -> dict[str, Any]:
        """Z-score 核心计算（基于给定历史值）"""
        n = len(history)
        # 空历史或单点：无法计算标准差，判为非异常
        if n == 0:
            return {
                "is_anomaly": False,
                "score": 0.0,
                "threshold": threshold,
                "mean": 0.0,
                "std": 0.0,
                "value": float(value),
                "metric_name": metric_name,
                "window_size": 0,
            }
        mean = statistics.fmean(history)
        if n < 2:
            # 单点历史：无法计算 std
            return {
                "is_anomaly": False,
                "score": 0.0,
                "threshold": threshold,
                "mean": mean,
                "std": 0.0,
                "value": float(value),
                "metric_name": metric_name,
                "window_size": n,
            }
        std = statistics.pstdev(history)
        if std == 0:
            # 所有历史值相同：value 偏离即为异常（z = inf）
            z = 0.0 if value == mean else _ZERO_STD_SENTINEL
        else:
            z = (value - mean) / std
        is_anomaly = abs(z) > threshold
        return {
            "is_anomaly": is_anomaly,
            "score": z,
            "threshold": threshold,
            "mean": mean,
            "std": std,
            "value": float(value),
            "metric_name": metric_name,
            "window_size": n,
        }

    # ────────── EWMA ──────────

    def detect_ewma(
        self,
        metric_name: str,
        value: float,
        alpha: float = 0.3,
        window: int = 100,
        threshold: float = 3.0,
        labels: dict | None = None,
    ) -> dict[str, Any]:
        """EWMA 异常检测

        算法：
        1. 获取最近 window 个历史值
        2. 计算 EWMA：ewma[i] = alpha * value[i] + (1-alpha) * ewma[i-1]
        3. 计算 EWMA 的标准差
        4. 计算 z = (value - ewma_latest) / ewma_std
        5. 如果 |z| > threshold，判定为异常
        """
        history = self.store.get_latest_values(
            metric_name, count=window, labels=labels
        )
        return self._ewma_core(value, history, alpha, threshold, metric_name)

    @staticmethod
    def _ewma_core(
        value: float,
        history: list[float],
        alpha: float,
        threshold: float,
        metric_name: str = "",
    ) -> dict[str, Any]:
        """EWMA 核心计算（基于给定历史值）"""
        n = len(history)
        if n == 0:
            return {
                "is_anomaly": False,
                "score": 0.0,
                "threshold": threshold,
                "mean": 0.0,
                "std": 0.0,
                "ewma": 0.0,
                "value": float(value),
                "metric_name": metric_name,
                "window_size": 0,
            }
        # 计算 EWMA 序列
        ewma_series: list[float] = []
        prev = history[0]
        ewma_series.append(prev)
        for v in history[1:]:
            prev = alpha * v + (1 - alpha) * prev
            ewma_series.append(prev)
        ewma_latest = ewma_series[-1]
        if n < 2:
            return {
                "is_anomaly": False,
                "score": 0.0,
                "threshold": threshold,
                "mean": statistics.fmean(history),
                "std": 0.0,
                "ewma": ewma_latest,
                "value": float(value),
                "metric_name": metric_name,
                "window_size": n,
            }
        ewma_std = statistics.pstdev(ewma_series)
        if ewma_std == 0:
            z = 0.0 if value == ewma_latest else _ZERO_STD_SENTINEL
        else:
            z = (value - ewma_latest) / ewma_std
        is_anomaly = abs(z) > threshold
        return {
            "is_anomaly": is_anomaly,
            "score": z,
            "threshold": threshold,
            "mean": statistics.fmean(history),
            "std": ewma_std,
            "ewma": ewma_latest,
            "value": float(value),
            "metric_name": metric_name,
            "window_size": n,
        }

    # ────────── 批量检测 ──────────

    def detect_batch(
        self,
        metric_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
        algorithm: str = "zscore",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """批量检测一段时间内的异常

        对 [start_time, end_time] 范围内的每个数据点，使用其之前的窗口历史值做检测。
        """
        window = kwargs.get("window", 100)
        threshold = kwargs.get("threshold", 3.0)
        alpha = kwargs.get("alpha", 0.3)
        labels = kwargs.get("labels")

        # 取批次内的数据点
        batch_points = self.store.query(
            metric_name,
            start_time=start_time,
            end_time=end_time,
            labels=labels,
            limit=100000,
        )
        if not batch_points:
            return []

        # 取 end_time 之前所有数据点（含批次），按时间升序，构建滑动窗口
        # 注意：批次内点也作为后续点的历史
        all_points = self.store.query(
            metric_name,
            end_time=end_time,
            labels=labels,
            limit=1000000,
        )
        # query 已按 timestamp ASC 返回
        results: list[dict[str, Any]] = []
        for pt in batch_points:
            # 取 pt 之前的历史值（严格小于 pt.timestamp）
            history = [
                p["value"] for p in all_points if p["timestamp"] < pt["timestamp"]
            ][-window:]
            if algorithm == "ewma":
                res = self._ewma_core(
                    pt["value"], history, alpha, threshold, metric_name
                )
            else:
                res = self._zscore_core(
                    pt["value"], history, threshold, metric_name
                )
            res["timestamp"] = pt["timestamp"]
            res["labels"] = pt.get("labels", {})
            results.append(res)
        return results

    # ────────── 统一入口 ──────────

    def evaluate(
        self,
        metric_name: str,
        value: float,
        algorithm: str = "zscore",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """统一入口：根据 algorithm 选择检测方法"""
        algo = algorithm.lower()
        if algo == "zscore":
            return self.detect_zscore(metric_name, value, **kwargs)
        if algo == "ewma":
            return self.detect_ewma(metric_name, value, **kwargs)
        raise ValueError(
            f"不支持的算法: {algorithm}。支持: {list(SUPPORTED_ALGORITHMS.keys())}"
        )

    # ────────── anomaly → event 桥接 ──────────

    def detect_and_dispatch(
        self,
        metric_name: str,
        value: float,
        algorithm: str = "zscore",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """检测异常并自动分发 anomaly 事件到 webhook 系统

        如果检测到异常：
        1. 调用 webhook dispatch_event("event.ingested", payload)
        2. payload 包含：metric_name, value, score, algorithm, severity, timestamp, labels
        3. severity 根据异常程度自动分级：
           - |z| > 5 → critical
           - |z| > 3 → warning
           - |z| > 2 → info
        """
        result = self.evaluate(metric_name, value, algorithm=algorithm, **kwargs)
        result["dispatched"] = 0
        result["severity"] = ""
        if not result.get("is_anomaly"):
            return result

        score = abs(result.get("score", 0.0))
        if score > 5:
            severity = "critical"
        elif score > 3:
            severity = "warning"
        else:
            severity = "info"
        result["severity"] = severity

        labels = kwargs.get("labels") or {}
        payload = {
            "metric_name": metric_name,
            "value": float(value),
            "score": result.get("score", 0.0),
            "algorithm": algorithm,
            "severity": severity,
            "timestamp": _now_iso(),
            "labels": labels,
            "source": "anomaly_detector",
        }
        try:
            from app.webhooks import dispatch_event

            dispatched = dispatch_event("event.ingested", payload)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "anomaly.dispatch_failed",
                metric_name=metric_name,
                err=str(e),
            )
            dispatched = 0
        result["dispatched"] = dispatched
        logger.info(
            "anomaly.detected_and_dispatched",
            metric_name=metric_name,
            value=value,
            score=result.get("score"),
            severity=severity,
            dispatched=dispatched,
        )
        return result


# ────────── 单例 ──────────

_detector: AnomalyDetector | None = None


def get_anomaly_detector() -> AnomalyDetector:
    global _detector
    if _detector is None:
        from app.aiops.timeseries_store import get_timeseries_store

        _detector = AnomalyDetector(get_timeseries_store())
    return _detector
