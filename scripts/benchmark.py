#!/usr/bin/env python3
"""OpsKG 性能基准测试

P2: 运行前需确保本地 Ollama 实例已启动（默认 http://localhost:11434），
否则 /api/llm-wiki/index 等 LLM 相关端点会超时。

运行：
    python scripts/benchmark.py --concurrency 5 --requests 20 --json | tee benchmark-results.json

输出 JSON 格式的基准测试结果，包含 P50/P95/P99 延迟。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


def single_request(url: str, endpoint: str) -> dict:
    """单次请求，返回状态码和延迟（ms）"""
    start = time.perf_counter()
    try:
        resp = requests.get(f"{url}{endpoint}", timeout=10)
        latency = (time.perf_counter() - start) * 1000
        return {"status": resp.status_code, "latency_ms": round(latency, 2)}
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        return {"status": 0, "latency_ms": round(latency, 2), "error": str(e)}


def benchmark_endpoint(url: str, endpoint: str, concurrency: int, total_requests: int) -> dict:
    """对单个端点进行基准测试"""
    latencies = []
    errors = 0

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(single_request, url, endpoint) for _ in range(total_requests)]
        for future in as_completed(futures):
            result = future.result()
            if result["status"] == 0:
                errors += 1
            latencies.append(result["latency_ms"])

    sorted_lat = sorted(latencies)
    return {
        "endpoint": endpoint,
        "total": total_requests,
        "errors": errors,
        "p50": round(statistics.median(sorted_lat), 2),
        "p95": round(sorted_lat[int(len(sorted_lat) * 0.95)], 2),
        "p99": round(sorted_lat[int(len(sorted_lat) * 0.99)], 2),
        "min": round(sorted_lat[0], 2),
        "max": round(sorted_lat[-1], 2),
        "mean": round(statistics.mean(sorted_lat), 2),
    }


def main():
    parser = argparse.ArgumentParser(description="OpsKG benchmark")
    parser.add_argument("--url", default="http://localhost:8080", help="Base URL")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent workers")
    parser.add_argument("--requests", type=int, default=20, help="Total requests per endpoint")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    endpoints = [
        "/health",
        "/api/llm-wiki/index",
        "/api/documents",
    ]

    results = {}
    for ep in endpoints:
        results[ep.lstrip("/").replace("/", "_")] = benchmark_endpoint(
            args.url, ep, args.concurrency, args.requests
        )

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for name, result in results.items():
            print(f"{name}: p50={result['p50']}ms p95={result['p95']}ms p99={result['p99']}ms errors={result['errors']}")

    # 检查是否有错误
    total_errors = sum(r["errors"] for r in results.values())
    if total_errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
