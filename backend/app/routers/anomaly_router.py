"""时序异常检测 API（S15-3）。

端点：
- POST   /anomaly/metrics/record           记录数据点
- GET    /anomaly/metrics/{metric_name}     查询时序数据
- DELETE /anomaly/metrics/cleanup           清理旧数据
- POST   /anomaly/detect                    检测单个值（dry-run，不分发事件）
- POST   /anomaly/detect/batch              批量检测
- POST   /anomaly/detect-and-dispatch       检测并分发异常事件
- GET    /anomaly/algorithms                列出支持的算法

认证策略：GET 类端点免认证，POST/DELETE 需要 verify_token。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.aiops.anomaly_detector import SUPPORTED_ALGORITHMS, get_anomaly_detector
from app.aiops.timeseries_store import get_timeseries_store
from app.auth import verify_token

router = APIRouter()


# ────────── 算法配置 ──────────


@router.get("/anomaly/algorithms")
async def list_algorithms() -> dict:
    """列出支持的异常检测算法"""
    return {
        "algorithms": [
            {"name": k, "description": v}
            for k, v in sorted(SUPPORTED_ALGORITHMS.items())
        ],
        "count": len(SUPPORTED_ALGORITHMS),
    }


# ────────── 时序数据 ──────────


@router.post(
    "/anomaly/metrics/record", dependencies=[Depends(verify_token)]
)
async def record_metric(payload: dict) -> dict:
    """记录一个数据点

    Body:
        metric_name: 必填，指标名
        value: 必填，数值
        labels: 可选，标签字典
        timestamp: 可选，ISO8601 UTC（不传则用当前时间）
    """
    metric_name = str(payload.get("metric_name", "")).strip()
    if not metric_name:
        raise HTTPException(400, "metric_name 必填")
    if "value" not in payload:
        raise HTTPException(400, "value 必填")
    try:
        value = float(payload["value"])
    except (TypeError, ValueError):
        raise HTTPException(400, "value 必须为数值")
    labels = payload.get("labels")
    if labels is not None and not isinstance(labels, dict):
        raise HTTPException(400, "labels 必须为对象")
    timestamp = payload.get("timestamp")
    store = get_timeseries_store()
    try:
        return store.record(
            metric_name=metric_name,
            value=value,
            labels=labels,
            timestamp=timestamp,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/anomaly/metrics/{metric_name}")
async def query_metrics(
    metric_name: str,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = Query(1000, le=100000),
) -> dict:
    """查询时序数据（按 timestamp 升序）

    Query 参数：
        start_time: 起始时间（ISO8601，含）
        end_time: 结束时间（ISO8601，含）
        limit: 返回数量上限（默认 1000）
        labels 过滤通过 query body 中的 labels 字段，此处仅支持时间范围
    """
    store = get_timeseries_store()
    points = store.query(
        metric_name=metric_name,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )
    return {"metric_name": metric_name, "points": points, "count": len(points)}


@router.delete(
    "/anomaly/metrics/cleanup", dependencies=[Depends(verify_token)]
)
async def cleanup_metrics(days: int = 30) -> dict:
    """清理超过 N 天的旧数据"""
    if days < 0:
        raise HTTPException(400, "days 不能为负数")
    store = get_timeseries_store()
    deleted = store.cleanup_old(days=days)
    return {"deleted": deleted, "days": days}


# ────────── 异常检测 ──────────


@router.post(
    "/anomaly/detect", dependencies=[Depends(verify_token)]
)
async def detect_single(payload: dict) -> dict:
    """检测单个值（dry-run，不分发事件）

    Body:
        metric_name: 必填
        value: 必填
        algorithm: 可选，zscore / ewma（默认 zscore）
        window: 可选，默认 100
        threshold: 可选，默认 3.0
        alpha: 可选（ewma），默认 0.3
        labels: 可选，标签字典
    """
    metric_name = str(payload.get("metric_name", "")).strip()
    if not metric_name:
        raise HTTPException(400, "metric_name 必填")
    if "value" not in payload:
        raise HTTPException(400, "value 必填")
    try:
        value = float(payload["value"])
    except (TypeError, ValueError):
        raise HTTPException(400, "value 必须为数值")
    algorithm = str(payload.get("algorithm", "zscore")).lower()
    kwargs: dict = {}
    for k in ("window", "threshold", "alpha"):
        if k in payload:
            try:
                kwargs[k] = float(payload[k]) if k != "window" else int(payload[k])
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} 必须为数值")
    labels = payload.get("labels")
    if labels is not None and not isinstance(labels, dict):
        raise HTTPException(400, "labels 必须为对象")
    if labels:
        kwargs["labels"] = labels
    detector = get_anomaly_detector()
    try:
        return detector.evaluate(metric_name, value, algorithm=algorithm, **kwargs)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post(
    "/anomaly/detect/batch", dependencies=[Depends(verify_token)]
)
async def detect_batch(payload: dict) -> dict:
    """批量检测一段时间内的异常

    Body:
        metric_name: 必填
        start_time: 可选
        end_time: 可选
        algorithm: 可选，默认 zscore
        window: 可选，默认 100
        threshold: 可选，默认 3.0
        alpha: 可选（ewma），默认 0.3
        labels: 可选，标签字典
    """
    metric_name = str(payload.get("metric_name", "")).strip()
    if not metric_name:
        raise HTTPException(400, "metric_name 必填")
    algorithm = str(payload.get("algorithm", "zscore")).lower()
    kwargs: dict = {}
    for k in ("window", "threshold", "alpha"):
        if k in payload:
            try:
                kwargs[k] = float(payload[k]) if k != "window" else int(payload[k])
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} 必须为数值")
    labels = payload.get("labels")
    if labels is not None and not isinstance(labels, dict):
        raise HTTPException(400, "labels 必须为对象")
    if labels:
        kwargs["labels"] = labels
    if payload.get("start_time"):
        kwargs["start_time"] = str(payload["start_time"])
    if payload.get("end_time"):
        kwargs["end_time"] = str(payload["end_time"])
    detector = get_anomaly_detector()
    try:
        results = detector.detect_batch(
            metric_name, algorithm=algorithm, **kwargs
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    anomalies = [r for r in results if r.get("is_anomaly")]
    return {
        "metric_name": metric_name,
        "algorithm": algorithm,
        "total": len(results),
        "anomalies": len(anomalies),
        "results": results,
    }


@router.post(
    "/anomaly/detect-and-dispatch", dependencies=[Depends(verify_token)]
)
async def detect_and_dispatch(payload: dict) -> dict:
    """检测并分发异常事件

    检测到异常时自动 dispatch event.ingested 到 webhook 系统。
    severity 自动分级：|z|>5 critical / |z|>3 warning / |z|>2 info。

    Body 同 /anomaly/detect。
    """
    metric_name = str(payload.get("metric_name", "")).strip()
    if not metric_name:
        raise HTTPException(400, "metric_name 必填")
    if "value" not in payload:
        raise HTTPException(400, "value 必填")
    try:
        value = float(payload["value"])
    except (TypeError, ValueError):
        raise HTTPException(400, "value 必须为数值")
    algorithm = str(payload.get("algorithm", "zscore")).lower()
    kwargs: dict = {}
    for k in ("window", "threshold", "alpha"):
        if k in payload:
            try:
                kwargs[k] = float(payload[k]) if k != "window" else int(payload[k])
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} 必须为数值")
    labels = payload.get("labels")
    if labels is not None and not isinstance(labels, dict):
        raise HTTPException(400, "labels 必须为对象")
    if labels:
        kwargs["labels"] = labels
    detector = get_anomaly_detector()
    try:
        return detector.detect_and_dispatch(
            metric_name, value, algorithm=algorithm, **kwargs
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
