"""知识抽取引擎（F2 / V2.1 §7）

LLM Few-shot 抽取实体/关系 → 置信度门控分流（自动入图 / 建议审查 / 丢弃）。

新增功能：
- 段落级 LLM 归类：为每段内容生成层级标签、摘要、结构化正文
- 支持批量段落处理，保持上下文一致性
"""

from __future__ import annotations

import json

import structlog

from app.config import get_settings
from app.core.llm import ChatMessage, get_llm_client
from app.extraction.compiled_extractor import CompiledKnowledgeExtractor
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

# 段落归类提示
PARAGRAPH_CLASSIFY_PROMPT = """你是一个运维知识文档分析专家。请对以下文档段落进行智能归类和处理。

## 分类体系（层级标签）
### 第一层（领域）
- 网络运维：网络架构、网络设备、网络协议、网络安全
- 系统运维：操作系统、服务器、存储、备份恢复
- 应用运维：应用部署、应用配置、应用监控、应用调优
- 数据库运维：数据库配置、数据库性能、数据库备份、数据库安全
- 云平台：云服务、容器化、Kubernetes、CI/CD

### 第二层（主题）
每个领域下的具体主题，如"网络运维"下的"防火墙配置"、"负载均衡"等

### 第三层（子主题）
主题下的更细粒度分类

## 处理要求
对每个段落，生成以下三个要素：
1. **层级标签**：按上述分类体系生成，格式为"领域>主题>子主题"，用 > 分隔，至少包含领域和主题
2. **段落摘要**：不超过 50 字的简洁准确摘要
3. **结构化正文**：基于原文深度理解后重写，保持核心信息不变，使用 Markdown 格式，不超过 300 字

## 输出格式（严格 JSON 数组）
[
  {
    "index": 0,
    "label": "网络运维>防火墙配置>访问控制策略",
    "summary": "介绍防火墙访问控制策略的配置步骤",
    "structured_content": "防火墙访问控制策略用于限制网络流量。配置步骤包括：1. 定义规则集；2. 设置源/目的地址；3. 配置协议和端口；4. 应用规则到接口。",
    "confidence": 0.95
  }
]

## 规则
1. 每个段落必须生成层级标签、摘要和结构化正文
2. 摘要控制在 50 字以内，结构化正文控制在 300 字以内
3. 结构化正文必须保持原文核心信息不变，可适当重排和优化表达
4. 置信度基于对段落理解的确定性（0-1）"""


class KnowledgeExtractor:
    """知识抽取引擎"""

    def __init__(self) -> None:
        self.llm = get_llm_client()
        self.settings = get_settings()
        self.compiled_extractor = CompiledKnowledgeExtractor()

    async def classify_paragraphs(self, doc: ParsedDocument) -> list[dict]:
        """段落级 LLM 归类 — 为每段内容生成层级标签、摘要、结构化正文

        Args:
            doc: ParsedDocument（含 elements）

        Returns:
            段落归类结果列表，每个元素包含：index, label, summary, structured_content, confidence
        """
        if not doc.elements:
            return []

        paragraphs = []
        for idx, elem in enumerate(doc.elements):
            if elem.type.value == "paragraph" and elem.content.strip():
                paragraphs.append({
                    "index": idx,
                    "content": elem.content,
                    "section": elem.section,
                    "parent_section": elem.parent_section,
                })

        if not paragraphs:
            return []

        results = []
        batch_size = 20  # P1: E1 — 5→20 减少 LLM 调用次数
        doc_title = doc.title or ""
        for i in range(0, len(paragraphs), batch_size):
            batch = paragraphs[i:i + batch_size]
            batch_results = await self._classify_batch(batch, doc_title)
            results.extend(batch_results)

        logger.info(
            "paragraph_classification_done",
            doc_id=doc.doc_id,
            total=len(results),
        )
        return results

    async def _classify_batch(self, paragraphs: list[dict], doc_title: str = "") -> list[dict]:
        """批量分类段落"""
        content_lines = []
        for p in paragraphs:
            content_lines.append(f"[段落{p['index']}] {p['content'][:500]}")

        user_prompt = f"""请对以下文档段落进行智能归类和处理。

# 文档标题
{doc_title or '未指定'}

# 段落内容
{chr(10).join(content_lines)}

# 要求
按照预设的分类体系对每个段落生成：层级标签、段落摘要、结构化正文。"""

        try:
            resp = await self.llm.chat(
                messages=[
                    ChatMessage(role="system", content=PARAGRAPH_CLASSIFY_PROMPT),
                    ChatMessage(role="user", content=user_prompt),
                ],
                temperature=0.2,
                max_tokens=self.settings.llm_max_tokens,
            )
            data = self._parse_json(resp.text)
            return data
        except Exception as e:
            logger.error("paragraph_classification_failed", error=str(e))
            fallback_results = []
            for p in paragraphs:
                fallback_results.append({
                    "index": p["index"],
                    "label": "未分类>未分类>未分类",
                    "summary": p["content"][:50],
                    "structured_content": p["content"][:300],
                    "confidence": 0.5,
                })
            return fallback_results

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

        # LLM 抽取为空时启用编译抽取兜底
        if not entities and not relations:
            logger.info("extraction_fallback_to_compiled", doc_id=doc.doc_id)
            compiled_result = self.compiled_extractor.extract_from_document(doc)
            entities = compiled_result.entities or []
            relations = compiled_result.relations or []

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
