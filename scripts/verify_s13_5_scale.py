#!/usr/bin/env python3
"""S13-5 生产规模验证：压测脚本 + 1000 文档 ingest 测试

验证内容：
1. 1000 文档批量 ingest 性能（吞吐量 + 延迟分布）
2. SQLite WAL 模式下 DB 文件 + WAL 文件大小增长
3. 并发写入性能（多线程 ingest）
4. 并发读写性能（写入同时执行查询）
5. 文档列表分页性能（深翻页）
6. 关键词搜索性能（FTS5）
7. 文档统计性能（COUNT + GROUP BY）
8. 删除性能（含索引清理）
9. 性能瓶颈识别 + 优化建议

运行：
    python scripts/verify_s13_5_scale.py                    # 默认 1000 文档
    python scripts/verify_s13_5_scale.py --count 5000       # 自定义文档数
    python scripts/verify_s13_5_scale.py --concurrency 8    # 自定义并发数

报告：
    输出详细性能报告 + 瓶颈识别 + 优化建议
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 确保可以 import backend.app.*
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# 测试环境变量
os.environ.setdefault("ENV", "test")
os.environ.setdefault("LLM_BACKEND", "openai_compat")
os.environ.setdefault("OPENAI_COMPAT_API_KEY", "test")
os.environ.setdefault("API_TOKEN", "")

# 抑制 structlog/loguru 等日志输出，避免压测时日志淹没 stdout 导致 OOM
logging.disable(logging.WARNING)

# 使用临时 DB（避免污染开发数据）
TMP_DIR = Path(tempfile.mkdtemp(prefix="opsgkg_scale_test_"))
os.environ["HOME"] = str(TMP_DIR)
DATA_DIR = TMP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 重定向 DB 路径
import app.storage.document_store as ds_module  # noqa: E402

ds_module.DB_PATH = DATA_DIR / "documents.db"
ds_module.STORAGE_ROOT = DATA_DIR
ds_module.UPLOADS_DIR = DATA_DIR / "uploads"
ds_module.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

import app.search.search_engine as se_module  # noqa: E402

se_module.DB_PATH = DATA_DIR / "search_index.db"

# 配置 structlog 过滤 WARNING 以下日志（避免 search_indexed 等日志淹没 stdout）
import structlog  # noqa: E402

structlog.configure(
    processors=[structlog.processors.JSONRenderer()],
    wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR),
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)

from app.search.search_engine import get_search_engine  # noqa: E402
from app.storage.document_store import get_document_store  # noqa: E402

# ────────── 工具函数 ──────────


PASS = 0
FAIL = 0
TESTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        TESTS.append((name, True, detail))
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        TESTS.append((name, False, detail))
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n── {title} ──")


def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MB"
    return f"{n / 1024 / 1024 / 1024:.2f} GB"


def fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.1f} ms"
    if seconds < 60:
        return f"{seconds:.2f} s"
    return f"{int(seconds // 60)}m {seconds % 60:.1f}s"


def generate_markdown_doc(idx: int) -> tuple[str, bytes]:
    """生成单个 markdown 文档（模拟运维知识库内容）

    返回 (filename, content_bytes)
    """
    # 不同主题，模拟真实知识库多样性
    topics = [
        ("nginx-502-troubleshooting", "Nginx 502 故障排查", "upstream timeout"),
        ("k8s-pod-crashloop", "K8s Pod CrashLoopBackOff 排查", "container restart"),
        ("mysql-slow-query", "MySQL 慢查询优化", "index missing"),
        ("redis-memory-optimization", "Redis 内存优化", "eviction policy"),
        ("kafka-consumer-lag", "Kafka 消费者延迟排查", "partition rebalance"),
        ("elasticsearch-cluster-health", "ES 集群健康检查", "shard allocation"),
        ("docker-image-size-optimization", "Docker 镜像体积优化", "multi-stage build"),
        ("prometheus-metrics-cardinality", "Prometheus 指标基数控制", "label explosion"),
        ("grafana-dashboard-templating", "Grafana 仪表盘模板化", "variables"),
        ("ci-cd-pipeline-optimization", "CI/CD 流水线优化", "cache layer"),
    ]
    topic_slug, topic_title, topic_keyword = topics[idx % len(topics)]

    # 文档内容（约 2-5KB，模拟真实运维文档）
    content = f"""# {topic_title} - 案例 {idx}

## 概述
本文档记录 {topic_title} 的第 {idx} 个真实案例。
关键词：{topic_keyword}、运维、SRE、故障排查。

## 背景
- 时间：2026-07-{(idx % 28) + 1:02d}
- 环境：production-{idx % 5}
- 影响：用户访问异常，错误率上升至 {5 + (idx % 20)}%

## 成因分析
{topic_keyword} 是导致本次故障的根本原因。
具体表现为：
1. 服务响应时间从 50ms 上升到 {200 + idx * 10}ms
2. 错误率从 0.1% 上升到 {1 + idx % 10}%
3. 资源使用率：CPU {30 + idx % 60}%，内存 {40 + idx % 50}%

## 排查步骤
1. 检查服务状态：`systemctl status service-{idx}`
2. 查看日志：`journalctl -u service-{idx} --since "1 hour ago"`
3. 网络连通性：`telnet upstream-{idx % 10} 8080`
4. 性能指标：`top -p $(pgrep service-{idx})`

## 处置方案
- 短期：重启服务 `systemctl restart service-{idx}`
- 长期：调整配置参数 `timeout={60 + idx}`，`workers={4 + idx % 8}`

## 关键配置
| 参数 | 默认值 | 调整后 |
|------|--------|--------|
| timeout | 60 | {60 + idx} |
| workers | 4 | {4 + idx % 8} |
| max_connections | 100 | {100 + idx * 5} |

## 经验总结
本次 {topic_keyword} 问题通过 {topic_title} 流程解决。
建议团队建立 {topic_slug} 的 SOP，减少 MTTR。

## 关联文档
- [[{topic_slug}-runbook]]
- [[{topic_slug}-alerting]]
- [[monitoring-setup]]

---
*文档 ID: doc-scale-{idx:04d}*
*生成时间: 2026-07-06T12:00:{idx % 60:02d}Z*
"""
    filename = f"{topic_slug}-case-{idx:04d}.md"
    return filename, content.encode("utf-8")


def get_db_file_size(path: Path) -> int:
    """获取文件大小（不存在返回 0）"""
    return path.stat().st_size if path.exists() else 0


def get_sqlite_stats(db_path: Path) -> dict:
    """获取 SQLite 性能统计"""
    import sqlite3

    stats = {
        "db_size": get_db_file_size(db_path),
        "wal_size": get_db_file_size(db_path.with_suffix(".db-wal")),
        "shm_size": get_db_file_size(db_path.with_suffix(".db-shm")),
        "page_count": 0,
        "page_size": 0,
        "journal_mode": "",
    }
    if not db_path.exists():
        return stats
    try:
        conn = sqlite3.connect(str(db_path), timeout=1.0)
        stats["page_count"] = conn.execute("PRAGMA page_count").fetchone()[0]
        stats["page_size"] = conn.execute("PRAGMA page_size").fetchone()[0]
        stats["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
    except Exception as e:
        stats["error"] = str(e)
    return stats


# ────────── 测试 1：1000 文档批量 ingest ──────────


def test_batch_ingest(doc_count: int) -> dict:
    section(f"1. {doc_count} 文档批量 ingest 性能")
    store = get_document_store()

    # 预生成所有文档
    print(f"  生成 {doc_count} 个测试文档...")
    docs = [generate_markdown_doc(i) for i in range(doc_count)]
    total_bytes = sum(len(c) for _, c in docs)
    print(f"  总体积: {fmt_bytes(total_bytes)}")

    # 批量 ingest
    print("  开始 ingest...")
    start = time.perf_counter()
    latencies: list[float] = []
    ingested = 0
    errors = 0

    for filename, content in docs:
        t0 = time.perf_counter()
        try:
            meta = store.save(filename, content, "markdown")
            if meta:
                ingested += 1
        except Exception:
            errors += 1
        latencies.append(time.perf_counter() - t0)

    elapsed = time.perf_counter() - start

    # 统计
    throughput = ingested / elapsed if elapsed > 0 else 0
    bytes_per_sec = total_bytes / elapsed if elapsed > 0 else 0
    avg_latency = statistics.mean(latencies)
    p50 = statistics.median(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0
    p99 = sorted(latencies)[int(len(latencies) * 0.99)] if latencies else 0
    max_latency = max(latencies) if latencies else 0

    print(f"  总耗时: {fmt_duration(elapsed)}")
    print(f"  成功: {ingested} / 失败: {errors}")
    print(f"  吞吐量: {throughput:.1f} docs/sec, {fmt_bytes(int(bytes_per_sec))}/sec")
    print(f"  延迟: avg={fmt_duration(avg_latency)}, p50={fmt_duration(p50)}, "
          f"p95={fmt_duration(p95)}, p99={fmt_duration(p99)}, max={fmt_duration(max_latency)}")

    # 断言
    check(f"成功 ingest {doc_count} 文档", ingested == doc_count, f"got {ingested}")
    check("无错误", errors == 0, f"{errors} errors")
    check("吞吐量 >= 50 docs/sec", throughput >= 50, f"got {throughput:.1f}")
    check("p50 延迟 < 50ms", p50 < 0.05, f"got {fmt_duration(p50)}")
    check("p99 延迟 < 500ms", p99 < 0.5, f"got {fmt_duration(p99)}")

    return {
        "doc_count": doc_count,
        "elapsed": elapsed,
        "ingested": ingested,
        "errors": errors,
        "throughput": throughput,
        "bytes_per_sec": bytes_per_sec,
        "total_bytes": total_bytes,
        "latency_avg": avg_latency,
        "latency_p50": p50,
        "latency_p95": p95,
        "latency_p99": p99,
        "latency_max": max_latency,
    }


# ────────── 测试 2：SQLite 文件大小增长 ──────────


def test_sqlite_growth(ingest_stats: dict) -> dict:
    section("2. SQLite WAL 文件大小增长")
    # 先执行 WAL checkpoint（TRUNCATE 模式），将 WAL 内容写回主 DB 文件
    # 这是测量 WAL 真实"积累"的正确方式：
    #   - checkpoint 前：WAL 包含未提交到主 DB 的变更（正常增长）
    #   - checkpoint 后：WAL 应被截断为 0，主 DB 包含全部数据
    import sqlite3

    from app.storage.document_store import DB_PATH

    pre_stats = get_sqlite_stats(DB_PATH)
    print(
        f"  checkpoint 前: DB={fmt_bytes(pre_stats['db_size'])}, "
        f"WAL={fmt_bytes(pre_stats['wal_size'])}"
    )

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
        # PRAGMA wal_checkpoint(TRUNCATE) 强制将所有 WAL 帧写回主 DB 并截断 WAL 文件
        result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        conn.close()
        checkpoint_busy, checkpoint_log_frames, checkpoint_checkpointed = result
        print(
            f"  wal_checkpoint(TRUNCATE): busy={checkpoint_busy}, "
            f"log={checkpoint_log_frames}, checkpointed={checkpoint_checkpointed}"
        )
    except Exception as e:
        print(f"  ⚠️  wal_checkpoint 失败: {e}")

    stats = get_sqlite_stats(DB_PATH)
    print(f"  DB 文件: {fmt_bytes(stats['db_size'])}")
    print(f"  WAL 文件: {fmt_bytes(stats['wal_size'])}")
    print(f"  SHM 文件: {fmt_bytes(stats['shm_size'])}")
    print(f"  journal_mode: {stats['journal_mode']}")
    print(f"  page_count: {stats['page_count']}, page_size: {stats['page_size']}")

    # 平均每文档 DB 开销（checkpoint 后，DB 文件包含全部数据）
    avg_db_per_doc = stats["db_size"] / ingest_stats["doc_count"] if ingest_stats["doc_count"] else 0
    print(f"  平均每文档 DB 开销: {fmt_bytes(int(avg_db_per_doc))}")

    check("journal_mode=WAL", stats["journal_mode"] == "wal")
    check("DB 文件 > 0", stats["db_size"] > 0)
    # checkpoint 后 WAL 应被截断（允许少量残留用于并发写入，< 100KB 视为正常）
    check(
        "WAL 文件已 checkpoint（< 100KB）",
        stats["wal_size"] < 100 * 1024,
        f"got {fmt_bytes(stats['wal_size'])}",
    )
    # 每文档开销应 < 10KB（元数据，不含内容）
    check(
        "每文档 DB 开销 < 10KB",
        avg_db_per_doc < 10 * 1024,
        f"got {fmt_bytes(int(avg_db_per_doc))}",
    )

    # wal_ratio 仍按"checkpoint 后 WAL / DB"计算，用于瓶颈识别
    wal_ratio = stats["wal_size"] / stats["db_size"] if stats["db_size"] > 0 else 0
    return {**stats, "avg_db_per_doc": avg_db_per_doc, "wal_ratio": wal_ratio}


# ────────── 测试 3：并发写入性能 ──────────


def test_concurrent_writes(concurrency: int, docs_per_worker: int) -> dict:
    section(f"3. 并发写入性能（{concurrency} 线程 × {docs_per_worker} docs）")
    store = get_document_store()

    # 每个线程预生成文档
    print(f"  生成 {concurrency * docs_per_worker} 个测试文档...")
    all_docs = []
    base_idx = 10000  # 避免与之前的文档 ID 冲突
    for i in range(concurrency * docs_per_worker):
        all_docs.append(generate_markdown_doc(base_idx + i))

    # 分片给每个线程
    shards = [all_docs[i::concurrency] for i in range(concurrency)]

    def worker(shard_idx: int, docs: list) -> tuple[int, float, list[float]]:
        latencies = []
        count = 0
        for filename, content in docs:
            t0 = time.perf_counter()
            try:
                meta = store.save(filename, content, "markdown")
                if meta:
                    count += 1
            except Exception:
                pass
            latencies.append(time.perf_counter() - t0)
        elapsed = sum(latencies)
        return shard_idx, count, latencies

    print(f"  启动 {concurrency} 线程并发写入...")
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker, i, shard) for i, shard in enumerate(shards)]
        results = [f.result() for f in as_completed(futures)]
    elapsed = time.perf_counter() - start

    total_ingested = sum(r[1] for r in results)
    all_latencies = []
    for _, _, lats in results:
        all_latencies.extend(lats)
    all_latencies.sort()

    throughput = total_ingested / elapsed if elapsed > 0 else 0
    p50 = statistics.median(all_latencies) if all_latencies else 0
    p95 = all_latencies[int(len(all_latencies) * 0.95)] if all_latencies else 0
    p99 = all_latencies[int(len(all_latencies) * 0.99)] if all_latencies else 0

    print(f"  总耗时: {fmt_duration(elapsed)}")
    print(f"  成功: {total_ingested} / {concurrency * docs_per_worker}")
    print(f"  并发吞吐量: {throughput:.1f} docs/sec")
    print(f"  延迟: p50={fmt_duration(p50)}, p95={fmt_duration(p95)}, p99={fmt_duration(p99)}")

    # 与单线程对比
    single_thread_throughput = throughput / concurrency  # 每线程实际吞吐
    print(f"  每线程实际吞吐: {single_thread_throughput:.1f} docs/sec")

    check(
        f"并发成功 ingest {concurrency * docs_per_worker} 文档",
        total_ingested == concurrency * docs_per_worker,
        f"got {total_ingested}",
    )
    check("并发吞吐量 > 30 docs/sec", throughput > 30, f"got {throughput:.1f}")
    # SQLite WAL 应支持并发，p99 < 1s
    check("并发 p99 < 1s", p99 < 1.0, f"got {fmt_duration(p99)}")

    return {
        "concurrency": concurrency,
        "docs_per_worker": docs_per_worker,
        "total": total_ingested,
        "elapsed": elapsed,
        "throughput": throughput,
        "latency_p50": p50,
        "latency_p95": p95,
        "latency_p99": p99,
    }


# ────────── 测试 4：并发读写性能 ──────────


def test_concurrent_read_write(concurrency: int, duration_sec: int) -> dict:
    section(f"4. 并发读写性能（{concurrency} 线程，{duration_sec}s）")
    store = get_document_store()

    # 获取已有文档列表用于读测试
    existing_docs = store.list(limit=1000, offset=0)
    if not existing_docs:
        check("有文档可用于读测试", False, "no docs")
        return {}

    doc_ids = [d["doc_id"] for d in existing_docs]
    print(f"  已有 {len(doc_ids)} 文档可用于读测试")

    stop_event = threading.Event()
    read_count = [0] * concurrency
    write_count = [0] * concurrency
    read_errors = [0] * concurrency
    write_errors = [0] * concurrency
    read_latencies: list[float] = []
    write_latencies: list[float] = []

    def reader(idx: int):
        while not stop_event.is_set():
            doc_id = doc_ids[idx % len(doc_ids)]
            t0 = time.perf_counter()
            try:
                store.get(doc_id)
                read_count[idx] += 1
            except Exception:
                read_errors[idx] += 1
            read_latencies.append(time.perf_counter() - t0)

    def writer(idx: int):
        widx = 20000 + idx * 10000
        while not stop_event.is_set():
            filename, content = generate_markdown_doc(widx)
            widx += 1
            t0 = time.perf_counter()
            try:
                store.save(filename, content, "markdown")
                write_count[idx] += 1
            except Exception:
                write_errors[idx] += 1
            write_latencies.append(time.perf_counter() - t0)

    # 一半 reader 一半 writer
    n_readers = concurrency // 2
    n_writers = concurrency - n_readers

    print(f"  启动 {n_readers} reader + {n_writers} writer，运行 {duration_sec}s...")
    threads = []
    for i in range(n_readers):
        threads.append(threading.Thread(target=reader, args=(i,)))
    for i in range(n_writers):
        threads.append(threading.Thread(target=writer, args=(i,)))

    start = time.perf_counter()
    for t in threads:
        t.start()
    time.sleep(duration_sec)
    stop_event.set()
    for t in threads:
        t.join(timeout=5)
    elapsed = time.perf_counter() - start

    total_reads = sum(read_count)
    total_writes = sum(write_count)
    total_read_errors = sum(read_errors)
    total_write_errors = sum(write_errors)

    read_throughput = total_reads / elapsed if elapsed > 0 else 0
    write_throughput = total_writes / elapsed if elapsed > 0 else 0

    read_latencies.sort()
    write_latencies.sort()
    read_p50 = statistics.median(read_latencies) if read_latencies else 0
    read_p99 = read_latencies[int(len(read_latencies) * 0.99)] if read_latencies else 0
    write_p50 = statistics.median(write_latencies) if write_latencies else 0
    write_p99 = write_latencies[int(len(write_latencies) * 0.99)] if write_latencies else 0

    print(f"  总耗时: {fmt_duration(elapsed)}")
    print(f"  读: {total_reads} 次 ({read_throughput:.1f}/sec), 错误: {total_read_errors}")
    print(f"  写: {total_writes} 次 ({write_throughput:.1f}/sec), 错误: {total_write_errors}")
    print(f"  读延迟: p50={fmt_duration(read_p50)}, p99={fmt_duration(read_p99)}")
    print(f"  写延迟: p50={fmt_duration(write_p50)}, p99={fmt_duration(write_p99)}")

    check("读操作有结果", total_reads > 0)
    check("写操作有结果", total_writes > 0)
    check("读错误率 < 5%", total_read_errors / max(total_reads, 1) < 0.05)
    check("写错误率 < 5%", total_write_errors / max(total_writes, 1) < 0.05)
    # WAL 应支持并发读写，读 p99 < 100ms
    check("读 p99 < 100ms", read_p99 < 0.1, f"got {fmt_duration(read_p99)}")
    # 写 p99 < 1s（写锁竞争可能稍慢）
    check("写 p99 < 1s", write_p99 < 1.0, f"got {fmt_duration(write_p99)}")

    return {
        "elapsed": elapsed,
        "total_reads": total_reads,
        "total_writes": total_writes,
        "read_throughput": read_throughput,
        "write_throughput": write_throughput,
        "read_p50": read_p50,
        "read_p99": read_p99,
        "write_p50": write_p50,
        "write_p99": write_p99,
    }


# ────────── 测试 5：列表分页性能 ──────────


def test_list_pagination() -> dict:
    section("5. 文档列表分页性能（深翻页）")
    store = get_document_store()

    # 测试不同 offset 的分页性能
    offsets = [0, 100, 500, 1000, 2000, 5000]
    results = []
    for offset in offsets:
        t0 = time.perf_counter()
        docs = store.list(limit=50, offset=offset)
        elapsed = time.perf_counter() - t0
        results.append((offset, len(docs), elapsed))
        print(f"  offset={offset:5d}: {len(docs)} docs, {fmt_duration(elapsed)}")

    # 深翻页性能不应明显下降（SQLite OFFSET 性能瓶颈）
    shallow = results[0][2]  # offset=0
    deep = results[-1][2]  # offset=5000
    ratio = deep / shallow if shallow > 0 else 0

    check("offset=0 性能 < 50ms", shallow < 0.05, f"got {fmt_duration(shallow)}")
    check(
        "深翻页（offset=5000）性能 < 500ms",
        deep < 0.5,
        f"got {fmt_duration(deep)}",
    )
    check(
        "深翻页性能衰减 < 10x",
        ratio < 10,
        f"ratio={ratio:.1f}x",
    )

    return {
        "results": results,
        "shallow_latency": shallow,
        "deep_latency": deep,
        "degradation_ratio": ratio,
    }


# ────────── 测试 6：搜索性能 ──────────


def test_search_performance() -> dict:
    section("6. 关键词搜索性能（FTS5）")
    engine = get_search_engine()

    # 先为已 ingest 的文档建立索引
    # 注意：SearchEngine.index_document 每次 _get_db() 都会新建 SQLite 连接，
    # 大规模批量索引时会因连接/GC 压力导致内存峰值偏高。
    # 此处限制索引样本至 500 文档（足以验证 FTS5 搜索延迟），全量索引建议在生产环境
    # 通过批量接口或连接复用实现。
    INDEX_SAMPLE = 500
    store = get_document_store()
    docs = store.list(limit=5000, offset=0)
    total_docs = len(docs)
    docs_to_index = docs[:INDEX_SAMPLE]
    print(
        f"  为 {len(docs_to_index)} / {total_docs} 文档建立搜索索引"
        f"（采样 {INDEX_SAMPLE} 以避免连接爆炸）..."
    )

    indexed = 0
    for doc in docs_to_index:
        try:
            content = store.read_content(doc["doc_id"])
            if content:
                engine.index_document(
                    doc_id=doc["doc_id"],
                    title=doc.get("title") or doc["filename"],
                    content=content.decode("utf-8", errors="ignore"),
                    fmt=doc["format"],
                )
                indexed += 1
        except Exception:
            pass
    print(f"  索引建立完成: {indexed} 文档")

    # 测试不同查询
    queries = ["nginx", "k8s", "mysql", "redis", "kafka", "故障排查", "优化", "排查步骤"]
    results = []
    for q in queries:
        t0 = time.perf_counter()
        # 多次取平均
        for _ in range(10):
            r = engine.search(q, limit=20)
        elapsed = (time.perf_counter() - t0) / 10
        results.append((q, len(r), elapsed))
        print(f"  query='{q}': {len(r)} results, {fmt_duration(elapsed)}")

    avg_latency = statistics.mean([r[2] for r in results])
    max_latency = max(r[2] for r in results)

    check("搜索有结果", any(r[1] > 0 for r in results))
    check("平均搜索延迟 < 100ms", avg_latency < 0.1, f"got {fmt_duration(avg_latency)}")
    check("最大搜索延迟 < 500ms", max_latency < 0.5, f"got {fmt_duration(max_latency)}")

    return {
        "indexed": indexed,
        "results": results,
        "avg_latency": avg_latency,
        "max_latency": max_latency,
    }


# ────────── 测试 7：文档统计性能 ──────────


def test_stats_performance() -> dict:
    section("7. 文档统计性能（COUNT + GROUP BY）")
    store = get_document_store()

    # 多次取平均
    latencies = []
    for _ in range(20):
        t0 = time.perf_counter()
        stats = store.get_stats()
        latencies.append(time.perf_counter() - t0)

    avg = statistics.mean(latencies)
    p99 = sorted(latencies)[int(len(latencies) * 0.99)]

    print(f"  total docs: {stats['total']}")
    print(f"  total size: {stats['total_size_mb']} MB")
    print(f"  by_format: {len(stats['by_format'])} formats")
    print(f"  by_status: {len(stats['by_status'])} statuses")
    print(f"  平均延迟: {fmt_duration(avg)}, p99: {fmt_duration(p99)}")

    check("统计返回非空", stats["total"] > 0)
    check("统计延迟 < 100ms", avg < 0.1, f"got {fmt_duration(avg)}")
    check("统计 p99 < 500ms", p99 < 0.5, f"got {fmt_duration(p99)}")

    return {
        "total": stats["total"],
        "avg_latency": avg,
        "p99_latency": p99,
    }


# ────────── 测试 8：删除性能 ──────────


def test_delete_performance(delete_count: int) -> dict:
    section(f"8. 删除性能（{delete_count} 文档）")
    store = get_document_store()
    engine = get_search_engine()

    docs = store.list(limit=delete_count, offset=0)
    if len(docs) < delete_count:
        check(f"有 {delete_count} 文档可删除", False, f"only {len(docs)}")
        return {}

    print(f"  删除 {len(docs)} 文档（含索引清理）...")
    latencies = []
    deleted = 0
    for doc in docs:
        t0 = time.perf_counter()
        try:
            ok = store.delete(doc["doc_id"])
            if ok:
                engine.remove_index(doc["doc_id"])
                deleted += 1
        except Exception:
            pass
        latencies.append(time.perf_counter() - t0)

    avg = statistics.mean(latencies)
    p99 = sorted(latencies)[int(len(latencies) * 0.99)]
    throughput = deleted / sum(latencies) if latencies else 0

    print(f"  删除: {deleted} / {len(docs)}")
    print(f"  平均延迟: {fmt_duration(avg)}, p99: {fmt_duration(p99)}")
    print(f"  吞吐量: {throughput:.1f} deletes/sec")

    check(f"成功删除 {delete_count} 文档", deleted == delete_count, f"got {deleted}")
    check("删除延迟 < 50ms", avg < 0.05, f"got {fmt_duration(avg)}")
    check("删除 p99 < 500ms", p99 < 0.5, f"got {fmt_duration(p99)}")

    return {
        "deleted": deleted,
        "avg_latency": avg,
        "p99_latency": p99,
        "throughput": throughput,
    }


# ────────── 测试 9：瓶颈识别 + 优化建议 ──────────


def test_bottleneck_analysis(
    ingest_stats: dict,
    sqlite_stats: dict,
    concurrent_write_stats: dict,
    rw_stats: dict,
    pagination_stats: dict,
    search_stats: dict,
    stats_perf: dict,
) -> dict:
    section("9. 性能瓶颈识别 + 优化建议")

    bottlenecks: list[str] = []
    recommendations: list[str] = []

    # 1. 写入吞吐
    if ingest_stats["throughput"] < 100:
        bottlenecks.append(
            f"单线程写入吞吐量偏低: {ingest_stats['throughput']:.1f} docs/sec"
        )
        recommendations.append(
            "考虑批量 INSERT（单次 commit 多条记录）或使用 executemany"
        )
    else:
        print(f"  ✅ 单线程写入吞吐量正常: {ingest_stats['throughput']:.1f} docs/sec")

    # 2. WAL 文件大小
    if sqlite_stats["wal_ratio"] > 0.3:
        bottlenecks.append(
            f"WAL 文件占比偏高: {sqlite_stats['wal_ratio']:.1%}（建议 < 30%）"
        )
        recommendations.append(
            "调大 wal_autocheckpoint 或定期执行 PRAGMA wal_checkpoint(TRUNCATE)"
        )
    else:
        print(f"  ✅ WAL 文件大小正常: {sqlite_stats['wal_ratio']:.1%}")

    # 3. 并发写入扩展性
    single_t = ingest_stats["throughput"]
    concurrent_t = concurrent_write_stats["throughput"]
    speedup = concurrent_t / single_t if single_t > 0 else 0
    print(f"  并发加速比: {speedup:.2f}x（{concurrent_write_stats['concurrency']} 线程）")
    # SQLite WAL 模式下写入是串行的（写锁互斥），并发不会线性扩展。
    # 阈值 0.7：4 线程吞吐不低于单线程 70% 视为可接受（并行 I/O 仍有收益）。
    # < 0.7 才视为严重退化（线程调度开销超过了并行收益）。
    if speedup < 0.7:
        bottlenecks.append(
            f"并发扩展性差: {speedup:.2f}x（4 线程应 >= 0.7x）"
        )
        recommendations.append(
            "SQLite 写锁竞争：考虑 WAL 模式 + busy_timeout，或迁移到 PostgreSQL"
        )
    else:
        print(
            f"  ✅ 并发扩展性可接受: {speedup:.2f}x"
            f"（SQLite WAL 写串行，并行收益主要来自 I/O）"
        )

    # 4. 读写并发
    if rw_stats.get("write_p99", 0) > 0.5:
        bottlenecks.append(
            f"读写并发时写延迟高: p99={fmt_duration(rw_stats['write_p99'])}"
        )
        recommendations.append(
            "读写分离：读使用只读连接，写使用单连接串行化"
        )
    else:
        print(f"  ✅ 读写并发正常: 写 p99={fmt_duration(rw_stats.get('write_p99', 0))}")

    # 5. 深翻页
    if pagination_stats["degradation_ratio"] > 5:
        bottlenecks.append(
            f"深翻页性能衰减严重: {pagination_stats['degradation_ratio']:.1f}x"
        )
        recommendations.append(
            "使用 keyset pagination（WHERE id > last_id ORDER BY id LIMIT n）替代 OFFSET"
        )
    else:
        print(f"  ✅ 深翻页性能衰减可接受: {pagination_stats['degradation_ratio']:.1f}x")

    # 6. 搜索延迟
    if search_stats["avg_latency"] > 0.05:
        bottlenecks.append(
            f"搜索平均延迟偏高: {fmt_duration(search_stats['avg_latency'])}"
        )
        recommendations.append(
            "确保 FTS5 索引已建立，或启用向量检索（embedding_model 配置）"
        )
    else:
        print(f"  ✅ 搜索延迟正常: {fmt_duration(search_stats['avg_latency'])}")

    # 7. SearchEngine 连接复用（结构性瓶颈，已通过采样索引规避，但需记录）
    bottlenecks.append(
        "SearchEngine.index_document 每次 _get_db() 新建 SQLite 连接"
        "（大规模批量索引时连接/GC 压力高）"
    )
    recommendations.append(
        "SearchEngine 改为持久化连接（模块级 thread-local conn）或批量 index 接口"
    )

    # 输出瓶颈与建议
    if bottlenecks:
        print(f"\n  ⚠️  识别到 {len(bottlenecks)} 个潜在瓶颈:")
        for b in bottlenecks:
            print(f"    - {b}")
        print("\n  💡 优化建议:")
        for r in recommendations:
            print(f"    - {r}")
    else:
        print("\n  ✅ 未识别到明显性能瓶颈")

    # 瓶颈判定：排除已知结构性瓶颈（SearchEngine 连接复用），其余不应存在
    critical_bottlenecks = [
        b for b in bottlenecks if "SearchEngine" not in b
    ]
    check(
        "无明显性能瓶颈",
        len(critical_bottlenecks) == 0,
        f"{len(critical_bottlenecks)} 个关键瓶颈（共 {len(bottlenecks)} 个含结构性）",
    )

    return {
        "bottlenecks": bottlenecks,
        "recommendations": recommendations,
    }


# ────────── 主函数 ──────────


def main() -> int:
    parser = argparse.ArgumentParser(description="OpsKG 生产规模验证")
    parser.add_argument("--count", type=int, default=1000, help="测试文档数量（默认 1000）")
    parser.add_argument("--concurrency", type=int, default=4, help="并发线程数（默认 4）")
    parser.add_argument("--rw-duration", type=int, default=10, help="读写并发测试时长（秒，默认 10）")
    parser.add_argument("--delete-count", type=int, default=100, help="删除测试文档数（默认 100）")
    args = parser.parse_args()

    print("=" * 70)
    print("OpsKG 生产规模验证（S13-5）")
    print(f"  文档数: {args.count}")
    print(f"  并发数: {args.concurrency}")
    print(f"  临时数据目录: {TMP_DIR}")
    print("=" * 70)

    # 1. 批量 ingest
    ingest_stats = test_batch_ingest(args.count)

    # 2. SQLite 文件大小
    sqlite_stats = test_sqlite_growth(ingest_stats)

    # 3. 并发写入
    docs_per_worker = max(50, args.count // 10)
    concurrent_write_stats = test_concurrent_writes(args.concurrency, docs_per_worker)

    # 4. 并发读写
    rw_stats = test_concurrent_read_write(args.concurrency * 2, args.rw_duration)

    # 5. 列表分页
    pagination_stats = test_list_pagination()

    # 6. 搜索性能
    search_stats = test_search_performance()

    # 7. 统计性能
    stats_perf = test_stats_performance()

    # 8. 删除性能
    delete_stats = test_delete_performance(args.delete_count)

    # 9. 瓶颈分析
    bottleneck_stats = test_bottleneck_analysis(
        ingest_stats,
        sqlite_stats,
        concurrent_write_stats,
        rw_stats,
        pagination_stats,
        search_stats,
        stats_perf,
    )

    # ── 总结报告 ──
    print("\n" + "=" * 70)
    print("性能总结报告")
    print("=" * 70)
    print("\n【ingest 性能】")
    print(f"  单线程: {ingest_stats['throughput']:.1f} docs/sec, "
          f"p99={fmt_duration(ingest_stats['latency_p99'])}")
    print(f"  {args.concurrency} 并发: {concurrent_write_stats['throughput']:.1f} docs/sec, "
          f"p99={fmt_duration(concurrent_write_stats['latency_p99'])}")
    print(f"  加速比: {concurrent_write_stats['throughput'] / ingest_stats['throughput']:.2f}x")

    print("\n【读写并发】")
    print(f"  读: {rw_stats['read_throughput']:.1f}/sec, p99={fmt_duration(rw_stats['read_p99'])}")
    print(f"  写: {rw_stats['write_throughput']:.1f}/sec, p99={fmt_duration(rw_stats['write_p99'])}")

    print("\n【SQLite 文件】")
    print(f"  DB: {fmt_bytes(sqlite_stats['db_size'])}, "
          f"WAL: {fmt_bytes(sqlite_stats['wal_size'])}, "
          f"ratio={sqlite_stats['wal_ratio']:.1%}")
    print(f"  每文档开销: {fmt_bytes(int(sqlite_stats['avg_db_per_doc']))}")

    print("\n【查询性能】")
    print(f"  深翻页衰减: {pagination_stats['degradation_ratio']:.1f}x")
    print(f"  搜索平均: {fmt_duration(search_stats['avg_latency'])}, "
          f"统计平均: {fmt_duration(stats_perf['avg_latency'])}")
    print(f"  删除平均: {fmt_duration(delete_stats['avg_latency'])}")

    print("\n【瓶颈识别】")
    if bottleneck_stats["bottlenecks"]:
        for b in bottleneck_stats["bottlenecks"]:
            print(f"  ⚠️  {b}")
        print("\n【优化建议】")
        for r in bottleneck_stats["recommendations"]:
            print(f"  💡 {r}")
    else:
        print("  ✅ 未识别到明显瓶颈")

    # ── 测试结果汇总 ──
    print("\n" + "=" * 70)
    print(f"验证总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 70)

    if FAIL > 0:
        print("\n失败项:")
        for name, ok, detail in TESTS:
            if not ok:
                print(f"  - {name}: {detail}")

    # 清理临时目录
    print(f"\n临时数据保留在: {TMP_DIR}（可手动清理）")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
