"""从 Compiled Section 提取知识图谱实体

这是知识图谱抽取的新入口，替代旧的 rule_based + LLM few-shot 抽取。

流程:
1. 确定性解析: 从 Compiled Section .md 的 HTML 注释标签中提取实体和关系
2. LLM 消歧: 将新实体与已有图谱实体合并消歧
3. 写入 GraphStore

不再需要正则匹配 IP/端口/主机名等，这些信息在 LLM 编译阶段已标注。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.sections.compiler import CompiledSection


@dataclass
class ExtractedEntity:
    """提取的实体"""
    name: str
    slug: str
    entity_type: str       # Concept/Incident/Symptom/Cause/Solution/Procedure/Parameter/Command
    definition: str
    source_section_id: str
    source_doc_id: str
    confidence: float = 0.9  # 从编译产物提取，置信度较高
    properties: dict = field(default_factory=dict)
    evidence_span: str = ""  # 兼容旧 API


@dataclass
class ExtractedRelation:
    """提取的关系"""
    from_slug: str
    to_slug: str
    relation_type: str     # CAUSES/MANIFESTS_AS/RESOLVED_BY/HAS_STEP/CONFIGURES/RELATES_TO
    source_section_id: str
    source_doc_id: str
    confidence: float = 0.9

    @property
    def from_entity(self) -> str:
        """兼容旧 API: from_entity = from_slug"""
        return self.from_slug

    @property
    def to_entity(self) -> str:
        """兼容旧 API: to_entity = to_slug"""
        return self.to_slug


@dataclass
class CompiledExtractionResult:
    """编译提取结果"""
    entities: list[ExtractedEntity]
    relations: list[ExtractedRelation]
    stats: dict = field(default_factory=dict)


class CompiledKnowledgeExtractor:
    """从 Compiled Section 提取知识图谱实体

    确定性解析 + LLM 消歧两步走。
    """

    # 实体类型映射（编译产物中的 type → 标准 entity_type）
    ENTITY_TYPE_MAP = {
        'Concept': 'Concept',
        'Incident': 'Incident',
        'Symptom': 'Symptom',
        'Cause': 'Cause',
        'Solution': 'Solution',
        'Procedure': 'Procedure',
        'Parameter': 'Parameter',
        'Command': 'Command',
    }

    # 关系类型映射
    RELATION_TYPE_MAP = {
        'CAUSES': 'CAUSES',
        'MANIFESTS_AS': 'MANIFESTS_AS',
        'RESOLVED_BY': 'RESOLVED_BY',
        'HAS_STEP': 'HAS_STEP',
        'CONFIGURES': 'CONFIGURES',
        'RELATES_TO': 'RELATES_TO',
    }

    def __init__(self, llm_call: Any | None = None):
        self._llm_call = llm_call

    def extract_from_document(self, doc: Any) -> CompiledExtractionResult:
        """从 ParsedDocument 提取实体和关系（兜底方案）

        当 LLM 抽取失败时，从文档标题树和元素中提取实体。
        不依赖正则，基于文档结构（标题层级、代码块、表格）。

        Args:
            doc: ParsedDocument 实例

        Returns:
            CompiledExtractionResult
        """
        entities: list[ExtractedEntity] = []
        relations: list[ExtractedRelation] = []

        # 从标题树提取实体
        heading_tree = getattr(doc, 'heading_tree', []) or []
        for node_dict in heading_tree if isinstance(heading_tree, list) else []:
            title = node_dict.get('title', '') if isinstance(node_dict, dict) else getattr(node_dict, 'title', '')
            if title:
                slug = self._slugify(title)
                entities.append(ExtractedEntity(
                    name=title,
                    slug=slug,
                    entity_type='Concept',
                    definition=f'文档章节: {title}',
                    source_section_id='',
                    source_doc_id=getattr(doc, 'doc_id', ''),
                    confidence=0.5,
                ))

        # 从元素提取关键词实体
        elements = getattr(doc, 'elements', []) or []
        code_blocks = []
        for elem in elements:
            if isinstance(elem, dict):
                if elem.get('type') == 'code':
                    code_blocks.append(elem.get('content', ''))
                elif elem.get('type') == 'table':
                    entities.append(ExtractedEntity(
                        name=f'配置表: {elem.get("content", "")[:50]}',
                        slug=self._slugify(f'table-{elem.get("content", "")[:30]}'),
                        entity_type='Parameter',
                        definition='文档中的配置/参数表格',
                        source_section_id='',
                        source_doc_id=getattr(doc, 'doc_id', ''),
                        confidence=0.4,
                    ))

        # 从代码块提取命令
        for code in code_blocks:
            for line in code.split('\n'):
                line = line.strip()
                if line.startswith('#') or line.startswith('//') or line.startswith('>'):
                    continue
                if any(cmd in line for cmd in ['systemctl', 'curl', 'kubectl', 'docker', 'psql', 'redis-cli', 'nginx']):
                    entities.append(ExtractedEntity(
                        name=line[:80],
                        slug=self._slugify(f'cmd-{line[:30]}'),
                        entity_type='Command',
                        definition=f'操作命令: {line[:60]}',
                        source_section_id='',
                        source_doc_id=getattr(doc, 'doc_id', ''),
                        confidence=0.5,
                    ))

        return CompiledExtractionResult(
            entities=entities,
            relations=relations,
            stats={
                'entity_count': len(entities),
                'relation_count': len(relations),
                'by_type': self._count_by_type(entities),
                'source': 'document_fallback',
            },
        )

    def extract_from_section(
        self, compiled: CompiledSection,
    ) -> CompiledExtractionResult:
        """从单个编译章节提取实体和关系

        Args:
            compiled: 编译后的章节

        Returns:
            CompiledExtractionResult
        """
        entities = self._extract_entities(compiled)
        relations = self._extract_relations(compiled)

        return CompiledExtractionResult(
            entities=entities,
            relations=relations,
            stats={
                'entity_count': len(entities),
                'relation_count': len(relations),
                'by_type': self._count_by_type(entities),
                'by_relation': self._count_by_relation(relations),
            },
        )

    def extract_from_sections(
        self, compiled_sections: list[CompiledSection],
    ) -> CompiledExtractionResult:
        """从多个编译章节批量提取"""
        all_entities: list[ExtractedEntity] = []
        all_relations: list[ExtractedRelation] = []
        seen_entity_slugs: set[str] = set()

        for cs in compiled_sections:
            result = self.extract_from_section(cs)
            # 去重实体（按 slug）
            for entity in result.entities:
                if entity.slug not in seen_entity_slugs:
                    seen_entity_slugs.add(entity.slug)
                    all_entities.append(entity)
            all_relations.extend(result.relations)

        return CompiledExtractionResult(
            entities=all_entities,
            relations=all_relations,
            stats={
                'entity_count': len(all_entities),
                'relation_count': len(all_relations),
                'by_type': self._count_by_type(all_entities),
                'by_relation': self._count_by_relation(all_relations),
                'section_count': len(compiled_sections),
            },
        )

    async def resolve_entities(
        self,
        new_entities: list[ExtractedEntity],
        existing_entities: list[dict],
    ) -> list[dict]:
        """LLM 实体消歧：将新实体与已有实体合并

        Args:
            new_entities: 新提取的实体
            existing_entities: 已有图谱中的实体 [{slug, name, definition, ...}]

        Returns:
            [{action: 'merge'|'create'|'conflict', entity, target_slug, reason}]
        """
        if not self._llm_call:
            # 无 LLM 时，按名称简单匹配
            return self._simple_resolve(new_entities, existing_entities)

        # LLM 消歧
        prompt = self._build_resolve_prompt(new_entities, existing_entities)
        try:
            response = await self._llm_call(prompt)
            return self._parse_resolve_response(response, new_entities)
        except Exception:
            return self._simple_resolve(new_entities, existing_entities)

    def _extract_entities(self, compiled: CompiledSection) -> list[ExtractedEntity]:
        """从编译章节提取实体"""
        entities: list[ExtractedEntity] = []
        for ent in compiled.entities:
            if not isinstance(ent, dict):
                continue
            name = ent.get('name', '')
            if not name:
                continue
            entity_type = self.ENTITY_TYPE_MAP.get(
                ent.get('type', 'Concept'), 'Concept',
            )
            entities.append(ExtractedEntity(
                name=name,
                slug=ent.get('slug', self._slugify(name)),
                entity_type=entity_type,
                definition=ent.get('definition', ''),
                source_section_id=compiled.section_id,
                source_doc_id=compiled.source_doc_id,
                confidence=ent.get('confidence', 0.9),
            ))
        return entities

    def _extract_relations(self, compiled: CompiledSection) -> list[ExtractedRelation]:
        """从编译章节提取关系"""
        relations: list[ExtractedRelation] = []
        for rel in compiled.relations:
            if not isinstance(rel, dict):
                continue
            relation_type = self.RELATION_TYPE_MAP.get(
                rel.get('type', 'RELATES_TO'), 'RELATES_TO',
            )
            relations.append(ExtractedRelation(
                from_slug=rel.get('from', ''),
                to_slug=rel.get('to', ''),
                relation_type=relation_type,
                source_section_id=compiled.section_id,
                source_doc_id=compiled.source_doc_id,
                confidence=rel.get('confidence', 0.9),
            ))
        return relations

    def _simple_resolve(
        self,
        new_entities: list[ExtractedEntity],
        existing_entities: list[dict],
    ) -> list[dict]:
        """简单实体消歧（按名称匹配）"""
        existing_map: dict[str, dict] = {}
        for e in existing_entities:
            name = e.get('name', '')
            slug = e.get('slug', '')
            if name:
                existing_map[name.lower()] = e
            if slug:
                existing_map[slug.lower()] = e

        results = []
        for entity in new_entities:
            key = entity.name.lower()
            slug_key = entity.slug.lower()
            if key in existing_map or slug_key in existing_map:
                existing = existing_map.get(key) or existing_map.get(slug_key)
                results.append({
                    'action': 'merge',
                    'entity': entity,
                    'target_slug': existing.get('slug', entity.slug),
                    'reason': '名称匹配',
                })
            else:
                results.append({
                    'action': 'create',
                    'entity': entity,
                    'target_slug': entity.slug,
                    'reason': '新实体',
                })
        return results

    def _build_resolve_prompt(
        self,
        new_entities: list[ExtractedEntity],
        existing_entities: list[dict],
    ) -> str:
        new_json = [
            {
                'name': e.name,
                'slug': e.slug,
                'type': e.entity_type,
                'definition': e.definition,
            }
            for e in new_entities
        ]
        existing_json = [
            {'name': e.get('name', ''), 'slug': e.get('slug', ''), 'definition': e.get('definition', '')}
            for e in existing_entities
        ]
        return f"""你是一个运维知识图谱的实体消歧专家。

## 已有实体
{existing_json}

## 新提取的实体
{new_json}

## 任务
对每个新实体，判断它是否与已有实体相同（同义概念）。

判断规则:
1. 名称相同或高度相似 → 合并
2. 描述/定义指向同一概念 → 合并
3. 名称不同且描述不同 → 新建
4. 名称相同但描述矛盾 → 标记冲突

## 输出格式
JSON 数组:
[{{"new_entity": "新实体名称", "action": "merge|create|conflict", "target_entity": "合并到的已有实体名称", "reason": "判断理由"}}]"""

    def _parse_resolve_response(
        self, response: str, new_entities: list[ExtractedEntity],
    ) -> list[dict]:
        import json as _json
        try:
            # 提取 JSON 部分
            response = response.strip()
            if '```' in response:
                response = re.sub(r'```\w*\n?', '', response)
            resolved = _json.loads(response)
            return [
                {
                    'action': r.get('action', 'create'),
                    'entity': next(
                        (e for e in new_entities if e.name == r.get('new_entity', '')),
                        None,
                    ),
                    'target_slug': r.get('target_entity', ''),
                    'reason': r.get('reason', ''),
                }
                for r in resolved
            ]
        except Exception:
            return self._simple_resolve(new_entities, [])

    @staticmethod
    def _slugify(name: str) -> str:
        """简单 slug 化"""
        slug = re.sub(r'[^\w\s-]', '', name.lower())
        slug = re.sub(r'[-\s]+', '-', slug)
        return slug.strip('-')

    @staticmethod
    def _count_by_type(entities: list[ExtractedEntity]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in entities:
            counts[e.entity_type] = counts.get(e.entity_type, 0) + 1
        return counts

    @staticmethod
    def _count_by_relation(relations: list[ExtractedRelation]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in relations:
            counts[r.relation_type] = counts.get(r.relation_type, 0) + 1
        return counts
