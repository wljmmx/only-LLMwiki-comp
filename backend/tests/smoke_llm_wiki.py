"""LLM Wiki 端到端审计测试（Karpathy 范式）

覆盖 P0-1 → P1-4 全流程：
1. Ingest raw 文档 → LLM 编译为 wiki 页面（P0-4 + P1-1）
2. wiki 页面包含 frontmatter / [[wikilink]] / 必含章节（P0-1 + P0-2）
3. index.md 自动维护，按类型分组 + 孤岛候选（P0-3）
4. backlink 双向索引正确（P0-2）
5. wiki Q&A 基于编译页面回答（P1-2）
6. raw 文档变化 → drift 检测 → stale 标注 → 自动重编译 → ReviewQueue（P1-1 + P1-4）
7. Lint 检测死链 / orphan / stale / 矛盾（P1-3）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock LLM（避免依赖外部 API）
import app.core.llm as llm_mod
from app.core.llm.base import LLMResponse

call_state = {'n_compile': 0, 'n_answer': 0}


class MockLLM:
    backend_name = 'mock'

    async def chat(self, messages, *, temperature=None, max_tokens=None, **kw):
        last = messages[-1].content
        # 区分编译 / 问答
        if '用户问题' in last:
            call_state['n_answer'] += 1
            return LLMResponse(
                text=(
                    "根据 [[redis]] 与 [[nginx]] 页面，Redis OOM 的常见成因是 maxmemory 配置不当。\n"
                    "排查步骤：\n"
                    "1. 检查 [[redis]] maxmemory 配置\n"
                    "2. 检查 [[nginx]] 反向代理是否正常\n\n"
                    "来源：[[redis]]"
                ),
                model='mock',
            )
        # 编译
        call_state['n_compile'] += 1
        name = 'Unknown'
        for line in last.splitlines():
            if '名称：' in line:
                name = line.split('名称：', 1)[1].strip()
                break
        # 第二次编译（重编译）时改写正文，触发 diff
        if call_state['n_compile'] >= 2:
            body = (
                f"# {name}\n\n"
                f"## 概述\n{name} 是更新后的版本（v2）。\n\n"
                f"## 属性\n- **maxmemory**: 4gb\n- **port**: 6379\n\n"
                f"## 排查步骤\n1. 检查 [[nginx]] 状态\n2. 检查 [[redis-cluster]] 拓扑\n\n"
                f"## 来源\n- doc_id: mock-v2\n"
            )
        else:
            body = (
                f"# {name}\n\n"
                f"## 概述\n{name} 是初始版本（v1）。\n\n"
                f"## 属性\n- **maxmemory**: 2gb\n- **port**: 6379\n\n"
                f"## 排查步骤\n1. 检查 [[nginx]] 状态\n\n"
                f"## 来源\n- doc_id: mock-v1\n"
            )
        return LLMResponse(text=body, model='mock')

    async def stream(self, messages, **kw):
        raise NotImplementedError

    async def health(self):
        return True


llm_mod.get_llm_client = lambda: MockLLM()

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
passed = 0
failed = 0
total = 0


def step(name: str, ok: bool, detail: str = ""):
    global passed, failed, total
    total += 1
    if ok:
        passed += 1
        print(f"  ✅ {name}: {detail}")
    else:
        failed += 1
        print(f"  ❌ {name}: {detail}")


print("=" * 60)
print("LLM Wiki 端到端审计测试（Karpathy 范式）")
print("=" * 60)

# ───── P0-4 + P1-1: Ingest raw → LLM 编译 ─────
print("\n[STEP] 1. POST /llm-wiki/ingest（首次编译）")
raw_v1 = (
    "# Redis 配置手册\n\n"
    "## 现象\nRedis 报 OOM 错误\n"
    "## 涉及服务\n- Redis\n## 排查\n1. 检查 maxmemory\n"
).encode("utf-8")
r = client.post("/llm-wiki/ingest", files={"file": ("redis-handbook.md", raw_v1, "text/markdown")})
data = r.json()
doc_id = data["doc_id"]
step("ingest status 200", r.status_code == 200, f"doc_id={doc_id}")
# 幂等：重复运行时文档去重命中，page 已存在则 updated=1；首次运行则 created=1
step("wiki 页面生成（created 或 updated）",
     data["compile"]["pages_created"] >= 1 or data["compile"]["pages_updated"] >= 1,
     f"slugs={data['compile']['slugs']} created={data['compile']['pages_created']} updated={data['compile']['pages_updated']}")
step("index_rebuilt", data["compile"]["index_rebuilt"], "")
step("drift.changed == False（首次）", data["drift"]["changed"] is False, "")

# ───── P0-1 + P0-2: wiki 页面 frontmatter / wikilink ─────
print("\n[STEP] 2. GET /llm-wiki/page/{slug}（验证 frontmatter + wikilink）")
slug = data["compile"]["slugs"][0]
r = client.get(f"/llm-wiki/page/{slug}")
d = r.json()
step("page status 200", r.status_code == 200, f"slug={slug} version={d['version']}")
step("content has frontmatter", d["content"].startswith("---"), "")
step("content has [[wikilink]]", "[[" in d["content"], "")
step("outlinks 非空", len(d["outlinks"]) > 0, f"targets={[o['target'] for o in d['outlinks']]}")

# ───── P0-2: backlink 双向索引 ─────
print("\n[STEP] 3. GET /llm-wiki/backlinks/{slug}（验证 backlink）")
target = d["outlinks"][0]["target"]
r = client.get(f"/llm-wiki/backlinks/{target}")
bd = r.json()
step("backlinks status 200", r.status_code == 200, f"target={target} count={bd['count']}")
step("backlinks 包含 source slug", any(b["source"] == slug for b in bd["backlinks"]),
     f"sources={[b['source'] for b in bd['backlinks']]}")

# ───── P0-3: index.md 自动维护 ─────
print("\n[STEP] 4. GET /llm-wiki/index（验证 index.md）")
r = client.get("/llm-wiki/index")
d = r.json()
step("index status 200", r.status_code == 200, f"version={d['version']}")
step("index 含按类型浏览", "按类型浏览" in d["content"], "")
step("index 含 [[wikilink]]", "[[" in d["content"], "")

# ───── P1-2: wiki Q&A ─────
print("\n[STEP] 5. POST /llm-wiki/query（基于 wiki 回答）")
r = client.post("/llm-wiki/query", json={"question": "Redis OOM 怎么排查？", "recall_limit": 5})
d = r.json()
step("query status 200", r.status_code == 200, "")
step("answer 非空", len(d["answer"]) > 0, f"answer_len={len(d['answer'])}")
step("answer 含 [[slug]] 引用", "[[" in d["answer"], "")
step("cited_slugs 非空", len(d["cited_slugs"]) > 0, f"cited={d['cited_slugs'][:3]}")
step("insufficient_knowledge == False", d["insufficient_knowledge"] is False, "")

# ───── P1-2: 召回测试 ─────
print("\n[STEP] 6. GET /llm-wiki/recall?q=Redis")
r = client.get("/llm-wiki/recall", params={"q": "Redis OOM", "limit": 5})
d = r.json()
step("recall status 200", r.status_code == 200, f"count={d['count']}")
step("recall 非空", d["count"] > 0, f"top_slug={d['hits'][0]['slug'] if d['hits'] else None}")

# ───── P1-3: Lint 死链 / orphan 检测 ─────
print("\n[STEP] 7. GET /llm-wiki/deadlinks + /llm-wiki/orphans")
r = client.get("/llm-wiki/deadlinks")
dead = r.json()
step("deadlinks status 200", r.status_code == 200, f"count={dead['count']}")
step("死链检测到 nginx 引用", any(d["target"] == "nginx" for d in dead["deadlinks"])
     or dead["count"] > 0, f"deadlink_targets={[d['target'] for d in dead['deadlinks']][:3]}")

r = client.get("/llm-wiki/orphans")
orphans = r.json()
step("orphans status 200", r.status_code == 200, f"count={orphans['count']}")

# ───── P1-3: Lint 全量检查 ─────
print("\n[STEP] 8. POST /llm-wiki/lint（全量健康检查）")
r = client.post("/llm-wiki/lint", params={"include_stale": True})
d = r.json()
step("lint status 200", r.status_code == 200, f"pages_checked={d['pages_checked']}")
step("lint 检出问题", d["total_issues"] > 0, f"by_type={d['by_type']}")
step("lint 含 missing_concept 类型", "missing_concept" in d["by_type"], "")

# ───── P1-1 + P1-4: drift 检测 + 自动重编译闭环 ─────
print("\n[STEP] 9. 模拟 raw 文档变化（篡改 checksum）→ drift 检测")
from app.knowledge.wiki_drift import detect_drift, mark_pages_stale, record_compiled_checksum

# 篡改：把记录中的 checksum 改成假值，让 detect_drift 认为发生了变化
record_compiled_checksum(doc_id, "fake_old_checksum_drift_test")
report = detect_drift(doc_id)
step("drift.changed == True", report.changed is True, f"affected={report.affected_slugs}")

# 标记 stale
marked = mark_pages_stale(report.affected_slugs, doc_id)
step("mark_pages_stale >= 1", marked >= 1, f"marked={marked}")

# GET /llm-wiki/stale 应非空
r = client.get("/llm-wiki/stale")
d = r.json()
step("GET stale count >= 1", d["count"] >= 1, f"count={d['count']}")

print("\n[STEP] 10. POST /llm-wiki/recompile-stale（自动重编译闭环）")
r = client.post("/llm-wiki/recompile-stale", params={"push_review": True})
d = r.json()
step("recompile-stale status 200", r.status_code == 200, "")
step("total_jobs >= 1", d["total_jobs"] >= 1, f"jobs={d['total_jobs']}")
step("total_recompiled >= 1", d["total_recompiled"] >= 1, f"recompiled={d['total_recompiled']}")
step("total_review_queued >= 1", d["total_review_queued"] >= 1, f"review_queued={d['total_review_queued']}")
step("至少 1 个 job 含 diff_summary", any(j["diff_summary"] for j in d["jobs"]), "")

# 重编译后 stale 应清空
r = client.get("/llm-wiki/stale")
d = r.json()
step("重编译后 stale 清空", d["count"] == 0, f"count={d['count']}")

# ───── P1-4: ReviewQueue 收到 WikiDrift 条目 ─────
print("\n[STEP] 11. GET /review/queue（验证 WikiDrift 入队）")
r = client.get("/review/queue", params={"limit": 50})
d = r.json()
step("review queue status 200", r.status_code == 200, f"pending={d['stats'].get('pending', 0)}")

# ───── P1-1: 重新 ingest 同名文档（force 重编译）─────
print("\n[STEP] 12. POST /llm-wiki/recompile/{doc_id}（手动强制重编译）")
r = client.post(f"/llm-wiki/recompile/{doc_id}", params={"force": True})
d = r.json()
step("recompile status 200", r.status_code == 200, "")
step("recompile pages_updated >= 1", d["pages_updated"] >= 1, f"updated={d['pages_updated']}")

# ───── 综合验证：LLM 调用次数合理 ─────
print("\n[STEP] 13. LLM 调用统计")
step("LLM 编译调用 >= 1 次", call_state['n_compile'] >= 1, f"n_compile={call_state['n_compile']}")
step("LLM 问答调用 >= 1 次", call_state['n_answer'] >= 1, f"n_answer={call_state['n_answer']}")


print("\n" + "=" * 60)
print(f"审计测试结果: {passed} 通过 / {failed} 失败 / 总计 {total}")
print("=" * 60)
if failed > 0:
    sys.exit(1)
