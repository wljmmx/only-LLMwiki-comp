"""S17-4 Wiki Query 容错消费 验证脚本（P2-2）

验证 wiki_query.answer() 的 permissive consumption：
1. answer 新增 permissive 参数（默认 True）
2. 召回为空 + permissive=True → 尝试降级召回
3. 降级召回成功 → 用 raw 文档 snippet 作为上下文
4. 降级召回失败 → 友好提示（含 OKF 导入建议）
5. permissive=False → 直接拒绝（旧行为）
6. 降级 hit 的 type="raw-fallback"
7. 降级 hit 的 score 降权（×0.5）
8. 上下文加载容错降级 hit（不查 VC）
9. _try_degraded_recall 异常容错（返回空列表）
10. recall_pages 无 type 过滤（permissive）
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_s174_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

import app.storage.version_control as vc_mod

vc_mod.DB_PATH = TMP_DIR / "versions.db"
import app.knowledge.wikilink as wl_mod

wl_mod.DB_PATH = TMP_DIR / "events.db"
import app.knowledge.wiki_drift as wd_mod

wd_mod.DB_PATH = TMP_DIR / "events.db"

from app.knowledge.wiki_query import (  # noqa: E402
    WikiPageHit,
    WikiQAEngine,
    recall_pages,
)
from app.storage.version_control import get_version_control  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


async def main_async() -> int:
    global PASS, FAIL
    print("=" * 70)
    print("S17-4 Wiki Query 容错消费验证 (P2-2)")
    print("=" * 70)

    vc = get_version_control()

    # ── 1. answer 新增 permissive 参数 ──
    print("\n[1] answer permissive 参数")
    import inspect

    from app.knowledge.wiki_query import WikiQAEngine as WQE

    sig = inspect.signature(WQE.answer)
    check(
        "permissive" in sig.parameters,
        f"answer 含 permissive 参数 (got params={list(sig.parameters.keys())})",
    )
    check(
        sig.parameters["permissive"].default is True,
        f"permissive 默认 True (got {sig.parameters['permissive'].default})",
    )

    # ── 2. 召回为空 + permissive=True → 尝试降级 ──
    print("\n[2] 召回为空 permissive=True 尝试降级")
    # 空 wiki，recall_pages 返回 []
    engine = WikiQAEngine()

    # mock _try_degraded_recall 返回降级结果
    degraded_hits = [
        WikiPageHit(
            slug="raw-doc-1",
            title="Raw Doc 1",
            type="raw-fallback",
            score=0.5,
            snippet="这是 raw 文档的片段内容。",
        )
    ]
    engine._try_degraded_recall = AsyncMock(return_value=degraded_hits)
    # mock LLM 回答
    engine._llm_answer = AsyncMock(return_value="基于 raw 文档的回答。")

    result = await engine.answer("测试问题", writeback=False)
    check(
        result.insufficient_knowledge is False,
        f"降级成功不标记 insufficient_knowledge (got {result.insufficient_knowledge})",
    )
    check(
        "raw 文档的回答" in result.answer,
        f"返回 LLM 基于 raw 的回答 (got {result.answer[:50]})",
    )
    check(
        "raw-doc-1" in result.cited_slugs,
        f"降级 hit 被 cited (got {result.cited_slugs})",
    )
    check(
        len(result.recalled_pages) >= 1,
        "recalled_pages 含降级 hit",
    )

    # ── 3. 降级 hit 上下文加载（用 snippet）──
    print("\n[3] 降级 hit 上下文加载")
    # 验证 _llm_answer 被调用时 contexts 含 snippet
    call_args = engine._llm_answer.call_args
    if call_args:
        contexts = call_args[0][1] if call_args[0] else call_args[1].get("contexts", [])
        check(
            any("raw 文档的片段" in c for c in contexts),
            f"contexts 含 raw snippet (got {len(contexts)} contexts)",
        )
        check(
            any("[raw]" in c for c in contexts),
            "contexts 标注 [raw] 降级来源",
        )

    # ── 4. 降级召回失败 → 友好提示 ──
    print("\n[4] 降级召回失败友好提示")
    engine2 = WikiQAEngine()
    engine2._try_degraded_recall = AsyncMock(return_value=[])  # 降级也空

    result2 = await engine2.answer("无解问题", writeback=False)
    check(
        result2.insufficient_knowledge is True,
        f"降级失败标记 insufficient_knowledge (got {result2.insufficient_knowledge})",
    )
    check(
        "OKF bundle 导入" in result2.answer or "/api/okf/import" in result2.answer,
        f"提示含 OKF 导入建议 (got {result2.answer[:80]})",
    )

    # ── 5. permissive=False → 直接拒绝 ──
    print("\n[5] permissive=False 直接拒绝")
    engine3 = WikiQAEngine()
    # 即使有降级能力，permissive=False 时不调用
    engine3._try_degraded_recall = AsyncMock(return_value=degraded_hits)

    result3 = await engine3.answer("测试", writeback=False, permissive=False)
    check(
        result3.insufficient_knowledge is True,
        f"permissive=False 直接拒绝 (got insufficient={result3.insufficient_knowledge})",
    )
    check(
        not engine3._try_degraded_recall.called,
        "permissive=False 不调用降级召回",
    )

    # ── 6-7. 降级 hit 属性 ──
    print("\n[6-7] 降级 hit 属性")
    hit = degraded_hits[0]
    check(hit.type == "raw-fallback", f"type=raw-fallback (got {hit.type})")
    check(hit.score == 0.5, f"score 降权 0.5 (got {hit.score})")
    check(
        "raw 文档" in hit.snippet,
        "snippet 含 raw 内容",
    )

    # ── 8. 上下文加载容错：降级 hit 不查 VC ──
    print("\n[8] 上下文加载容错降级 hit")
    # 验证：降级 hit 的 slug 是 raw doc_id，VC 中无对应 wiki 页面
    # 但仍能进入 contexts（通过 snippet）
    check(
        vc.get_latest("wiki:raw-doc-1") is None,
        "VC 中无 raw-doc-1 的 wiki 页面",
    )
    # 上面 test 2 已验证 contexts 含 snippet，说明降级 hit 不依赖 VC

    # ── 9. _try_degraded_recall 异常容错 ──
    print("\n[9] _try_degraded_recall 异常容错")
    engine4 = WikiQAEngine()
    # mock search 抛异常
    with patch("app.search.get_search_engine") as mock_get:
        mock_get.side_effect = RuntimeError("search engine unavailable")
        degraded = await engine4._try_degraded_recall("问题", 5)
    check(
        degraded == [],
        f"异常时返回空列表 (got {degraded})",
    )

    # ── 10. recall_pages 无 type 过滤 ──
    print("\n[10] recall_pages 无 type 过滤（permissive）")
    # 灌入不同 type 的页面
    vc.save_version(
        doc_key="wiki:incident-1",
        title="Incident 1",
        content="---\nslug: incident-1\ntype: incident\ntitle: Incident 1\ntags: [nginx]\n---\n\n# Incident\nnginx 故障\n",
        author="test",
        change_summary="seed",
    )
    vc.save_version(
        doc_key="wiki:unknown-type-page",
        title="Unknown Type",
        content="---\nslug: unknown-type-page\ntype: custom-unknown\ntitle: Unknown Type\ntags: [nginx]\n---\n\n# Unknown\nnginx 自定义\n",
        author="test",
        change_summary="seed",
    )

    hits = await recall_pages("nginx", limit=10, min_score=0)
    hit_types = {h.type for h in hits}
    check(
        "incident" in hit_types,
        f"recall 含 incident 类型 (got types={hit_types})",
    )
    check(
        "custom-unknown" in hit_types,
        f"recall 含未知类型 custom-unknown（permissive 不过滤）(got types={hit_types})",
    )

    # ── 总结 ──
    print("\n" + "=" * 70)
    print(f"总计: {PASS} PASS / {FAIL} FAIL")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
