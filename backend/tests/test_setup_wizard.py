"""Setup Wizard API 测试

覆盖 /setup/status、/setup/test-llm、/setup/test-neo4j、/setup/generate-command
"""
from __future__ import annotations

import os

os.environ.setdefault("OPSKG_API_TOKEN", "")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ────────── /setup/status ──────────


class TestSetupStatus:
    def test_status_returns_200(self):
        r = client.get("/setup/status")
        assert r.status_code == 200
        body = r.json()
        # 必含字段
        for key in (
            "llm_backend",
            "llm_configured",
            "llm_backend_options",
            "neo4j_uri",
            "neo4j_configured",
            "auth_enabled",
            "bootstrap_admin_configured",
            "tracing_enabled",
            "ready",
            "missing",
        ):
            assert key in body, f"missing field: {key}"

    def test_llm_backend_options_complete(self):
        body = client.get("/setup/status").json()
        assert set(body["llm_backend_options"]) == {"openai_compat", "ollama", "vllm"}

    def test_missing_is_list(self):
        body = client.get("/setup/status").json()
        assert isinstance(body["missing"], list)

    def test_ready_is_bool(self):
        body = client.get("/setup/status").json()
        assert isinstance(body["ready"], bool)

    def test_does_not_leak_api_key(self):
        """敏感值不应出现在响应中"""
        body_text = client.get("/setup/status").text
        assert "api_key" not in body_text.lower()
        assert "sk-" not in body_text


# ────────── /setup/test-llm ──────────


class TestTestLLM:
    def test_no_api_key_returns_error(self):
        """openai_compat 无 key 应返回 ok=False 且 error 提示"""
        r = client.post(
            "/setup/test-llm",
            json={"backend": "openai_compat", "api_key": ""},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "key" in body["error"].lower() or "未配置" in body["error"]

    def test_invalid_backend_returns_error(self):
        """未知 backend 应返回 ok=False"""
        # Literal 类型会校验，FastAPI 返回 422
        r = client.post(
            "/setup/test-llm",
            json={"backend": "invalid_backend"},
        )
        assert r.status_code == 422

    def test_unknown_host_returns_connect_error(self):
        """指向不存在的主机应返回 ok=False 且带 error"""
        r = client.post(
            "/setup/test-llm",
            json={
                "backend": "openai_compat",
                "base_url": "http://nonexistent-host-12345:9999/v1",
                "api_key": "sk-test",
                "model": "test-model",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error"] is not None


# ────────── /setup/test-neo4j ──────────


class TestTestNeo4j:
    def test_unreachable_neo4j_returns_error(self):
        """指向不存在的 Neo4j 应返回 ok=False"""
        r = client.post(
            "/setup/test-neo4j",
            json={
                "uri": "bolt://nonexistent-host-12345:7687",
                "user": "neo4j",
                "password": "test",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error"] is not None


# ────────── /setup/generate-command ──────────


class TestGenerateCommand:
    def test_docker_compose_mode(self):
        r = client.post(
            "/setup/generate-command",
            json={
                "mode": "docker-compose",
                "llm_backend": "openai_compat",
                "openai_compat_api_key": "sk-test-key",
                "neo4j_password": "mysecret",
                "port": 8080,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "docker compose up -d" in body["command"]
        assert "sk-test-key" in body["env_file_content"]
        assert "mysecret" in body["env_file_content"]
        assert "OPENAI_COMPAT_API_KEY=sk-test-key" in body["env_file_content"]

    def test_docker_run_mode(self):
        r = client.post(
            "/setup/generate-command",
            json={
                "mode": "docker-run",
                "llm_backend": "ollama",
                "ollama_base_url": "http://localhost:11434",
                "ollama_model": "qwen2.5:7b",
                "neo4j_password": "mypass",
                "port": 9090,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "docker run -d --name opskg" in body["command"]
        assert "OLLAMA_BASE_URL=http://localhost:11434" in body["command"]
        assert "OLLAMA_MODEL=qwen2.5:7b" in body["command"]
        assert "-p 9090:80" in body["command"]
        # --add-host 让容器内 host.docker.internal 可解析（访问宿主 Ollama/vLLM）
        assert "--add-host host.docker.internal:host-gateway" in body["command"]
        # docker run 场景用 --link 连接 Neo4j 容器，URI 应为 bolt://neo4j:7687
        assert "NEO4J_URI=bolt://neo4j:7687" in body["command"]

    def test_ollama_default_uses_host_docker_internal(self):
        """Docker 部署默认 ollama_base_url 应为 host.docker.internal（非 localhost）

        容器内 localhost 访问不到宿主 Ollama，必须用 host.docker.internal。
        """
        r = client.post(
            "/setup/generate-command",
            json={
                "mode": "docker-compose",
                "llm_backend": "ollama",
            },
        )
        body = r.json()
        # 默认值应包含 host.docker.internal
        assert "OLLAMA_BASE_URL=http://host.docker.internal:11434" in body["env_file_content"]

    def test_auth_enabled_includes_token(self):
        r = client.post(
            "/setup/generate-command",
            json={
                "mode": "docker-compose",
                "llm_backend": "openai_compat",
                "openai_compat_api_key": "sk-x",
                "enable_auth": True,
                "api_token": "my-secret-token",
            },
        )
        body = r.json()
        assert "OPSKG_API_TOKEN=my-secret-token" in body["env_file_content"]

    def test_auth_disabled_empty_token(self):
        r = client.post(
            "/setup/generate-command",
            json={
                "mode": "docker-compose",
                "llm_backend": "openai_compat",
                "openai_compat_api_key": "sk-x",
                "enable_auth": False,
                "api_token": "",
            },
        )
        body = r.json()
        assert "OPSKG_API_TOKEN=" in body["env_file_content"]
        # 确保不是 token=某值
        import re

        assert not re.search(r"OPSKG_API_TOKEN=\S", body["env_file_content"])

    def test_vllm_backend(self):
        r = client.post(
            "/setup/generate-command",
            json={
                "mode": "docker-compose",
                "llm_backend": "vllm",
                "vllm_base_url": "http://gpu-host:8000",
                "vllm_model": "Qwen2.5-14B",
            },
        )
        body = r.json()
        assert "VLLM_BASE_URL=http://gpu-host:8000" in body["env_file_content"]
        assert "VLLM_MODEL=Qwen2.5-14B" in body["env_file_content"]

    def test_invalid_port_returns_422(self):
        r = client.post(
            "/setup/generate-command",
            json={"port": 99999},
        )
        assert r.status_code == 422
