"""S12-3 矛盾检测语义化 验证脚本

验证 LLM 辅助的语义矛盾检测（在 regex 检测之上的增强层）。

测试覆盖：
1. _parse_json_array 基础 JSON 数组解析
2. _parse_json_array 去除代码块围栏
3. _parse_json_array 提取子串
4. _parse_json_array 解析失败返回空
5. _parse_json_array 非 list 返回空
6. _collect_semantic_candidate_pairs 共享 tag 配对
7. _collect_semantic_candidate_pairs outlink 配对
8. _collect_semantic_candidate_pairs 去重
9. _collect_semantic_candidate_pairs 排除自身对
10. _llm_detect_conflicts LLM 返回冲突
11. _llm_detect_conflicts LLM 返回空
12. _llm_detect_conflicts LLM 异常容错
13. _llm_detect_conflicts 截断过长内容
14. _check_contradictions_semantic 整体流程
15. _check_contradictions_semantic 候选对限制
16. _check_contradictions_semantic 无候选返回空
17. lint_all_async 不启用语义检测（与同步等价）
18. lint_all_async 启用语义检测（添加 semantic issues）
19. lint_all_async LLM 异常自动降级
20. TYPE_CONTRADICTION_SEMANTIC 类型独立于 TYPE_CONTRADICTION
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_s123_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

import app.knowledge.wikilink as wl_mod  # noqa: E402

wl_mod.DB_PATH = TMP_DIR / "events.db"

import app.storage.version_control as vc_mod  # noqa: E402

vc_mod.DB_PATH = TMP_DIR / "versions.db"

from app.knowledge.wiki_lint import (  # noqa: E402
    TYPE_CONTRADICTION,
    TYPE_CONTRADICTION_SEMANTIC,
    _check_contradictions_semantic,
    _collect_semantic_candidate_pairs,
    _llm_detect_conflicts,
    _parse_json_array,
    lint_all,
    lint_all_async,
)
from app.knowledge.wikilink import update_backlinks  # noqa: E402
from app.storage.version_control import get_version_control  # noqa: E402

# ────────── 测试桩 ──────────


class _FakeLLMClient:
    """模拟 LLM 客户端"""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    async def chat(self, messages, temperature=0.0, max_tokens=None):
        idx = min(self.calls, len(self.responses) - 1)
        text = self.responses[idx]
        self.calls += 1

        class _Resp:
            pass

        r = _Resp()
        r.text = text
        return r


class _ExplodingLLM:
    """始终抛异常的 LLM"""

    async def chat(self, messages, temperature=0.0, max_tokens=None):
        raise RuntimeError("LLM 不可用")


class _FakeSettings:
    llm_max_tokens = 1024


def _seed_page(slug: str, title: str, body: str, tags=None, vc=None):
    """在版本库中创建一个 wiki 页面"""
    if vc is None:
        vc = get_version_control()
    tags = tags or []
    front = (
        "---\n"
        f"slug: {slug}\n"
        f"title: {title}\n"
        "type: concept\n"
        f"tags: {tags}\n"
        "sources: []\n"
        "updated_at: 2026-07-05T00:00:00Z\n"
        "review_status: auto\n"
        "---\n\n"
    )
    content = front + body
    vc.save_version(
        doc_key=f"wiki:{slug}",
        title=title,
        content=content,
        author="test-seed",
        change_summary="seed",
    )
    update_backlinks(slug, content)


# ────────── 测试 ──────────


def test_parse_json_basic():
    """测试 1: _parse_json_array 基础解析"""
    print("\n[1/20] 测试 _parse_json_array 基础解析...")
    text = '[{"summary": "端口冲突", "evidence_a": "80", "evidence_b": "8080"}]'
    result = _parse_json_array(text)
    assert len(result) == 1
    assert result[0]["summary"] == "端口冲突"
    print("  ✓ 基础 JSON 数组解析成功")


def test_parse_json_codefence():
    """测试 2: _parse_json_array 去除代码块围栏"""
    print("\n[2/20] 测试 _parse_json_array 去除代码块围栏...")
    text = '```json\n[{"summary": "x"}]\n```'
    result = _parse_json_array(text)
    assert len(result) == 1
    assert result[0]["summary"] == "x"
    print("  ✓ 去除代码块围栏成功")


def test_parse_json_extract_substring():
    """测试 3: _parse_json_array 提取子串"""
    print("\n[3/20] 测试 _parse_json_array 提取子串...")
    text = '前缀文字 [{"summary": "y"}] 后缀文字'
    result = _parse_json_array(text)
    assert len(result) == 1
    assert result[0]["summary"] == "y"
    print("  ✓ 提取子串成功")


def test_parse_json_invalid():
    """测试 4: _parse_json_array 解析失败返回空"""
    print("\n[4/20] 测试 _parse_json_array 解析失败...")
    assert _parse_json_array("not json") == []
    assert _parse_json_array("") == []
    assert _parse_json_array("[invalid") == []
    print("  ✓ 解析失败返回空列表")


def test_parse_json_non_list():
    """测试 5: _parse_json_array 非 list 返回空"""
    print("\n[5/20] 测试 _parse_json_array 非 list...")
    # 返回 JSON 对象而非数组
    assert _parse_json_array('{"summary": "x"}') == []
    print("  ✓ 非 list 返回空")


def test_collect_pairs_shared_tag():
    """测试 6: _collect_semantic_candidate_pairs 共享 tag 配对"""
    print("\n[6/20] 测试共享 tag 配对...")
    metas = {
        "page-a": {"slug": "page-a", "tags": ["nginx", "web"]},
        "page-b": {"slug": "page-b", "tags": ["nginx"]},
        "page-c": {"slug": "page-c", "tags": ["redis"]},
    }
    pairs = _collect_semantic_candidate_pairs(metas)
    # a-b 共享 nginx；a-c 无共享；b-c 无共享
    assert ("page-a", "page-b") in pairs or ("page-b", "page-a") in pairs
    assert len(pairs) == 1
    print(f"  ✓ 共享 tag 配对: {pairs}")


def test_collect_pairs_outlink():
    """测试 7: _collect_semantic_candidate_pairs outlink 配对"""
    print("\n[7/20] 测试 outlink 配对...")
    # 准备：page-x outlink 到 page-y
    _seed_page("page-x", "X", "# X\n\n参见 [[page-y]] 的内容。")
    _seed_page("page-y", "Y", "# Y\n\nY 正文。")
    _seed_page("page-z", "Z", "# Z\n\nZ 正文。")  # 无 outlink

    metas = {
        "page-x": {"slug": "page-x", "tags": []},
        "page-y": {"slug": "page-y", "tags": []},
        "page-z": {"slug": "page-z", "tags": []},
    }
    pairs = _collect_semantic_candidate_pairs(metas)
    # x-y 应通过 outlink 配对
    assert any(sorted(p) == ["page-x", "page-y"] for p in pairs)
    # z 不应出现在任何配对中
    for a, b in pairs:
        assert "page-z" not in (a, b)
    print(f"  ✓ outlink 配对: {pairs}")


def test_collect_pairs_dedup():
    """测试 8: _collect_semantic_candidate_pairs 去重"""
    print("\n[8/20] 测试去重...")
    # 两个页面共享 tag 且互相 outlink —— 应只出现一次
    _seed_page("a1", "A1", "# A1\n\n参见 [[a2]]。", tags=["shared"])
    _seed_page("a2", "A2", "# A2\n\n参见 [[a1]]。", tags=["shared"])
    metas = {
        "a1": {"slug": "a1", "tags": ["shared"]},
        "a2": {"slug": "a2", "tags": ["shared"]},
    }
    pairs = _collect_semantic_candidate_pairs(metas)
    # 应只有 1 对（去重）
    assert len(pairs) == 1
    print(f"  ✓ 去重后仅 1 对: {pairs}")


def test_collect_pairs_skip_self():
    """测试 9: _collect_semantic_candidate_pairs 排除自身对"""
    print("\n[9/20] 测试排除自身对...")
    metas = {
        "only": {"slug": "only", "tags": ["solo"]},
    }
    pairs = _collect_semantic_candidate_pairs(metas)
    assert pairs == []
    print("  ✓ 单页面无配对")


def test_llm_detect_conflicts_with_conflicts():
    """测试 10: _llm_detect_conflicts LLM 返回冲突"""
    print("\n[10/20] 测试 LLM 返回冲突...")
    llm = _FakeLLMClient(
        ['[{"summary": "端口冲突", "evidence_a": "80", "evidence_b": "8080"}]']
    )
    conflicts = asyncio.run(
        _llm_detect_conflicts(
            llm, _FakeSettings(),
            slug_a="a", content_a="---\nslug: a\n---\n\n# A\n\n端口 80",
            slug_b="b", content_b="---\nslug: b\n---\n\n# B\n\n端口 8080",
        )
    )
    assert len(conflicts) == 1
    assert conflicts[0]["summary"] == "端口冲突"
    print(f"  ✓ 检测到冲突: {conflicts}")


def test_llm_detect_conflicts_no_conflicts():
    """测试 11: _llm_detect_conflicts LLM 返回空"""
    print("\n[11/20] 测试 LLM 返回空...")
    llm = _FakeLLMClient(["[]"])
    conflicts = asyncio.run(
        _llm_detect_conflicts(
            llm, _FakeSettings(),
            slug_a="a", content_a="# A\n\n内容",
            slug_b="b", content_b="# B\n\n内容",
        )
    )
    assert conflicts == []
    print("  ✓ 无冲突时返回空")


def test_llm_detect_conflicts_exception():
    """测试 12: _llm_detect_conflicts LLM 异常容错"""
    print("\n[12/20] 测试 LLM 异常容错...")
    conflicts = asyncio.run(
        _llm_detect_conflicts(
            _ExplodingLLM(), _FakeSettings(),
            slug_a="a", content_a="# A",
            slug_b="b", content_b="# B",
        )
    )
    assert conflicts == []
    print("  ✓ LLM 异常时返回空列表")


def test_llm_detect_conflicts_truncates():
    """测试 13: _llm_detect_conflicts 截断过长内容"""
    print("\n[13/20] 测试截断过长内容...")
    captured_prompt = []

    class _CaptureLLM:
        async def chat(self, messages, temperature=0.0, max_tokens=None):
            captured_prompt.append(messages[1].content)
            class _R:
                text = "[]"
            return _R()

    long_body = "x" * 5000  # 远超 2000 字符
    asyncio.run(
        _llm_detect_conflicts(
            _CaptureLLM(), _FakeSettings(),
            slug_a="a", content_a=f"---\nslug: a\n---\n\n{long_body}",
            slug_b="b", content_b="# B",
        )
    )
    # 验证 prompt 中的 body 被截断
    assert len(captured_prompt[0]) < 5000
    print(f"  ✓ 内容已截断，prompt 长度 {len(captured_prompt[0])}")


def test_check_semantic_overall():
    """测试 14: _check_contradictions_semantic 整体流程"""
    print("\n[14/20] 测试 _check_contradictions_semantic 整体流程...")
    vc = get_version_control()
    _seed_page("s-a", "A", "# A\n\n端口 80", tags=["nginx"])
    _seed_page("s-b", "B", "# B\n\n端口 8080", tags=["nginx"])

    # Monkey-patch get_llm_client / get_settings

    orig_llm = None
    orig_settings = None
    try:
        # 通过 monkeypatch 注入
        import app.config as cfg
        import app.core.llm as llm_core

        orig_llm = llm_core.get_llm_client
        orig_settings = cfg.get_settings
        llm_core.get_llm_client = lambda: _FakeLLMClient(
            ['[{"summary": "端口冲突", "evidence_a": "80", "evidence_b": "8080"}]']
        )
        cfg.get_settings = lambda: _FakeSettings()

        page_contents = {
            "s-a": vc.get_latest("wiki:s-a")["content"],
            "s-b": vc.get_latest("wiki:s-b")["content"],
        }
        metas = {
            "s-a": {"slug": "s-a", "tags": ["nginx"]},
            "s-b": {"slug": "s-b", "tags": ["nginx"]},
        }

        issues = asyncio.run(
            _check_contradictions_semantic(page_contents, metas, max_pairs=10)
        )
        assert len(issues) == 1
        assert issues[0].type == TYPE_CONTRADICTION_SEMANTIC
        assert "端口冲突" in issues[0].message
        assert issues[0].detail["other_slug"] in ("s-a", "s-b")
        print(f"  ✓ 整体流程: {issues[0].message}")
    finally:
        if orig_llm:
            llm_core.get_llm_client = orig_llm
        if orig_settings:
            cfg.get_settings = orig_settings


def test_check_semantic_max_pairs():
    """测试 15: _check_contradictions_semantic 候选对限制"""
    print("\n[15/20] 测试候选对限制...")
    vc = get_version_control()
    # 5 个页面共享 tag → C(5,2)=10 对
    for i in range(5):
        _seed_page(f"m-{i}", f"M{i}", f"# M{i}\n\n内容 {i}", tags=["shared"])

    page_metas = {f"m-{i}": {"slug": f"m-{i}", "tags": ["shared"]} for i in range(5)}
    page_contents = {
        f"m-{i}": vc.get_latest(f"wiki:m-{i}")["content"] for i in range(5)
    }

    # Monkey-patch LLM
    import app.config as cfg
    import app.core.llm as llm_core

    orig_llm = llm_core.get_llm_client
    orig_settings = cfg.get_settings
    fake_llm = _FakeLLMClient(["[]"] * 100)
    llm_core.get_llm_client = lambda: fake_llm
    cfg.get_settings = lambda: _FakeSettings()

    try:
        # 限制 max_pairs=3
        asyncio.run(
            _check_contradictions_semantic(page_contents, page_metas, max_pairs=3)
        )
        assert fake_llm.calls == 3, f"应调用 3 次 LLM，实际 {fake_llm.calls}"
        print(f"  ✓ max_pairs=3 时调用 {fake_llm.calls} 次 LLM")
    finally:
        llm_core.get_llm_client = orig_llm
        cfg.get_settings = orig_settings


def test_check_semantic_no_candidates():
    """测试 16: _check_contradictions_semantic 无候选返回空"""
    print("\n[16/20] 测试无候选返回空...")
    vc = get_version_control()
    _seed_page("solo", "Solo", "# Solo\n\n内容")

    page_metas = {"solo": {"slug": "solo", "tags": []}}
    page_contents = {"solo": vc.get_latest("wiki:solo")["content"]}

    import app.config as cfg
    import app.core.llm as llm_core

    orig_llm = llm_core.get_llm_client
    orig_settings = cfg.get_settings
    fake_llm = _FakeLLMClient(["[]"])
    llm_core.get_llm_client = lambda: fake_llm
    cfg.get_settings = lambda: _FakeSettings()

    try:
        issues = asyncio.run(
            _check_contradictions_semantic(page_contents, page_metas, max_pairs=10)
        )
        assert issues == []
        assert fake_llm.calls == 0  # 无候选不应调用 LLM
        print("  ✓ 无候选时不调用 LLM，返回空")
    finally:
        llm_core.get_llm_client = orig_llm
        cfg.get_settings = orig_settings


def test_lint_all_async_no_semantic():
    """测试 17: lint_all_async 不启用语义检测（与同步等价）"""
    print("\n[17/20] 测试 lint_all_async 不启用语义检测...")
    get_version_control()
    _seed_page("alone", "Alone", "# Alone\n\n内容")

    sync_report = lint_all(include_stale=False)
    async_report = asyncio.run(
        lint_all_async(include_stale=False, include_semantic=False)
    )
    assert sync_report.pages_checked == async_report.pages_checked
    assert len(sync_report.issues) == len(async_report.issues)
    print(
        f"  ✓ 同步/异步等价: pages={sync_report.pages_checked}, issues={len(sync_report.issues)}"
    )


def test_lint_all_async_with_semantic():
    """测试 18: lint_all_async 启用语义检测添加 semantic issues"""
    print("\n[18/20] 测试 lint_all_async 启用语义检测...")
    get_version_control()
    _seed_page("sem-a", "A", "# A\n\n端口 80", tags=["nginx"])
    _seed_page("sem-b", "B", "# B\n\n端口 8080", tags=["nginx"])

    import app.config as cfg
    import app.core.llm as llm_core

    orig_llm = llm_core.get_llm_client
    orig_settings = cfg.get_settings
    llm_core.get_llm_client = lambda: _FakeLLMClient(
        ['[{"summary": "端口冲突", "evidence_a": "80", "evidence_b": "8080"}]']
    )
    cfg.get_settings = lambda: _FakeSettings()

    try:
        report = asyncio.run(
            lint_all_async(include_stale=False, include_semantic=True)
        )
        semantic_count = report.by_type.get(TYPE_CONTRADICTION_SEMANTIC, 0)
        assert semantic_count >= 1, f"应至少有 1 个 semantic issue，实际 {semantic_count}"
        print(f"  ✓ 启用语义检测: semantic issues = {semantic_count}")
    finally:
        llm_core.get_llm_client = orig_llm
        cfg.get_settings = orig_settings


def test_lint_all_async_llm_failure_degrades():
    """测试 19: lint_all_async LLM 异常自动降级"""
    print("\n[19/20] 测试 LLM 异常自动降级...")
    get_version_control()
    _seed_page("deg-a", "A", "# A\n\n端口 80", tags=["nginx"])
    _seed_page("deg-b", "B", "# B\n\n端口 8080", tags=["nginx"])

    import app.config as cfg
    import app.core.llm as llm_core

    orig_llm = llm_core.get_llm_client
    orig_settings = cfg.get_settings
    llm_core.get_llm_client = lambda: _ExplodingLLM()
    cfg.get_settings = lambda: _FakeSettings()

    try:
        # LLM 异常不应导致整个 lint 崩溃
        report = asyncio.run(
            lint_all_async(include_stale=False, include_semantic=True)
        )
        # 没有 semantic issues（因为 LLM 失败），但 regex 部分仍可能有
        semantic_count = report.by_type.get(TYPE_CONTRADICTION_SEMANTIC, 0)
        assert semantic_count == 0
        print(f"  ✓ LLM 异常降级: semantic issues = {semantic_count}, total = {len(report.issues)}")
    finally:
        llm_core.get_llm_client = orig_llm
        cfg.get_settings = orig_settings


def test_type_separation():
    """测试 20: TYPE_CONTRADICTION_SEMANTIC 独立于 TYPE_CONTRADICTION"""
    print("\n[20/20] 测试类型独立...")
    assert TYPE_CONTRADICTION != TYPE_CONTRADICTION_SEMANTIC
    assert TYPE_CONTRADICTION == "contradiction"
    assert TYPE_CONTRADICTION_SEMANTIC == "contradiction_semantic"
    print("  ✓ 两种矛盾类型独立可区分")


def main():
    print("=" * 60)
    print("S12-3 矛盾检测语义化 验证")
    print("=" * 60)

    test_parse_json_basic()
    test_parse_json_codefence()
    test_parse_json_extract_substring()
    test_parse_json_invalid()
    test_parse_json_non_list()
    test_collect_pairs_shared_tag()
    test_collect_pairs_outlink()
    test_collect_pairs_dedup()
    test_collect_pairs_skip_self()
    test_llm_detect_conflicts_with_conflicts()
    test_llm_detect_conflicts_no_conflicts()
    test_llm_detect_conflicts_exception()
    test_llm_detect_conflicts_truncates()
    test_check_semantic_overall()
    test_check_semantic_max_pairs()
    test_check_semantic_no_candidates()
    test_lint_all_async_no_semantic()
    test_lint_all_async_with_semantic()
    test_lint_all_async_llm_failure_degrades()
    test_type_separation()

    print("\n" + "=" * 60)
    print("✓ S12-3 全部 20 项验证通过！")
    print("=" * 60)


if __name__ == "__main__":
    main()
