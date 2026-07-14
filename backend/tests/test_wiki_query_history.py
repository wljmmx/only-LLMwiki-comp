"""P2-13b: Wiki QA 多轮会话历史 — 后端纯函数单元测试

覆盖 `_sanitize_history`（清洗 + 截断）与 `_build_llm_messages`（消息顺序），
不依赖 LLM / DB，验证多轮上下文注入逻辑。
"""
from __future__ import annotations

from app.core.llm import ChatMessage
from app.knowledge.wiki_query import (
    MAX_HISTORY_MESSAGES,
    _build_llm_messages,
    _sanitize_history,
)


class TestSanitizeHistory:
    def test_none_returns_empty(self) -> None:
        assert _sanitize_history(None) == []

    def test_empty_returns_empty(self) -> None:
        assert _sanitize_history([]) == []

    def test_keeps_valid_user_assistant_pairs(self) -> None:
        history = [
            {"role": "user", "content": "什么是 nginx"},
            {"role": "assistant", "content": "Nginx 是反向代理"},
        ]
        cleaned = _sanitize_history(history)
        assert cleaned == history

    def test_filters_invalid_roles(self) -> None:
        history = [
            {"role": "system", "content": "应被过滤"},
            {"role": "user", "content": "保留"},
            {"role": "tool", "content": "应被过滤"},
            {"role": "assistant", "content": "保留"},
        ]
        cleaned = _sanitize_history(history)
        assert len(cleaned) == 2
        assert cleaned[0]["role"] == "user"
        assert cleaned[1]["role"] == "assistant"

    def test_filters_empty_or_whitespace_content(self) -> None:
        history = [
            {"role": "user", "content": ""},
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": "有效回答"},
        ]
        cleaned = _sanitize_history(history)
        assert len(cleaned) == 1
        assert cleaned[0]["content"] == "有效回答"

    def test_filters_non_dict_entries(self) -> None:
        history = [
            "not a dict",
            None,
            {"role": "user", "content": "保留"},
            42,
        ]
        cleaned = _sanitize_history(history)
        assert len(cleaned) == 1
        assert cleaned[0]["content"] == "保留"

    def test_filters_non_string_content(self) -> None:
        history = [
            {"role": "user", "content": 123},
            {"role": "assistant", "content": ["list"]},
            {"role": "user", "content": "保留"},
        ]
        cleaned = _sanitize_history(history)
        assert len(cleaned) == 1
        assert cleaned[0]["content"] == "保留"

    def test_truncates_to_max_recent(self) -> None:
        # 构造超过上限的历史
        history = [
            {"role": "user", "content": f"老问题 {i}"}
            if i % 2 == 0
            else {"role": "assistant", "content": f"老回答 {i}"}
            for i in range(MAX_HISTORY_MESSAGES + 4)
        ]
        cleaned = _sanitize_history(history)
        assert len(cleaned) == MAX_HISTORY_MESSAGES
        # 保留最近 N 条（尾部）
        assert cleaned[-1]["content"] == f"老回答 {MAX_HISTORY_MESSAGES + 3}"
        assert cleaned[0]["content"] == "老问题 4"  # 最早被丢弃的是 0~3


class TestBuildLlmMessages:
    def test_system_first_then_user_when_no_history(self) -> None:
        messages = _build_llm_messages("SYS", "Q1", None)
        assert len(messages) == 2
        assert messages[0] == ChatMessage(role="system", content="SYS")
        assert messages[1] == ChatMessage(role="user", content="Q1")

    def test_empty_history_same_as_none(self) -> None:
        messages = _build_llm_messages("SYS", "Q1", [])
        assert len(messages) == 2

    def test_history_inserted_between_system_and_current_question(self) -> None:
        history = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
        ]
        messages = _build_llm_messages("SYS", "Q2", history)
        assert len(messages) == 4
        # 顺序：system → 历史 user → 历史 assistant → 当前 user
        assert messages[0].role == "system"
        assert messages[0].content == "SYS"
        assert messages[1].role == "user"
        assert messages[1].content == "Q1"
        assert messages[2].role == "assistant"
        assert messages[2].content == "A1"
        assert messages[3].role == "user"
        assert messages[3].content == "Q2"

    def test_preserves_multi_turn_history_order(self) -> None:
        history = [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
            {"role": "assistant", "content": "第二答"},
        ]
        messages = _build_llm_messages("SYS", "第三问", history)
        assert len(messages) == 6
        roles = [m.role for m in messages]
        assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
        assert messages[-1].content == "第三问"
