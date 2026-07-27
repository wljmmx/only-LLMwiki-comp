"""CompiledKnowledgeExtractor 单元测试

验证内容：
1. extract_from_document：从标题树提取 Concept 实体（置信度 0.65，通过 review 门控 0.60）
2. extract_from_document：从代码块提取 Command 实体（置信度 0.65）
3. extract_from_document：从表格元素提取 Parameter 实体（置信度 0.65）
4. extract_from_document：跳过注释行（# // >）
5. extract_from_document：仅匹配已知命令关键字（systemctl/curl/kubectl/docker/...）
6. extract_from_document：空文档返回空结果
7. extract_from_document：兼容 dict 类型的 heading_tree/elements
8. extract_from_document：stats 字段正确反映 entity_count
9. _slugify：基础 slug 化行为
10. _count_by_type：按 entity_type 分组计数
11. extract_from_sections：批量提取 + slug 去重

不依赖 LLM，仅测试确定性 fallback 提取逻辑。
"""
from __future__ import annotations

from app.extraction.compiled_extractor import (
    CompiledExtractionResult,
    CompiledKnowledgeExtractor,
    ExtractedEntity,
)
from app.parsers.base import (
    ElementType,
    HeadingNode,
    ParsedDocument,
    ParsedElement,
)

# Review 门控阈值（与 wiki_compiler 一致，extracted 实体置信度需 ≥ 0.60 才会被接受）
_REVIEW_THRESHOLD = 0.60


def _make_doc(
    *,
    heading_tree: list[HeadingNode] | None = None,
    elements: list[ParsedElement] | None = None,
    doc_id: str = "test-doc-001",
) -> ParsedDocument:
    """构造测试用 ParsedDocument"""
    return ParsedDocument(
        doc_id=doc_id,
        source_path="/tmp/test.md",
        format="markdown",
        checksum="sha256:abc",
        title="Test Document",
        elements=elements or [],
        heading_tree=heading_tree or [],
    )


# ────────── 1. 从标题树提取 Concept 实体 ──────────


def test_extract_from_document_extracts_concept_from_heading_tree() -> None:
    """从 heading_tree 提取 Concept 实体，置信度 0.65 通过 review 门控"""
    extractor = CompiledKnowledgeExtractor()
    doc = _make_doc(
        heading_tree=[
            HeadingNode(level=1, title="Nginx 部署指南"),
            HeadingNode(level=2, title="配置"),
            HeadingNode(level=2, title="故障排查"),
        ]
    )

    result = extractor.extract_from_document(doc)

    assert isinstance(result, CompiledExtractionResult)
    assert len(result.entities) == 3
    types = {e.entity_type for e in result.entities}
    assert types == {"Concept"}

    # 所有实体置信度必须 ≥ 0.60（review 门控）
    for entity in result.entities:
        assert entity.confidence >= _REVIEW_THRESHOLD, (
            f"实体 {entity.name} 置信度 {entity.confidence} 低于 review 门控 {_REVIEW_THRESHOLD}"
        )
        assert entity.source_doc_id == "test-doc-001"


def test_extract_from_document_heading_entity_confidence_is_065() -> None:
    """标题树实体置信度精确为 0.65（高于 review 门控 0.60）"""
    extractor = CompiledKnowledgeExtractor()
    doc = _make_doc(heading_tree=[HeadingNode(level=2, title="Important Section")])

    result = extractor.extract_from_document(doc)
    assert len(result.entities) == 1
    assert result.entities[0].confidence == 0.65


# ────────── 2. 从代码块提取 Command 实体 ──────────


def test_extract_from_document_extracts_commands_from_code_blocks() -> None:
    """从代码块提取已知命令（systemctl/curl/kubectl/docker 等）"""
    extractor = CompiledKnowledgeExtractor()
    code = """
systemctl status nginx
curl -X GET http://localhost:8080/health
kubectl get pods
docker ps -a
psql -c "SELECT 1"
redis-cli ping
nginx -t
"""
    doc = _make_doc(
        elements=[ParsedElement(type=ElementType.CODE, content=code)],
    )

    result = extractor.extract_from_document(doc)

    commands = [e for e in result.entities if e.entity_type == "Command"]
    assert len(commands) == 7
    # 所有命令置信度 ≥ 0.60
    for cmd in commands:
        assert cmd.confidence >= _REVIEW_THRESHOLD
        assert cmd.source_doc_id == "test-doc-001"


def test_extract_from_document_command_confidence_is_065() -> None:
    """Command 实体置信度精确为 0.65"""
    extractor = CompiledKnowledgeExtractor()
    doc = _make_doc(
        elements=[ParsedElement(type=ElementType.CODE, content="systemctl restart nginx")],
    )

    result = extractor.extract_from_document(doc)
    commands = [e for e in result.entities if e.entity_type == "Command"]
    assert len(commands) == 1
    assert commands[0].confidence == 0.65


# ────────── 3. 从表格元素提取 Parameter 实体 ──────────


def test_extract_from_document_extracts_parameter_from_table() -> None:
    """从表格元素提取 Parameter 实体"""
    extractor = CompiledKnowledgeExtractor()
    table_content = "| param | value |\n|-------|-------|\n| port  | 80    |"
    doc = _make_doc(
        elements=[ParsedElement(type=ElementType.TABLE, content=table_content)],
    )

    result = extractor.extract_from_document(doc)
    tables = [e for e in result.entities if e.entity_type == "Parameter"]
    assert len(tables) == 1
    assert tables[0].confidence >= _REVIEW_THRESHOLD
    assert tables[0].confidence == 0.65


# ────────── 4. 跳过注释行 ──────────


def test_extract_from_document_skips_comment_lines() -> None:
    """代码块中的注释行（# // >）不应被提取为命令"""
    extractor = CompiledKnowledgeExtractor()
    code = """
# systemctl status nginx
// curl http://localhost
> docker ps
systemctl restart nginx
"""
    doc = _make_doc(
        elements=[ParsedElement(type=ElementType.CODE, content=code)],
    )

    result = extractor.extract_from_document(doc)
    commands = [e for e in result.entities if e.entity_type == "Command"]
    # 只有一条非注释命令
    assert len(commands) == 1
    assert "systemctl restart nginx" in commands[0].name


# ────────── 5. 仅匹配已知命令关键字 ──────────


def test_extract_from_document_ignores_unknown_commands() -> None:
    """不含已知命令关键字的代码行不被提取"""
    extractor = CompiledKnowledgeExtractor()
    code = """
echo hello
ls -la
cat /etc/hosts
python script.py
"""
    doc = _make_doc(
        elements=[ParsedElement(type=ElementType.CODE, content=code)],
    )

    result = extractor.extract_from_document(doc)
    commands = [e for e in result.entities if e.entity_type == "Command"]
    assert len(commands) == 0


# ────────── 6. 空文档返回空结果 ──────────


def test_extract_from_document_empty_doc_returns_empty_result() -> None:
    """空 ParsedDocument 应返回空结果"""
    extractor = CompiledKnowledgeExtractor()
    doc = _make_doc()

    result = extractor.extract_from_document(doc)
    assert result.entities == []
    assert result.relations == []
    assert result.stats["entity_count"] == 0


# ────────── 7. 兼容 dict 类型的 heading_tree/elements ──────────


def test_extract_from_document_handles_dict_heading_tree() -> None:
    """heading_tree 支持 dict 类型（与对象混合）"""
    extractor = CompiledKnowledgeExtractor()
    # 模拟 dict 形式的 heading_tree（如序列化后还原的数据）
    doc = _make_doc(
        heading_tree=[
            {"title": "Dict Section", "level": 2},  # type: ignore[arg-type]
        ],
    )

    result = extractor.extract_from_document(doc)
    assert len(result.entities) == 1
    assert result.entities[0].name == "Dict Section"


def test_extract_from_document_handles_dict_elements() -> None:
    """elements 支持 dict 类型（type_value + content 字段）"""
    extractor = CompiledKnowledgeExtractor()
    # 通过 setattr 注入 dict 形式的 elements（绕过类型检查）
    # 字段约定：type_value（ElementType.value 字符串）+ content
    doc = _make_doc()
    doc.elements = [
        {"type_value": "code", "content": "systemctl status nginx"},  # type: ignore[list-item]
    ]

    result = extractor.extract_from_document(doc)
    commands = [e for e in result.entities if e.entity_type == "Command"]
    assert len(commands) == 1


# ────────── 8. stats 字段正确 ──────────


def test_extract_from_document_stats_reflect_entity_count() -> None:
    """stats.entity_count 应与 entities 列表长度一致"""
    extractor = CompiledKnowledgeExtractor()
    doc = _make_doc(
        heading_tree=[
            HeadingNode(level=2, title="Section A"),
            HeadingNode(level=2, title="Section B"),
        ],
        elements=[
            ParsedElement(type=ElementType.CODE, content="systemctl restart nginx"),
        ],
    )

    result = extractor.extract_from_document(doc)
    assert result.stats["entity_count"] == len(result.entities)
    assert result.stats["relation_count"] == 0
    assert result.stats["source"] == "document_fallback"
    # by_type 应按 entity_type 计数
    by_type = result.stats["by_type"]
    assert by_type.get("Concept") == 2
    assert by_type.get("Command") == 1


# ────────── 9. _slugify 行为 ──────────


def test_slugify_basic() -> None:
    """_slugify：空格转 -、小写化、移除特殊字符"""
    assert CompiledKnowledgeExtractor._slugify("Nginx 配置指南") == "nginx-配置指南"
    assert CompiledKnowledgeExtractor._slugify("Hello, World!") == "hello-world"
    assert CompiledKnowledgeExtractor._slugify("Multiple   Spaces") == "multiple-spaces"
    assert CompiledKnowledgeExtractor._slugify("--trim--") == "trim"


def test_slugify_empty_string() -> None:
    """_slugify 处理空字符串"""
    assert CompiledKnowledgeExtractor._slugify("") == ""


# ────────── 10. _count_by_type ──────────


def test_count_by_type_groups_by_entity_type() -> None:
    """_count_by_type 按 entity_type 分组计数"""
    entities = [
        ExtractedEntity(
            name="a", slug="a", entity_type="Concept",
            definition="", source_section_id="", source_doc_id="",
        ),
        ExtractedEntity(
            name="b", slug="b", entity_type="Concept",
            definition="", source_section_id="", source_doc_id="",
        ),
        ExtractedEntity(
            name="c", slug="c", entity_type="Command",
            definition="", source_section_id="", source_doc_id="",
        ),
    ]
    counts = CompiledKnowledgeExtractor._count_by_type(entities)
    assert counts == {"Concept": 2, "Command": 1}


def test_count_by_type_empty_list() -> None:
    """_count_by_type 处理空列表"""
    assert CompiledKnowledgeExtractor._count_by_type([]) == {}


# ────────── 11. extract_from_sections 批量提取与去重 ──────────


def test_extract_from_sections_dedupes_by_slug() -> None:
    """extract_from_sections 按 slug 去重"""
    from app.sections.compiler import CompiledSection

    extractor = CompiledKnowledgeExtractor()
    # 构造两个 CompiledSection（去重逻辑只关心 slug 字段）
    section1 = CompiledSection(
        section_id="sec-1",
        source_doc_id="doc-1",
        title="Section 1",
        semantic_role="concept",
        content="",
    )
    section2 = CompiledSection(
        section_id="sec-2",
        source_doc_id="doc-1",
        title="Section 2",
        semantic_role="concept",
        content="",
    )

    # Mock _extract_entities 让两个 section 返回同名 slug 的实体
    original_extract = extractor._extract_entities

    def mock_extract(compiled: CompiledSection) -> list[ExtractedEntity]:
        # 第一个 section 返回 2 个实体，第二个返回 1 个（其中 1 个 slug 与第一个重复）
        if compiled.section_id == "sec-1":
            return [
                ExtractedEntity(
                    name="dup", slug="duplicate-slug", entity_type="Concept",
                    definition="", source_section_id="sec-1", source_doc_id="doc-1",
                ),
                ExtractedEntity(
                    name="uniq1", slug="unique-1", entity_type="Concept",
                    definition="", source_section_id="sec-1", source_doc_id="doc-1",
                ),
            ]
        return [
            ExtractedEntity(
                name="dup again", slug="duplicate-slug", entity_type="Concept",
                definition="", source_section_id="sec-2", source_doc_id="doc-1",
            ),
            ExtractedEntity(
                name="uniq2", slug="unique-2", entity_type="Concept",
                definition="", source_section_id="sec-2", source_doc_id="doc-1",
            ),
        ]

    extractor._extract_entities = mock_extract  # type: ignore[assignment]
    try:
        result = extractor.extract_from_sections([section1, section2])
    finally:
        extractor._extract_entities = original_extract  # type: ignore[assignment]

    # 去重后应保留 3 个实体（duplicate-slug 只算 1 个 + unique-1 + unique-2）
    assert len(result.entities) == 3
    slugs = {e.slug for e in result.entities}
    assert slugs == {"duplicate-slug", "unique-1", "unique-2"}
    assert result.stats["entity_count"] == 3
    assert result.stats["section_count"] == 2


# ────────── 12. 关键回归：实体置信度全部 ≥ 0.60（修复"知识图谱为 0"问题）──────────


def test_extract_from_document_all_entities_pass_review_threshold() -> None:
    """回归测试：所有 fallback 提取的实体置信度必须 ≥ 0.60

    这是修复"知识图谱提取为 0"问题的关键保证：
    - review 门控阈值为 0.60
    - 旧版 fallback 实体置信度为 0.4-0.5，全部被门控丢弃 → 知识图谱为空
    - 修复后置信度提升至 0.65，全部通过门控
    """
    extractor = CompiledKnowledgeExtractor()
    code = "systemctl restart nginx\ndocker pull redis:latest"
    table = "| port | 80 |\n| host | localhost |"
    doc = _make_doc(
        heading_tree=[
            HeadingNode(level=1, title="Main Title"),
            HeadingNode(level=2, title="Configuration"),
        ],
        elements=[
            ParsedElement(type=ElementType.CODE, content=code),
            ParsedElement(type=ElementType.TABLE, content=table),
        ],
    )

    result = extractor.extract_from_document(doc)

    # 至少有 4 个实体（2 heading + 2 command + 1 table）
    assert len(result.entities) >= 4

    # 所有实体置信度 ≥ 0.60（review 门控阈值）
    for entity in result.entities:
        assert entity.confidence >= _REVIEW_THRESHOLD, (
            f"实体 {entity.name} (type={entity.entity_type}) 置信度 "
            f"{entity.confidence} < review 门控 {_REVIEW_THRESHOLD}"
        )
