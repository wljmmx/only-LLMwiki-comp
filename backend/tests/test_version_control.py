"""VersionControl 单元测试

使用 SQLite 内存数据库（:memory:）作为测试后端，不依赖外部服务。
覆盖版本保存、获取、对比、回滚、列表等操作。
"""
from __future__ import annotations

import pytest

from app.storage.version_control import VersionControl


# ────────── 版本保存 ──────────


class TestVersionSave:
    def test_save_first_version(self, temp_db_version_control):
        """保存第一个版本"""
        vc = VersionControl()
        result = vc.save_version(
            doc_key="test-doc",
            title="Test Document",
            content="# Hello World",
        )
        assert result["doc_key"] == "test-doc"
        assert result["version"] == 1
        assert result["title"] == "Test Document"
        assert "checksum" in result
        assert "created_at" in result
        assert "skipped" not in result

    def test_save_multiple_versions(self, temp_db_version_control):
        """保存多个版本，版本号递增"""
        vc = VersionControl()
        v1 = vc.save_version("test-doc", "V1", "Content V1")
        v2 = vc.save_version("test-doc", "V2", "Content V2")
        v3 = vc.save_version("test-doc", "V3", "Content V3")

        assert v1["version"] == 1
        assert v2["version"] == 2
        assert v3["version"] == 3

    def test_save_duplicate_content_skipped(self, temp_db_version_control):
        """相同内容不创建新版本，返回 skipped"""
        vc = VersionControl()
        vc.save_version("test-doc", "V1", "Content V1")
        result = vc.save_version("test-doc", "V1 Again", "Content V1")

        assert result.get("skipped") is True
        assert result["reason"] == "内容无变化"
        # 版本号回退到上一版本
        assert result["version"] == 1

    def test_save_with_author_and_summary(self, temp_db_version_control):
        """保存版本时附带作者和变更摘要"""
        vc = VersionControl()
        vc.save_version(
            doc_key="test-doc",
            title="Test",
            content="Content",
            author="admin",
            change_summary="Initial creation",
        )
        latest = vc.get_latest("test-doc")
        assert latest["author"] == "admin"
        assert latest["change_summary"] == "Initial creation"

    def test_save_different_doc_keys_independent(self, temp_db_version_control):
        """不同 doc_key 的版本号独立"""
        vc = VersionControl()
        vc.save_version("doc-a", "A1", "Content A1")
        vc.save_version("doc-a", "A2", "Content A2")
        vc.save_version("doc-b", "B1", "Content B1")

        assert vc.get_latest("doc-a")["version"] == 2
        assert vc.get_latest("doc-b")["version"] == 1


# ────────── 版本获取 ──────────


class TestVersionGet:
    def test_get_version(self, temp_db_version_control):
        """获取指定版本"""
        vc = VersionControl()
        vc.save_version("test-doc", "V1", "Content V1")
        vc.save_version("test-doc", "V2", "Content V2")

        v1 = vc.get_version("test-doc", 1)
        assert v1 is not None
        assert v1["title"] == "V1"
        assert v1["content"] == "Content V1"

        v2 = vc.get_version("test-doc", 2)
        assert v2["title"] == "V2"
        assert v2["content"] == "Content V2"

    def test_get_version_nonexistent(self, temp_db_version_control):
        """获取不存在的版本返回 None"""
        vc = VersionControl()
        assert vc.get_version("test-doc", 1) is None
        assert vc.get_version("nonexistent", 99) is None

    def test_get_latest(self, temp_db_version_control):
        """获取最新版本"""
        vc = VersionControl()
        vc.save_version("test-doc", "V1", "Content V1")
        vc.save_version("test-doc", "V2", "Content V2")
        vc.save_version("test-doc", "V3", "Content V3")

        latest = vc.get_latest("test-doc")
        assert latest["version"] == 3
        assert latest["title"] == "V3"

    def test_get_latest_nonexistent(self, temp_db_version_control):
        """获取不存在文档的最新版本返回 None"""
        vc = VersionControl()
        assert vc.get_latest("nonexistent") is None

    def test_list_versions(self, temp_db_version_control):
        """列出所有版本（不含内容）"""
        vc = VersionControl()
        vc.save_version("test-doc", "V1", "Content V1")
        vc.save_version("test-doc", "V2", "Content V2", author="admin")
        vc.save_version("test-doc", "V3", "Content V3")

        versions = vc.list_versions("test-doc")
        assert len(versions) == 3
        # 按版本号降序排列
        assert versions[0]["version"] == 3
        assert versions[1]["version"] == 2
        assert versions[2]["version"] == 1
        # 不包含 content 字段
        assert "content" not in versions[0]

    def test_list_versions_empty(self, temp_db_version_control):
        """无版本的文档返回空列表"""
        vc = VersionControl()
        assert vc.list_versions("nonexistent") == []


# ────────── 版本对比 ──────────


class TestVersionDiff:
    def test_diff_two_versions(self, temp_db_version_control):
        """对比两个版本"""
        vc = VersionControl()
        vc.save_version("test-doc", "V1", "line1\nline2\nline3")
        vc.save_version("test-doc", "V2", "line1\nline2 modified\nline3\nline4")

        diff = vc.diff("test-doc", 1, 2)
        assert "error" not in diff
        assert diff["doc_key"] == "test-doc"
        assert diff["v1"] == 1
        assert diff["v2"] == 2
        assert diff["added_lines"] >= 1
        assert diff["removed_lines"] >= 1
        assert diff["diff"]  # diff 文本不为空

    def test_diff_same_version(self, temp_db_version_control):
        """对比相同版本，无差异"""
        vc = VersionControl()
        vc.save_version("test-doc", "V1", "Content")

        diff = vc.diff("test-doc", 1, 1)
        assert "error" not in diff
        assert diff["added_lines"] == 0
        assert diff["removed_lines"] == 0

    def test_diff_nonexistent_version(self, temp_db_version_control):
        """对比不存在的版本返回错误"""
        vc = VersionControl()
        vc.save_version("test-doc", "V1", "Content")

        diff = vc.diff("test-doc", 1, 99)
        assert "error" in diff
        assert diff["error"] == "版本不存在"

    def test_diff_nonexistent_doc(self, temp_db_version_control):
        """对比不存在的文档返回错误"""
        vc = VersionControl()
        diff = vc.diff("nonexistent", 1, 2)
        assert "error" in diff


# ────────── 版本回滚 ──────────


class TestVersionRollback:
    def test_rollback_to_version(self, temp_db_version_control):
        """回滚到指定版本（创建新版本）"""
        vc = VersionControl()
        vc.save_version("test-doc", "V1", "Original Content")
        vc.save_version("test-doc", "V2", "Modified Content")
        vc.save_version("test-doc", "V3", "Broken Content")

        # 回滚到 V1
        result = vc.rollback("test-doc", 1, author="admin")
        assert "error" not in result
        assert result["version"] == 4  # 新版本

        # 新版本内容与 V1 相同
        latest = vc.get_latest("test-doc")
        assert latest["content"] == "Original Content"
        assert latest["title"] == "V1"
        assert "回滚到版本 1" in latest["change_summary"]

    def test_rollback_nonexistent_version(self, temp_db_version_control):
        """回滚到不存在的版本返回错误"""
        vc = VersionControl()
        vc.save_version("test-doc", "V1", "Content")

        result = vc.rollback("test-doc", 99)
        assert "error" in result


# ────────── 版本删除 ──────────


class TestVersionDelete:
    def test_delete_all_versions(self, temp_db_version_control):
        """删除文档的所有版本"""
        vc = VersionControl()
        vc.save_version("test-doc", "V1", "Content V1")
        vc.save_version("test-doc", "V2", "Content V2")
        vc.save_version("test-doc", "V3", "Content V3")

        count = vc.delete_all("test-doc")
        assert count == 3

        # 删除后无法获取
        assert vc.get_latest("test-doc") is None
        assert vc.list_versions("test-doc") == []

    def test_delete_all_nonexistent(self, temp_db_version_control):
        """删除不存在文档的版本返回 0"""
        vc = VersionControl()
        count = vc.delete_all("nonexistent")
        assert count == 0


# ────────── 按前缀列出 ──────────


class TestListByPrefix:
    def test_list_by_prefix(self, temp_db_version_control):
        """按 doc_key 前缀列出最新版本"""
        vc = VersionControl()
        vc.save_version("wiki:nginx", "Nginx", "Content")
        vc.save_version("wiki:nginx", "Nginx V2", "Content V2")
        vc.save_version("wiki:mysql", "MySQL", "Content")
        vc.save_version("other:doc", "Other", "Content")

        results = vc.list_by_prefix("wiki:")
        assert len(results) == 2
        doc_keys = [r["doc_key"] for r in results]
        assert "wiki:nginx" in doc_keys
        assert "wiki:mysql" in doc_keys
        assert "other:doc" not in doc_keys

        # 每个 doc_key 只返回最新版本
        nginx_result = next(r for r in results if r["doc_key"] == "wiki:nginx")
        assert nginx_result["version"] == 2

    def test_list_by_prefix_empty(self, temp_db_version_control):
        """无匹配前缀返回空列表"""
        vc = VersionControl()
        assert vc.list_by_prefix("nonexistent:") == []

    def test_list_by_prefix_order(self, temp_db_version_control):
        """按创建时间降序排列"""
        vc = VersionControl()
        vc.save_version("wiki:doc-a", "A", "Content")
        vc.save_version("wiki:doc-b", "B", "Content")
        vc.save_version("wiki:doc-c", "C", "Content")

        results = vc.list_by_prefix("wiki:")
        # 最新创建的在前
        assert results[0]["doc_key"] == "wiki:doc-c"


# ────────── 完整生命周期 ──────────


class TestVersionLifecycle:
    def test_full_lifecycle(self, temp_db_version_control):
        """完整版本生命周期：save → list → get → diff → rollback → delete"""
        vc = VersionControl()

        # 保存
        v1 = vc.save_version("lifecycle-doc", "V1", "# Content V1\nline A")
        assert v1["version"] == 1

        v2 = vc.save_version("lifecycle-doc", "V2", "# Content V2\nline B")
        assert v2["version"] == 2

        v3 = vc.save_version("lifecycle-doc", "V3", "# Content V3\nline C")
        assert v3["version"] == 3

        # 列出
        versions = vc.list_versions("lifecycle-doc")
        assert len(versions) == 3

        # 获取
        v2_detail = vc.get_version("lifecycle-doc", 2)
        assert v2_detail["title"] == "V2"

        # 对比
        diff = vc.diff("lifecycle-doc", 1, 3)
        assert diff["added_lines"] > 0

        # 回滚
        rollback_result = vc.rollback("lifecycle-doc", 1)
        assert rollback_result["version"] == 4

        # 回滚后内容恢复
        latest = vc.get_latest("lifecycle-doc")
        assert latest["content"] == "# Content V1\nline A"

        # 删除
        count = vc.delete_all("lifecycle-doc")
        assert count == 4
        assert vc.get_latest("lifecycle-doc") is None