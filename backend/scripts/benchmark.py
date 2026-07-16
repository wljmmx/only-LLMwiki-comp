#!/usr/bin/env python3
"""性能基准测试脚本（QUAL-3）

对关键 API 端点进行并发压测，输出延迟分布和吞吐量。

用法：
    python scripts/benchmark.py [--url http://localhost:8080] [--concurrency 10] [--requests 100]

测试端点：
    - /health          — 健康检查（无 DB 依赖）
    - /api/v1/search   — 搜索 API
    - /api/v1/documents — 文档列表
    - /api/v1/wiki/pages — Wiki 页面列表
    - /api/v1/llm-wiki/query — Wiki 问答（需要 LLM，默认跳过）

输出：
    - 每个端点的 min/avg/p50/p95/p99/max 延迟（ms）
    - 吞吐量（req/s）
    - 状态码分布
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class BenchmarkResult:
    """单个端点的基准测试结果"""

    endpoint: str
    method: str = "GET"
    total_requests: int = 0
    success_count: int = 0
    error_count: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    status_codes: dict[int, int] = field(default_factory=dict)
    duration_sec: float = 0.0

    @property
    def throughput(self) -> float:
        if self.duration_sec == 0:
            return 0.0
        return self.total_requests / self.duration_sec

    @property
    def min_ms(self) -> float:
        return min(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def avg_ms(self) -> float:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p50_ms(self) -> float:
        return _percentile(self.latencies_ms, 50)

    @property
    def p95_ms(self) -> float:
        return _percentile(self.latencies_ms, 95)

    @property
    def p99_ms(self) -> float:
        return _percentile(self.latencies_ms, 99)

    @property
    def max_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.success_count / self.total_requests * 100

    def summary(self) -> str:
        lines = [
            f"\n--- {self.method} {self.endpoint} ---",
            f"  Requests:   {self.total_requests}",
            f"  Success:    {self.success_count} ({self.success_rate:.1f}%)",
            f"  Errors:     {self.error_count}",
            f"  Duration:   {self.duration_sec:.2f}s",
            f"  Throughput: {self.throughput:.1f} req/s",
            "  Latency (ms):",
            f"    min={self.min_ms:.1f}  avg={self.avg_ms:.1f}  p50={self.p50_ms:.1f}",
            f"    p95={self.p95_ms:.1f}  p99={self.p99_ms:.1f}  max={self.max_ms:.1f}",
        ]
        if self.status_codes:
            lines.append(
                "  Status codes: "
                + ", ".join(f"{k}:{v}" for k, v in sorted(self.status_codes.items()))
            )
        return "\n".join(lines)


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


async def benchmark_endpoint(
    client: httpx.AsyncClient,
    method: str,
    endpoint: str,
    total_requests: int,
    concurrency: int,
    *,
    json_body: dict | None = None,
    skip: bool = False,
) -> BenchmarkResult:
    """对单个端点进行并发基准测试"""
    result = BenchmarkResult(
        endpoint=endpoint,
        method=method,
        total_requests=total_requests,
    )

    if skip:
        result.duration_sec = 0.0
        return result

    sem = asyncio.Semaphore(concurrency)
    start = time.monotonic()

    async def _do_one() -> None:
        t0 = time.monotonic()
        try:
            async with sem:
                if method == "GET":
                    resp = await client.get(endpoint)
                elif method == "POST":
                    resp = await client.post(endpoint, json=json_body)
                else:
                    resp = await client.request(method, endpoint, json=json_body)
            elapsed = (time.monotonic() - t0) * 1000
            result.latencies_ms.append(elapsed)
            result.status_codes[resp.status_code] = (
                result.status_codes.get(resp.status_code, 0) + 1
            )
            if resp.status_code < 400:
                result.success_count += 1
            else:
                result.error_count += 1
        except Exception:
            result.error_count += 1
            result.latencies_ms.append((time.monotonic() - t0) * 1000)

    tasks = [_do_one() for _ in range(total_requests)]
    await asyncio.gather(*tasks)

    result.duration_sec = time.monotonic() - start
    return result


ENDPOINTS = [
    # (method, endpoint, skip_llm)
    ("GET", "/health", False),
    ("GET", "/api/v1/search", False),
    ("GET", "/api/v1/documents", False),
    ("GET", "/api/v1/wiki/pages", False),
    ("POST", "/api/v1/llm-wiki/query", True),  # 默认跳过 LLM 查询
    ("GET", "/api/v1/wiki/lint", False),
    ("GET", "/api/v1/graph/stats", False),
]


async def main() -> None:
    parser = argparse.ArgumentParser(description="OpsKG API 性能基准测试")
    parser.add_argument(
        "--url", default="http://localhost:8080", help="API 基础 URL"
    )
    parser.add_argument(
        "--concurrency", type=int, default=10, help="并发数"
    )
    parser.add_argument(
        "--requests", type=int, default=100, help="每个端点的请求数"
    )
    parser.add_argument(
        "--include-llm", action="store_true", help="包含 LLM 查询端点"
    )
    parser.add_argument(
        "--json", action="store_true", help="以 JSON 格式输出结果"
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")

    print("OpsKG 性能基准测试")
    print(f"  URL:        {base_url}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Requests:   {args.requests}")
    print(f"  LLM tests:  {'enabled' if args.include_llm else 'skipped'}")

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        # 预热：先 ping 一次确保服务可达
        try:
            r = await client.get("/health")
            if r.status_code != 200:
                print(f"\n[ERROR] 服务不可达: /health → {r.status_code}")
                return
            print(f"\n[OK] 服务可达: /health → {r.status_code}")
        except Exception as e:
            print(f"\n[ERROR] 无法连接服务: {e}")
            return

        results: list[BenchmarkResult] = []
        for method, endpoint, skip_llm in ENDPOINTS:
            skip = skip_llm and not args.include_llm
            label = " (skipped)" if skip else ""
            print(f"\nBenchmarking {method} {endpoint}{label} ...", end="", flush=True)
            result = await benchmark_endpoint(
                client,
                method,
                endpoint,
                args.requests,
                args.concurrency,
                skip=skip,
                json_body={"question": "test"} if method == "POST" else None,
            )
            results.append(result)
            print(" done")

        # 输出汇总
        print("\n" + "=" * 60)
        print("BENCHMARK RESULTS")
        print("=" * 60)

        if args.json:
            output = {
                "config": {
                    "url": base_url,
                    "concurrency": args.concurrency,
                    "requests_per_endpoint": args.requests,
                },
                "results": [
                    {
                        "endpoint": f"{r.method} {r.endpoint}",
                        "total": r.total_requests,
                        "success": r.success_count,
                        "errors": r.error_count,
                        "throughput": round(r.throughput, 1),
                        "latency_ms": {
                            "min": round(r.min_ms, 1),
                            "avg": round(r.avg_ms, 1),
                            "p50": round(r.p50_ms, 1),
                            "p95": round(r.p95_ms, 1),
                            "p99": round(r.p99_ms, 1),
                            "max": round(r.max_ms, 1),
                        },
                        "status_codes": r.status_codes,
                    }
                    for r in results
                ],
            }
            print(json.dumps(output, indent=2))
        else:
            for r in results:
                print(r.summary())

            # 汇总表
            print("\n--- Summary Table ---")
            print(
                f"{'Endpoint':<35} {'Success':>8} {'Avg(ms)':>8} "
                f"{'P95(ms)':>8} {'Req/s':>8}"
            )
            print("-" * 72)
            for r in results:
                if r.total_requests == 0:
                    continue
                print(
                    f"{r.method + ' ' + r.endpoint:<35} "
                    f"{r.success_rate:>7.1f}% "
                    f"{r.avg_ms:>7.1f} "
                    f"{r.p95_ms:>7.1f} "
                    f"{r.throughput:>7.1f}"
                )


if __name__ == "__main__":
    asyncio.run(main())
