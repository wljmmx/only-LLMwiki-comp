"""Wiki 管道端到端测试（P0 → P1 全流程）

覆盖：
1. 文档上传（parsers/parse）→ 获取 doc_id
2. LLM 编译为 wiki 页面（llm-wiki/recompile）
3. 基于 wiki 的问答（llm-wiki/query）
4. wiki 页面列表（llm-wiki/pages）
5. wiki 健康检查（llm-wiki/lint）

测试分层：
- 无需 LLM：upload / list / lint（总是运行）
- 需要 LLM：compile / query（openai 不可用时跳过）
"""

from __future__ import annotations

import io
import os
from pathlib import Path

# 确保测试期间关闭认证（开发模式放行）
os.environ.setdefault("OPSKG_API_TOKEN", "")

import pytest
from fastapi.testclient import TestClient

from app.main import app

# ────────── LLM 可用性检测 ──────────

try:
    import openai  # noqa: F401

    _openai_available = True
except ImportError:
    _openai_available = False

requires_llm = pytest.mark.skipif(
    not _openai_available,
    reason="openai SDK 未安装，LLM 不可用",
)

# ────────── 测试客户端 ──────────

client = TestClient(app)

# ────────── Fixture 辅助 ──────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _read_sample_md() -> bytes:
    """读取 sample.md fixture 内容"""
    sample_path = FIXTURES_DIR / "sample.md"
    if not sample_path.exists():
        pytest.skip(f"fixture 文件不存在: {sample_path}")
    return sample_path.read_bytes()


# ────────── 测试类 ──────────


class TestE2EWikiPipeline:
    """Wiki 管道端到端测试

    按顺序执行：upload → compile → query → list → lint
    通过类变量 _doc_id 在测试间传递文档 ID。
    """

    _doc_id: str | None = None
    _compiled: bool = False

    # ── 1. 文档上传 ──

    def test_upload_document(self) -> None:
        """上传 sample.md → 解析为文档 → 返回 doc_id

        POST /parsers/parse/markdown
        """
        content = _read_sample_md()
        r = client.post(
            "/parsers/parse/markdown",
            files={"file": ("sample.md", io.BytesIO(content), "text/markdown")},
        )
        assert r.status_code == 200, f"上传失败: {r.text}"

        data = r.json()
        assert "doc_id" in data, f"响应缺少 doc_id: {data}"
        assert data.get("stored") is True, f"文档未持久化: {data}"
        assert data.get("format") == "markdown", f"格式错误: {data.get('format')}"

        # 验证解析出了标题
        assert data.get("title"), f"标题为空: {data}"

        # 保存 doc_id 供后续测试使用
        TestE2EWikiPipeline._doc_id = data["doc_id"]

    # ── 2. LLM 编译为 wiki ──

    @requires_llm
    def test_compile_to_wiki(self) -> None:
        """将已上传文档编译为 wiki 页面

        POST /llm-wiki/recompile/{doc_id}?force=true
        """
        doc_id = TestE2EWikiPipeline._doc_id
        if doc_id is None:
            pytest.skip("前置测试未生成 doc_id（upload 步骤可能失败）")

        r = client.post(
            f"/llm-wiki/recompile/{doc_id}",
            params={"force": True},
        )
        assert r.status_code == 200, f"编译失败: {r.text}"

        data = r.json()
        assert data.get("doc_id") == doc_id, f"doc_id 不匹配: {data}"

        # 编译应产生至少一个 wiki 页面
        total_pages = (
            data.get("pages_created", 0)
            + data.get("pages_updated", 0)
            + data.get("pages_unchanged", 0)
        )
        assert total_pages >= 1, (
            f"编译未产生 wiki 页面: created={data.get('pages_created')}, "
            f"updated={data.get('pages_updated')}, "
            f"unchanged={data.get('pages_unchanged')}"
        )

        # 验证返回了 slugs 列表
        assert "slugs" in data, f"响应缺少 slugs: {data}"
        assert isinstance(data["slugs"], list), f"slugs 不是列表: {type(data['slugs'])}"

        # 验证 index 是否重建
        assert "index_rebuilt" in data, f"响应缺少 index_rebuilt: {data}"

        # 验证错误列表
        assert isinstance(data.get("errors", []), list), (
            f"errors 不是列表: {type(data.get('errors'))}"
        )

        TestE2EWikiPipeline._compiled = True

    # ── 3. wiki 问答 ──

    @requires_llm
    def test_query_wiki(self) -> None:
        """基于 wiki 的知识问答

        POST /llm-wiki/query
        """
        r = client.post(
            "/llm-wiki/query",
            json={
                "question": "MySQL 主从同步延迟怎么排查？",
                "recall_limit": 5,
                "expand_backlinks": True,
            },
        )
        assert r.status_code == 200, f"查询失败: {r.text}"

        data = r.json()

        # 必须包含 answer 字段
        assert "answer" in data, f"响应缺少 answer: {data}"
        assert isinstance(data["answer"], str), (
            f"answer 不是字符串: {type(data['answer'])}"
        )

        # 必须包含 cited_slugs 字段
        assert "cited_slugs" in data, f"响应缺少 cited_slugs: {data}"
        assert isinstance(data["cited_slugs"], list), (
            f"cited_slugs 不是列表: {type(data['cited_slugs'])}"
        )

        # 必须包含 question 字段
        assert data.get("question") is not None, f"响应缺少 question: {data}"

        # 必须包含 recalled_pages 字段
        assert "recalled_pages" in data, f"响应缺少 recalled_pages: {data}"
        assert isinstance(data["recalled_pages"], list), (
            f"recalled_pages 不是列表: {type(data['recalled_pages'])}"
        )

        # 必须包含 insufficient_knowledge 字段
        assert "insufficient_knowledge" in data, (
            f"响应缺少 insufficient_knowledge: {data}"
        )

        # 如果有 wiki 页面已编译，answer 不应为空
        if TestE2EWikiPipeline._compiled:
            assert len(data["answer"]) > 0, "已编译 wiki 但 answer 为空"

    # ── 4. wiki 页面列表 ──

    def test_list_wiki_pages(self) -> None:
        """验证 wiki 页面列表 API 可用

        GET /llm-wiki/pages
        """
        r = client.get("/llm-wiki/pages")
        assert r.status_code == 200, f"获取页面列表失败: {r.text}"

        data = r.json()

        # 必须包含 count 字段
        assert "count" in data, f"响应缺少 count: {data}"
        assert isinstance(data["count"], int), f"count 不是整数: {type(data['count'])}"

        # 必须包含 by_type 字段
        assert "by_type" in data, f"响应缺少 by_type: {data}"
        assert isinstance(data["by_type"], dict), (
            f"by_type 不是字典: {type(data['by_type'])}"
        )

        # 必须包含 pages 字段
        assert "pages" in data, f"响应缺少 pages: {data}"
        assert isinstance(data["pages"], list), (
            f"pages 不是列表: {type(data['pages'])}"
        )

        # 如果有页面，验证页面结构
        for page in data["pages"]:
            assert "slug" in page, f"页面缺少 slug: {page}"
            assert "title" in page, f"页面缺少 title: {page}"
            assert "type" in page, f"页面缺少 type: {page}"

    # ── 5. wiki 健康检查（lint） ──

    def test_lint_wiki(self) -> None:
        """运行 wiki 健康检查

        POST /llm-wiki/lint?include_stale=true
        """
        r = client.post(
            "/llm-wiki/lint",
            params={"include_stale": True},
        )
        assert r.status_code == 200, f"lint 失败: {r.text}"

        data = r.json()

        # 必须包含 pages_checked
        assert "pages_checked" in data, f"响应缺少 pages_checked: {data}"
        assert isinstance(data["pages_checked"], int), (
            f"pages_checked 不是整数: {type(data['pages_checked'])}"
        )

        # 必须包含 total_issues
        assert "total_issues" in data, f"响应缺少 total_issues: {data}"
        assert isinstance(data["total_issues"], int), (
            f"total_issues 不是整数: {type(data['total_issues'])}"
        )

        # 必须包含 by_type
        assert "by_type" in data, f"响应缺少 by_type: {data}"
        assert isinstance(data["by_type"], dict), (
            f"by_type 不是字典: {type(data['by_type'])}"
        )

        # 必须包含 by_severity
        assert "by_severity" in data, f"响应缺少 by_severity: {data}"
        assert isinstance(data["by_severity"], dict), (
            f"by_severity 不是字典: {type(data['by_severity'])}"
        )

        # 必须包含 issues 列表
        assert "issues" in data, f"响应缺少 issues: {data}"
        assert isinstance(data["issues"], list), (
            f"issues 不是列表: {type(data['issues'])}"
        )

        # 验证 issues 中每个条目的结构
        for issue in data["issues"]:
            assert "type" in issue, f"issue 缺少 type: {issue}"
            assert "severity" in issue, f"issue 缺少 severity: {issue}"
            assert "slug" in issue or "message" in issue, (
                f"issue 缺少 slug/message: {issue}"
            )

        # 验证 total_issues 与 by_type 汇总一致
        by_type_sum = sum(data["by_type"].values())
        assert by_type_sum == data["total_issues"], (
            f"by_type 汇总 ({by_type_sum}) 与 total_issues ({data['total_issues']}) 不一致"
        )
