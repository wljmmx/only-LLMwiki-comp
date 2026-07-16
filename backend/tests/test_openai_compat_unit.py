"""OpenAI 兼容客户端 — 纯函数单元测试（不依赖真实的 LLM API）"""

from __future__ import annotations

import pytest

try:
    import openai  # noqa: F401 — 仅用于检测 SDK 可用性

    _openai_available = True
except ImportError:
    _openai_available = False

requires_openai = pytest.mark.skipif(
    not _openai_available, reason="openai SDK 未安装"
)


class TestOpenAICompatInit:
    """测试 OpenAICompatClient 初始化与属性"""

    @requires_openai
    def test_init_basic(self) -> None:
        from app.core.llm.openai_compat import OpenAICompatClient

        client = OpenAICompatClient(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4",
            timeout=30,
            default_temperature=0.7,
            default_max_tokens=4096,
        )
        assert client._label == "openai_compat"
        assert client._model == "gpt-4"
        assert client._default_temperature == 0.7
        assert client._default_max_tokens == 4096

    @requires_openai
    def test_init_with_custom_label(self) -> None:
        from app.core.llm.openai_compat import OpenAICompatClient

        client = OpenAICompatClient(
            base_url="http://localhost:8000/v1",
            api_key="sk-local",
            model="llama3",
            timeout=60,
            default_temperature=0.0,
            default_max_tokens=2048,
            label="vllm-local",
        )
        assert client._label == "vllm-local"

    @requires_openai
    def test_init_with_embedding_model(self) -> None:
        from app.core.llm.openai_compat import OpenAICompatClient

        client = OpenAICompatClient(
            base_url="https://api.deepseek.com/v1",
            api_key="sk-deepseek",
            model="deepseek-chat",
            timeout=60,
            default_temperature=0.3,
            default_max_tokens=8192,
            embedding_model="text-embedding-3-small",
        )
        assert client._embedding_model == "text-embedding-3-small"

    @requires_openai
    def test_init_without_embedding_model(self) -> None:
        from app.core.llm.openai_compat import OpenAICompatClient

        client = OpenAICompatClient(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4",
            timeout=30,
            default_temperature=0.7,
            default_max_tokens=4096,
        )
        assert client._embedding_model is None

    @requires_openai
    def test_backend_name(self) -> None:
        from app.core.llm.openai_compat import OpenAICompatClient

        assert OpenAICompatClient.backend_name == "openai_compat"

    @requires_openai
    def test_base_url_passed_to_client(self) -> None:
        from app.core.llm.openai_compat import OpenAICompatClient

        client = OpenAICompatClient(
            base_url="https://api.openai.com/v1/",
            api_key="sk-test",
            model="gpt-4",
            timeout=30,
            default_temperature=0.7,
            default_max_tokens=4096,
        )
        # base_url is passed to AsyncOpenAI, verify client is created
        assert client._client is not None

    @requires_openai
    def test_client_created_with_api_key(self) -> None:
        from app.core.llm.openai_compat import OpenAICompatClient

        client = OpenAICompatClient(
            base_url="https://api.openai.com/v1",
            api_key="",
            model="gpt-4",
            timeout=30,
            default_temperature=0.7,
            default_max_tokens=4096,
        )
        # Empty API key is replaced with "EMPTY"
        assert client._client is not None
