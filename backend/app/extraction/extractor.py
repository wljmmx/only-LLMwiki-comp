"""知识抽取引擎（F2 / V2.1 §7）

LLM Few-shot 抽取实体/关系 → 置信度门控分流（自动入图 / 建议审查 / 丢弃）。
"""

from __future__ import annotations

import json

import structlog

from app.config import get_settings
from app.core.llm import ChatMessage, get_llm_client
from app.extraction.rule_extractor import RuleBasedExtractor
from app.extraction.types import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
    ExtractionStats,
)
from app.parsers.base import ParsedDocument

logger = structlog.get_logger()

# Few-shot 提示（运维领域）
EXTRACTION_SYSTEM_PROMPT = """你是一个运维知识抽取专家。从给定的文档片段中抽取实体和关系。

## 实体类型（必须严格使用以下标签）
- Host: 主机/服务器（hostname, ip, os, role, env）
- Service: 业务服务（service_id, name, owner_team, tier）
- Component: 中间件/组件（component_type, version）
- Parameter: 配置参数（key, value, default_value, scope）
- Command: 命令/脚本（cmd, shell, risk_level）
- Procedure: 操作步骤/SOP（title, steps）
- Incident: 故障案例（title, severity, root_cause, resolution）
- Symptom: 故障现象（description, category）
- Concept: 概念/术语（term, definition）

## 关系类型
- RUNS_ON(Service→Host): 服务运行于
- USES(Service→Component): 服务使用组件
- DEPENDS_ON(Service→Service): 服务依赖
- HAS_PARAMETER(Component→Parameter): 组件拥有参数
- INVOLVES(Incident→[Host|Service|Component]): 故障涉及
- MANIFESTS_AS(Incident→Symptom): 故障表现为
- RESOLVED_BY(Incident→Procedure): 通过步骤解决

## 输出格式（严格 JSON 数组）
[
  {"entity_type": "Service", "name": "auth-service", "properties": {"tier": "t1"},
   "confidence": 0.92, "evidence_span": "原文证据"},
  {"relation_type": "RUNS_ON", "from_entity": "auth-service", "to_entity": "web-01",
   "confidence": 0.88, "evidence_span": "原文证据"}
]

## 规则
1. 每个实体必须给出 0-1 置信度，基于证据充分性
2. 仅抽取文档中明确提到的实体，不要编造
3. 关系必须同时有 from 和 to 的实体证据
4. 如果文档没有可抽取的实体，返回空数组 []"""


class KnowledgeExtractor:
    """知识抽取引擎"""

    def __init__(self) -> None:
        self.llm = get_llm_client()
        self.settings = get_settings()
        self.rule_extractor = RuleBasedExtractor()

    async def extract(self, doc: ParsedDocument) -> ExtractionResult:
        """从 ParsedDocument 抽取知识"""
        result = ExtractionResult(doc_id=doc.doc_id)

        if not doc.elements:
            return result

        # 组装上下文（取前 20 个元素，限制 token）
        context = self._build_context(doc)
        if not context.strip():
            return result

        # 调用 LLM 抽取
        raw_entities, raw_relations = await self._call_llm(context)

        # 转换为内部类型
        entities = [self._parse_entity(e, doc.doc_id) for e in raw_entities]
        relations = [self._parse_relation(r, doc.doc_id) for r in raw_relations]

        # LLM 抽取为空时启用规则化兜底
        if not entities and not relations:
            logger.info("extraction_fallback_to_rules", doc_id=doc.doc_id)
            entities, relations = self.rule_extractor.extract(doc)

        # 置信度门控
        self._apply_gating(entities, relations, result)

        logger.info(
            "extraction_done",
            doc_id=doc.doc_id,
            total=len(entities),
            auto=len(result.auto_accepted_entities),
            review=len(result.review_entities),
            discarded=result.discarded_count,
            source="llm" if raw_entities or raw_relations else "rules",
        )
        return result

    def _build_context(self, doc: ParsedDocument) -> str:
        """构建 LLM 上下文（P0-2: 提升截断限制，避免大部分内容丢失）

        原限制：20 元素 × 500 字符 = 最多 10,000 字符
        新限制：100 元素 × 2000 字符 = 最多 200,000 字符
        使用 token 估算动态截断，确保不超过模型上下文窗口
        """
        lines = [f"# {doc.title or doc.doc_id}", ""]
        # 估算 token 数（中文约 1.5 字符/token，英文约 4 字符/token，取保守值 2 字符/token）
        max_tokens = self.settings.llm_max_tokens
        max_chars = max_tokens * 2  # 保守估算：总字符数不超过 max_tokens * 2

        total_chars = 0
        # P0-2: 元素限制从 20 提升到 100
        max_elements = min(len(doc.elements), 100)
        for e in doc.elements[:max_elements]:
            label = f"[{e.type.value}]"
            if e.section:
                label += f"（章节：{e.section}）"
            # P0-2: 内容截断从 500 提升到 2000
            content = e.content[:2000]
            line = f"{label} {content}"
            total_chars += len(line) + 1  # +1 for newline
            if total_chars > max_chars:
                lines.append(f"[截断] 上下文已达 {max_tokens} tokens 限制，省略后续元素")
                break
            lines.append(line)
            lines.append("")
        return "\n".join(lines)

    async def _call_llm(self, context: str) -> tuple[list[dict], list[dict]]:
        """调用 LLM 抽取，返回 (entities, relations)"""
        try:
            resp = await self.llm.chat(
                messages=[
                    ChatMessage(role="system", content=EXTRACTION_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=context),
                ],
                temperature=0.1,
                max_tokens=self.settings.llm_max_tokens,
            )
            # 解析 JSON
            data = self._parse_json(resp.text)
            entities = [d for d in data if "entity_type" in d]
            relations = [d for d in data if "relation_type" in d]
            return entities, relations
        except Exception as e:
            logger.error("extraction_llm_failed", error=str(e))
            return [], []

    def _parse_json(self, text: str) -> list[dict]:
        """从 LLM 输出中提取 JSON 数组"""
        text = text.strip()
        # 去掉 markdown 代码块包裹
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            return []
        except json.JSONDecodeError:
            # 尝试提取 [...] 片段
            import re

            m = re.search(r"\[.*\]", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            return []

    def _parse_entity(self, raw: dict, doc_id: str) -> ExtractedEntity:
        return ExtractedEntity(
            entity_type=raw.get("entity_type", "Concept"),
            name=raw.get("name", raw.get("term", "")),
            properties=raw.get("properties", {}),
            confidence=float(raw.get("confidence", 0.5)),
            evidence_span=raw.get("evidence_span", ""),
            source_doc_id=doc_id,
        )

    def _parse_relation(self, raw: dict, doc_id: str) -> ExtractedRelation:
        return ExtractedRelation(
            relation_type=raw.get("relation_type", "RELATED_TO"),
            from_entity=raw.get("from_entity", ""),
            to_entity=raw.get("to_entity", ""),
            properties=raw.get("properties", {}),
            confidence=float(raw.get("confidence", 0.5)),
            evidence_span=raw.get("evidence_span", ""),
            source_doc_id=doc_id,
        )

    def _apply_gating(
        self,
        entities: list[ExtractedEntity],
        relations: list[ExtractedRelation],
        result: ExtractionResult,
    ) -> None:
        """置信度门控（V2.1 §7.4）"""
        ca = self.settings.confidence_auto
        cr = self.settings.confidence_review

        result.entities = entities
        result.relations = relations

        for e in entities:
            if e.confidence >= ca:
                result.auto_accepted_entities.append(e)
            elif e.confidence >= cr:
                result.review_entities.append(e)
            else:
                result.discarded_count += 1

        for r in relations:
            if r.confidence >= ca:
                result.auto_accepted_relations.append(r)
            elif r.confidence >= cr:
                result.review_relations.append(r)
            else:
                result.discarded_count += 1

    def get_stats(self, result: ExtractionResult) -> ExtractionStats:
        """生成抽取统计"""
        entities = result.entities
        confidences = [e.confidence for e in entities]
        return ExtractionStats(
            total_entities=len(entities),
            auto_accepted=len(result.auto_accepted_entities),
            review_needed=len(result.review_entities),
            discarded=result.discarded_count,
            confidence_avg=sum(confidences) / len(confidences) if confidences else 0.0,
        )
