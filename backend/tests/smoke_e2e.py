"""端到端冒烟测试（P0/P1）

针对运行中的 HTTP 服务（默认 http://localhost:8000）走完整流水线：
  解析 → 抽取 → 上传图谱 → 审查队列 → 搜索 → 版本控制 → 模板 → 导出 → Wiki

注意：Neo4j 未运行时图谱相关步骤会优雅降级（返回 error 字段），不影响其他流程。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

BASE = "http://localhost:8000"
SAMPLE = Path(__file__).parent.parent / "data" / "smoke_nginx_runbook.md"
TIMEOUT = 30

step_results: list[tuple[str, bool, str]] = []


def step(name: str):
    def deco(fn):
        def wrap():
            print(f"\n[STEP] {name} ...", flush=True)
            try:
                msg = fn()
                step_results.append((name, True, msg or "ok"))
                print(f"  ✅ {msg or 'ok'}", flush=True)
            except Exception as e:
                step_results.append((name, False, str(e)))
                print(f"  ❌ {e}", flush=True)
            return None
        return wrap
    return deco


@step("1. health")
def s1():
    r = requests.get(f"{BASE}/health", timeout=TIMEOUT)
    assert r.status_code == 200
    return r.json()["status"]


@step("2. parse markdown")
def s2():
    with open(SAMPLE, "rb") as f:
        files = {"file": ("nginx_runbook.md", f, "text/markdown")}
        r = requests.post(f"{BASE}/parsers/parse/markdown", files=files, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["stored"] is True
    assert data["element_count"] > 0
    s1.doc_id = data["doc_id"]
    s1.title = data["title"]
    return f"doc_id={data['doc_id']} elements={data['element_count']} title={data['title']}"


@step("3. list documents")
def s3():
    r = requests.get(f"{BASE}/documents", timeout=TIMEOUT)
    assert r.status_code == 200
    docs = r.json()["documents"]
    assert any(d["doc_id"] == s1.doc_id for d in docs)
    return f"total={len(docs)}"


@step("4. document stats")
def s4():
    r = requests.get(f"{BASE}/documents/stats", timeout=TIMEOUT)
    assert r.status_code == 200
    return json.dumps(r.json(), ensure_ascii=False)


@step("5. extract knowledge")
def s5():
    with open(SAMPLE, "rb") as f:
        files = {"file": ("nginx_runbook.md", f, "text/markdown")}
        r = requests.post(f"{BASE}/extract", files=files, timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    s5.stats = data["stats"]
    return f"entities={data['stats']['total_entities']} auto={data['stats']['auto_accepted']} review={data['stats']['review_needed']}"


@step("6. graph upload (full pipeline)")
def s6():
    with open(SAMPLE, "rb") as f:
        files = {"file": ("nginx_runbook.md", f, "text/markdown")}
        r = requests.post(f"{BASE}/graph/upload", files=files, timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    return f"parsed={data['parsed_elements']} entities={data['extracted_entities']} review_queued={data['review_queued']}"


@step("7. review queue list")
def s7():
    r = requests.get(f"{BASE}/review/queue?limit=5", timeout=TIMEOUT)
    assert r.status_code == 200
    data = r.json()
    s7.pending = [i["id"] for i in data["items"]]
    return f"pending={data['stats']['pending']} items_returned={len(data['items'])}"


@step("8. review approve (writeback)")
def s8():
    if not s7.pending:
        return "no pending items to approve"
    item_id = s7.pending[0]
    r = requests.post(f"{BASE}/review/{item_id}/approve", params={"note": "smoke-test"}, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    return f"approved item_id={item_id}"


@step("9. search index check")
def s9():
    r = requests.get(f"{BASE}/search/stats", timeout=TIMEOUT)
    assert r.status_code == 200
    return json.dumps(r.json(), ensure_ascii=False)


@step("10. hybrid search")
def s10():
    r = requests.get(f"{BASE}/search", params={"q": "nginx 502", "limit": 5}, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] > 0, "search returned 0 results"
    return f"count={data['count']} top_score={data['results'][0]['combined_score']:.3f}"


@step("11. version save")
def s11():
    r = requests.post(
        f"{BASE}/versions/nginx-doc/save",
        params={"title": "Nginx 运维手册 v1", "content": "# Nginx\n初版", "author": "smoke", "change_summary": "初始版本"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    s11.v1 = r.json()["version"]
    # 保存 v2
    r = requests.post(
        f"{BASE}/versions/nginx-doc/save",
        params={"title": "Nginx 运维手册 v2", "content": "# Nginx\n## 升级\n新增章节", "author": "smoke", "change_summary": "新增升级章节"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    s11.v2 = r.json()["version"]
    return f"v1={s11.v1} v2={s11.v2}"


@step("12. version diff")
def s12():
    r = requests.get(f"{BASE}/versions/nginx-doc/diff/{s11.v1}/{s11.v2}", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    return f"added={data['added_lines']} removed={data['removed_lines']}"


@step("13. template list (builtins)")
def s13():
    r = requests.get(f"{BASE}/templates", timeout=TIMEOUT)
    assert r.status_code == 200
    slugs = [t["slug"] for t in r.json()["templates"]]
    assert "runbook" in slugs
    return f"builtin_count={len(slugs)} slugs={slugs}"


@step("14. template render")
def s14():
    r = requests.post(
        f"{BASE}/templates/runbook/render",
        json={"title": "502 排障", "service": "nginx", "steps": ["检查进程", "查看日志"]},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    assert "502 排障" in r.json()["rendered"]
    return "rendered ok"


@step("15. export markdown")
def s15():
    r = requests.post(
        f"{BASE}/export",
        json={"title": "冒烟测试文档", "content": "# 标题\n正文段落", "format": "markdown"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    assert "text/markdown" in r.headers["content-type"]
    return f"bytes={len(r.content)}"


@step("16. export html")
def s16():
    r = requests.post(
        f"{BASE}/export",
        json={"title": "冒烟测试文档", "content": "# 标题\n段落", "format": "html"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    assert b"<html" in r.content
    return f"bytes={len(r.content)}"


@step("17. wiki publish")
def s17():
    r = requests.post(
        f"{BASE}/wiki/smoke-page",
        params={"title": "冒烟测试页面", "content": "# 冒烟\n这是测试内容", "change_summary": "初始"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    return f"version={r.json().get('version')}"


@step("18. wiki get")
def s18():
    r = requests.get(f"{BASE}/wiki/smoke-page", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["title"] == "冒烟测试页面"
    return f"title={data['title']} version={data.get('version')}"


@step("19. graph stats (Neo4j graceful)")
def s19():
    r = requests.get(f"{BASE}/graph/stats", timeout=TIMEOUT)
    assert r.status_code == 200
    data = r.json()
    # Neo4j 未连接时返回 error 字段，这是预期优雅降级
    if "error" in data:
        return f"neo4j_unavailable (graceful): {data['error'][:60]}"
    return json.dumps(data, ensure_ascii=False)


@step("20. runbook generate (preview)")
def s20():
    r = requests.get(
        f"{BASE}/runbook/preview",
        params={"symptom": "nginx 502", "service": "nginx", "host": "web-prod-01", "max_docs": 3},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "runbook_md" in data
    assert data["stats"]["docs_used"] >= 0
    s20.stats = data["stats"]
    return f"docs={data['stats']['docs_used']} cmds={data['stats']['commands']} hosts={data['stats']['hosts']}"


@step("21. runbook generate + publish to wiki")
def s21():
    r = requests.post(
        f"{BASE}/runbook/generate",
        json={"symptom": "CPU 使用率过高", "service": "nginx", "publish": True},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("wiki_published") is True
    slug = data["wiki_slug"]
    # 验证 Wiki 已写入
    r2 = requests.get(f"{BASE}/wiki/{slug}", timeout=TIMEOUT)
    assert r2.status_code == 200
    assert "Runbook" in r2.json()["title"]
    return f"slug={slug} published"


@step("22. events ingest (5 alerts)")
def s22():
    # 用唯一后缀避免重复 ingest 被去重
    suffix = int(time.time())
    events = [
        {"id": f"smoke-a-{suffix}-{i}", "timestamp": f"2026-07-05T11:0{i}:00Z",
         "host": "web-prod-01", "service": "nginx", "severity": "critical" if i == 0 else "warning",
         "message": f"smoke alert {i}"}
        for i in range(3)
    ] + [
        {"id": f"smoke-b-{suffix}-{i}", "timestamp": f"2026-07-05T11:1{i}:00Z",
         "host": "db-01", "service": "mysql", "severity": "high",
         "message": f"db alert {i}"}
        for i in range(2)
    ]
    r = requests.post(f"{BASE}/events/ingest", json={"events": events}, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    assert r.json()["ingested"] == 5
    return f"ingested={r.json()['ingested']}"


@step("23. events correlate (incident grouping)")
def s23():
    r = requests.post(
        f"{BASE}/events/correlate",
        json={"since_minutes": 240},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["stats"]["incidents"] >= 2, f"expected >=2 incidents, got {data['stats']}"
    s23.first_incident = data["incidents"][0]["incident_id"]
    return f"incidents={data['stats']['incidents']} alerts={data['stats']['total_alerts']}"


@step("24. incident → runbook (auto)")
def s24():
    r = requests.post(
        f"{BASE}/events/incidents/{s23.first_incident}/runbook",
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "runbook_md" in data
    assert data.get("incident_id") == s23.first_incident
    return f"runbook_len={len(data['runbook_md'])}"


@step("25. close incident")
def s25():
    r = requests.post(
        f"{BASE}/events/incidents/{s23.first_incident}/close",
        params={"note": "smoke test resolved"},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    # 验证已关闭
    r2 = requests.get(f"{BASE}/events/incidents/{s23.first_incident}", timeout=TIMEOUT)  # noqa: F841
    # 关闭后仍可查询（通过 incidents 列表 status=closed）
    r3 = requests.get(f"{BASE}/events/incidents?status=closed", timeout=TIMEOUT)
    assert any(i["incident_id"] == s23.first_incident for i in r3.json()["incidents"])
    return f"closed={s23.first_incident}"


@step("26. changes ingest (deployment)")
def s26():
    # 取一个未关闭的 incident，倒推变更时间
    r_list = requests.get(f"{BASE}/events/incidents?status=open", timeout=TIMEOUT)
    open_incs = r_list.json()["incidents"]
    if not open_incs:
        return "no open incidents to test change correlation"
    s26.target_inc = open_incs[0]
    inc_start = s26.target_inc["started_at"]
    # 变更时间 = incident 前 2 分钟
    from datetime import datetime, timedelta
    inc_ts = datetime.fromisoformat(inc_start.replace("Z", "+00:00"))
    ch_ts = (inc_ts - timedelta(minutes=2)).isoformat()
    # 唯一 ID 避免去重
    s26.change_id = f"smoke-chg-{int(time.time())}"

    changes = [
        {
            "id": s26.change_id,
            "change_type": "deployment",
            "timestamp": ch_ts,
            "host": s26.target_inc["scope"].get("hosts", [""])[0] if s26.target_inc["scope"].get("hosts") else "",
            "service": s26.target_inc["scope"].get("services", [""])[0] if s26.target_inc["scope"].get("services") else "",
            "author": "smoke-tester",
            "ticket_id": "SMOKE-1",
            "description": "smoke test deployment",
        },
    ]
    r = requests.post(f"{BASE}/changes/ingest", json={"changes": changes}, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    assert r.json()["ingested"] == 1
    return f"ingested=1 target_inc={s26.target_inc['incident_id']}"


@step("27. changes correlate")
def s27():
    r = requests.post(
        f"{BASE}/changes/correlate",
        json={"since_hours": 12, "time_window_minutes": 30},
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # 至少应有 1 个关联
    assert data["stats"]["links"] >= 1, f"expected >=1 link, got {data['stats']}"
    # 找到对应 incident 的 link
    target = s26.target_inc["incident_id"]
    matched = [l for l in data["links"] if l["incident_id"] == target and l["change_id"] == s26.change_id]
    assert matched, f"未找到 incident {target} 的变更关联 (change_id={s26.change_id})"
    s27.link = matched[0]
    return f"links={data['stats']['links']} top_score={matched[0]['correlation_score']}"


@step("28. incident → changes (reverse query)")
def s28():
    target = s26.target_inc["incident_id"]
    r = requests.get(f"{BASE}/events/incidents/{target}/changes", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] >= 1
    assert any(c["id"] == s26.change_id for c in data["changes"])
    return f"count={data['count']}"


@step("29. rollback suggestion")
def s29():
    target = s26.target_inc["incident_id"]
    r = requests.get(f"{BASE}/events/incidents/{target}/rollback-suggestion", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["suggested"] is True
    # 建议的 change_id 可能是任意一个高分变更，验证它确实在 linked 列表中
    all_changes = data.get("all_linked_changes", [])
    assert any(c["id"] == s26.change_id for c in all_changes), \
        f"smoke change {s26.change_id} 未出现在关联列表中"
    return f"suggested=True change_id={data['change_id']} score={data['correlation_score']}"


@step("30. topology rebuild")
def s30():
    r = requests.post(f"{BASE}/topology/rebuild", params={"max_docs": 20}, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["docs_scanned"] >= 1
    return f"docs={data['docs_scanned']} nodes={data['nodes_extracted']} edges={data['edges_extracted']}"


@step("31. topology get")
def s31():
    r = requests.get(f"{BASE}/topology", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["stats"]["nodes"] > 0, "拓扑无节点"
    s31.node_name = data["nodes"][0]["name"]
    return f"nodes={data['stats']['nodes']} edges={data['stats']['edges']} by_type={data['stats']['by_type']}"


@step("32. topology impact analysis")
def s32():
    # 用 nginx 这个 component 节点测试
    r = requests.get(f"{BASE}/topology/impact/nginx", timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["node"] is not None, "未找到节点 nginx"
    # 验证 summary 字段存在
    assert "summary" in data
    return f"node={data['node']['name']} impacted={data['summary']['impacted_count']} root_causes={data['summary']['root_cause_candidates']}"


@step("33. MCP initialize")
def s33():
    r = requests.post(f"{BASE}/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
    }, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["result"]["serverInfo"]["name"] == "opskg-mcp-server"
    return f"protocol={data['result']['protocolVersion']}"


@step("34. MCP tools/list")
def s34():
    r = requests.post(f"{BASE}/mcp", json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list",
    }, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    tools = data["result"]["tools"]
    assert len(tools) >= 8, f"expected >=8 tools, got {len(tools)}"
    s34.tool_names = [t["name"] for t in tools]
    return f"tools={len(tools)} names={s34.tool_names}"


@step("35. MCP tools/call: search_knowledge")
def s35():
    r = requests.post(f"{BASE}/mcp", json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "search_knowledge", "arguments": {"query": "nginx", "limit": 2}},
    }, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    content = data["result"]["content"]
    assert content[0]["type"] == "text"
    parsed = json.loads(content[0]["text"])
    assert parsed["count"] >= 0
    return f"results={parsed['count']}"


@step("36. MCP tools/call: generate_runbook")
def s36():
    r = requests.post(f"{BASE}/mcp", json={
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "generate_runbook", "arguments": {"symptom": "nginx 502"}},
    }, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    content = data["result"]["content"]
    parsed = json.loads(content[0]["text"])
    assert "runbook_md" in parsed
    assert "故障处理 Runbook" in parsed["runbook_md"]
    return f"runbook_len={len(parsed['runbook_md'])}"


@step("37. MCP tools/call: impact_analysis")
def s37():
    r = requests.post(f"{BASE}/mcp", json={
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "impact_analysis", "arguments": {"node_name": "nginx"}},
    }, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    content = data["result"]["content"]
    parsed = json.loads(content[0]["text"])
    assert parsed["node"] is not None
    return f"impacted={parsed['summary']['impacted_count']}"


@step("38. MCP batch request")
def s38():
    # 批量请求
    r = requests.post(f"{BASE}/mcp", json=[
        {"jsonrpc": "2.0", "id": "a", "method": "tools/list"},
        {"jsonrpc": "2.0", "id": "b", "method": "tools/call",
         "params": {"name": "list_documents", "arguments": {"limit": 3}}},
    ], timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 2
    # 第二个响应应有 content
    docs_resp = data[1]
    assert "content" in docs_resp["result"]
    return f"batch_size={len(data)}"


def main():
    print("=" * 60)
    print("OpsKG 端到端冒烟测试")
    print("=" * 60)
    t0 = time.time()

    for fn in [s1, s2, s3, s4, s5, s6, s7, s8, s9, s10,
               s11, s12, s13, s14, s15, s16, s17, s18, s19, s20, s21, s22, s23, s24, s25,
               s26, s27, s28, s29, s30, s31, s32, s33, s34, s35, s36, s37, s38]:
        fn()

    elapsed = time.time() - t0
    passed = sum(1 for _, ok, _ in step_results if ok)
    failed = sum(1 for _, ok, _ in step_results if not ok)

    print("\n" + "=" * 60)
    print(f"冒烟测试结果: {passed} 通过 / {failed} 失败 / 总计 {len(step_results)}  耗时 {elapsed:.1f}s")
    print("=" * 60)
    for name, ok, msg in step_results:
        marker = "✅" if ok else "❌"
        print(f"  {marker} {name}: {msg[:80]}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
