"""知识抽取引擎（F2 / V2.1 §7）

LLM Few-shot 抽取实体/关系 → 置信度门控分流（自动入图 / 建议审查 / 丢弃）。

新增功能：
- 段落级 LLM 归类：为每段内容生成层级标签、摘要、结构化正文
- 支持批量段落处理，保持上下文一致性
"""

from __future__ import annotations

import asyncio
import json
import re

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
4. 如果文档没有可抽取的实体，返回空数组 []
5. **实体名称必须由你基于章节内容总结生成简洁的标准术语**，禁止直接复制章节标题或原文中的装饰性文字
6. **实体名称示例**：
   - 章节标题 "子网掩码（这个很重要，划重点！！！）" → 实体名应为 "子网掩码"
   - 章节标题 "Nginx 配置详解（必看！！！）" → 实体名应为 "Nginx 配置"
   - 章节标题 "注意：MySQL 数据库性能优化（重要）" → 实体名应为 "MySQL 数据库性能优化"
7. 实体名称应简洁、规范、无装饰性文字（不含感叹号、括号注释、强调标记等）""".strip()

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

    async def classify_paragraphs(
        self,
        doc: ParsedDocument,
        on_progress: Any = None,
    ) -> list[dict]:
        """段落级 LLM 归类 — 为每段内容生成层级标签、摘要、结构化正文

        Args:
            doc: ParsedDocument（含 elements）
            on_progress: 可选的进度回调 callback(batch_idx, total_batches, batch_count)

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
        total_batches = (len(paragraphs) + batch_size - 1) // batch_size
        for i in range(0, len(paragraphs), batch_size):
            batch_idx = i // batch_size
            batch = paragraphs[i:i + batch_size]
            logger.info(
                "paragraph_classification_batch_start",
                doc_id=doc.doc_id,
                batch_idx=batch_idx + 1,
                total_batches=total_batches,
                batch_size=len(batch),
            )
            batch_results = await self._classify_batch(batch, doc_title)
            results.extend(batch_results)
            # 发送批次进度
            if on_progress:
                try:
                    on_progress(batch_idx + 1, total_batches, len(results))
                except Exception:
                    pass

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

        call_timeout = getattr(self.settings, 'llm_call_timeout', 900)
        try:
            resp = await asyncio.wait_for(
                self.llm.chat(
                    messages=[
                        ChatMessage(role="system", content=PARAGRAPH_CLASSIFY_PROMPT),
                        ChatMessage(role="user", content=user_prompt),
                    ],
                    temperature=0.2,
                    max_tokens=self.settings.llm_max_tokens,
                ),
                timeout=call_timeout,
            )
            data = self._parse_json(resp.text)
            return data
        except asyncio.TimeoutError:
            logger.error("paragraph_classification_timeout", timeout=call_timeout)
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

    async def extract(self, doc: ParsedDocument, on_progress=None) -> ExtractionResult:
        """从 ParsedDocument 抽取知识"""
        result = ExtractionResult(doc_id=doc.doc_id)

        # 修复：旧版仅检查 doc.elements 为空就早返回，但 compiled_extractor 可从
        # heading_tree 提取 Concept 实体。当文档只有标题树无元素时，旧逻辑直接返回
        # 空结果，跳过 LLM 调用与 fallback，导致 0 产出。现仅当两者均空时早返回。
        if not doc.elements and not doc.heading_tree:
            return result

        if on_progress:
            try:
                on_progress("progress", {"percent": 10, "message": "构建抽取上下文..."})
            except Exception:
                pass

        # 组装上下文（取前 20 个元素，限制 token）
        context = self._build_context(doc)
        if not context.strip():
            return result

        if on_progress:
            try:
                on_progress("progress", {"percent": 30, "message": f"调用 LLM 抽取（上下文 {len(context)} 字符）..."})
            except Exception:
                pass

        # 调用 LLM 抽取
        logger.info("extraction_llm_call_start", doc_id=doc.doc_id, context_len=len(context))
        raw_entities, raw_relations, llm_error = await self._call_llm(context)
        logger.info(
            "extraction_llm_call_done",
            doc_id=doc.doc_id,
            entities=len(raw_entities),
            relations=len(raw_relations),
            error=llm_error,
        )

        # 记录处理路径：LLM 主路径还是 fallback
        processing_path = "llm"
        
        if llm_error:
            if on_progress:
                try:
                    on_progress("progress", {
                        "percent": 65,
                        "message": f"⚠️ LLM 抽取失败: {llm_error[:50]}",
                        "path": "llm_error",
                        "error": llm_error,
                    })
                except Exception:
                    pass
        elif on_progress:
            try:
                on_progress("progress", {
                    "percent": 70,
                    "message": f"✅ LLM 返回 {len(raw_entities)} 实体、{len(raw_relations)} 关系",
                    "path": "llm_success",
                })
            except Exception:
                pass

        # 转换为内部类型（_parse_entity/_parse_relation 内部已对畸形 confidence 容错）
        entities = [self._parse_entity(e, doc.doc_id) for e in raw_entities]
        relations = [self._parse_relation(r, doc.doc_id) for r in raw_relations]

        # 关键修复：对主路径 LLM 返回的实体也进行实体名清理
        # 问题：LLM 可能返回原始标题作为实体名（如"啥是IP地址"），需要二次清理
        entity_cleanup_path = "none"  # 记录实体名清理路径
        if entities:
            logger.info(
                "extraction_llm_entity_name_cleanup",
                doc_id=doc.doc_id,
                entity_count=len(entities),
                sample_names=[e.name[:30] for e in entities[:5]],
            )
            entities, cleanup_used_llm = await self._llm_clean_fallback_entity_names(
                entities, doc.title or "", on_progress=on_progress,
            )
            entity_cleanup_path = "llm" if cleanup_used_llm else "rule"
            if on_progress:
                try:
                    path_msg = "LLM 二次清理" if cleanup_used_llm else "规则兜底清理"
                    on_progress("progress", {
                        "percent": 75,
                        "message": f"📝 实体名{path_msg}完成",
                        "cleanup_path": entity_cleanup_path,
                    })
                except Exception:
                    pass
            logger.info(
                "extraction_llm_entity_name_cleanup_done",
                doc_id=doc.doc_id,
                cleaned_count=len(entities),
                cleanup_path=entity_cleanup_path,
            )

        # LLM 抽取为空时启用编译抽取兜底
        # 修复：旧版条件 `if not entities and not relations` 只在 LLM 返回空列表时触发，
        # 但若 LLM 返回了实体却全部因 confidence 低于 review 阈值被门控丢弃，
        # 旧逻辑不会触发 fallback，导致 0 产出。这里在门控后再做一次判定。
        if not entities and not relations:
            processing_path = "fallback"
            logger.info("extraction_fallback_to_compiled", doc_id=doc.doc_id, reason="llm_empty")
            if on_progress:
                try:
                    on_progress("progress", {
                        "percent": 80,
                        "message": "🔄 LLM 抽取为空，启用规则兜底抽取...",
                        "path": "fallback_start",
                    })
                except Exception:
                    pass
            try:
                compiled_result = self.compiled_extractor.extract_from_document(doc)
                entities = compiled_result.entities or []
                relations = compiled_result.relations or []
                # LLM 清理 fallback 实体名 — 用 LLM 总结生成简洁规范的实体名
                if entities:
                    entities, fallback_used_llm = await self._llm_clean_fallback_entity_names(
                        entities, doc.title or "", on_progress=on_progress,
                    )
                    entity_cleanup_path = "llm_fallback" if fallback_used_llm else "rule_fallback"
                    if on_progress:
                        try:
                            path_msg = "LLM 清理" if fallback_used_llm else "规则清理"
                            on_progress("progress", {
                                "percent": 85,
                                "message": f"📝 Fallback 实体名{path_msg}完成",
                                "cleanup_path": entity_cleanup_path,
                            })
                        except Exception:
                            pass
            except Exception as e:  # noqa: BLE001
                # fallback 自身失败不应中断 extract()
                logger.error("extraction_fallback_failed", doc_id=doc.doc_id, error=str(e))
                if on_progress:
                    try:
                        on_progress("progress", {
                            "percent": 85,
                            "message": f"❌ Fallback 抽取失败: {str(e)[:50]}",
                            "path": "fallback_error",
                            "error": str(e),
                        })
                    except Exception:
                        pass
                entities, relations = [], []

        # ── 富化实体 evidence_span：绑定来源段落的完整内容 ──
        # 逐段处理：从 doc.elements 中找到与实体相关的段落，保存完整内容
        if entities and doc.elements:
            entities = self._enrich_entities_with_paragraphs(entities, doc)

        # 置信度门控
        self._apply_gating(entities, relations, result)

        # 修复：若 LLM 返回了实体但门控后 auto+review 均为空（全部被 discard），
        # 且未触发前面的 fallback（因为 entities 非空），则补一次 fallback。
        if (
            entities
            and not result.auto_accepted_entities
            and not result.review_entities
            and not result.auto_accepted_relations
            and not result.review_relations
        ):
            logger.info(
                "extraction_fallback_to_compiled",
                doc_id=doc.doc_id, reason="all_discarded_after_gating",
            )
            if on_progress:
                try:
                    on_progress("progress", {
                        "percent": 85,
                        "message": "🔄 LLM 实体全部被门控丢弃，启用规则兜底...",
                        "path": "fallback_gating",
                    })
                except Exception:
                    pass
            try:
                compiled_result = self.compiled_extractor.extract_from_document(doc)
                fallback_entities = compiled_result.entities or []
                fallback_relations = compiled_result.relations or []
                # LLM 清理 fallback 实体名
                if fallback_entities:
                    fallback_entities, gating_used_llm = await self._llm_clean_fallback_entity_names(
                        fallback_entities, doc.title or "", on_progress=on_progress,
                    )
                    entity_cleanup_path = "llm_fallback_gating" if gating_used_llm else "rule_fallback_gating"
                # 富化 fallback 实体的 evidence_span
                if fallback_entities and doc.elements:
                    fallback_entities = self._enrich_entities_with_paragraphs(
                        fallback_entities, doc,
                    )
                # 合并 fallback 结果到 result（不覆盖原 entities 统计）
                self._apply_gating(fallback_entities, fallback_relations, result)
            except Exception as e:  # noqa: BLE001
                logger.error("extraction_fallback_failed", doc_id=doc.doc_id, error=str(e))
                if on_progress:
                    try:
                        on_progress("progress", {
                            "percent": 86,
                            "message": f"❌ Fallback 抽取失败: {str(e)[:40]}",
                            "path": "fallback_error",
                            "error": str(e),
                        })
                    except Exception:
                        pass

        # 设置处理路径信息
        result.processing_path = processing_path
        result.entity_cleanup_path = entity_cleanup_path
        result.llm_error = llm_error

        if on_progress:
            try:
                path_emoji = "✅" if processing_path == "llm" else "🔄"
                cleanup_msg = f"，实体名清理: {entity_cleanup_path}" if entity_cleanup_path != "none" else ""
                on_progress("progress", {
                    "percent": 100,
                    "message": f"{path_emoji} 抽取完成：{len(result.auto_accepted_entities)} 自动 + {len(result.review_entities)} 审查{cleanup_msg}",
                    "path": processing_path,
                    "cleanup_path": entity_cleanup_path,
                    "llm_error": llm_error,
                })
            except Exception:
                pass

        logger.info(
            "extraction_done",
            doc_id=doc.doc_id,
            total=len(entities),
            auto=len(result.auto_accepted_entities),
            review=len(result.review_entities),
            discarded=result.discarded_count,
            processing_path=processing_path,
            entity_cleanup_path=entity_cleanup_path,
            llm_error=llm_error,
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

    async def _call_llm(self, context: str) -> tuple[list[dict], list[dict], str | None]:
        """调用 LLM 抽取，返回 (entities, relations, error_message)

        添加 asyncio.wait_for 超时保护，默认 600s。
        """
        call_timeout = getattr(self.settings, 'llm_call_timeout', 900)
        try:
            resp = await asyncio.wait_for(
                self.llm.chat(
                    messages=[
                        ChatMessage(role="system", content=EXTRACTION_SYSTEM_PROMPT),
                        ChatMessage(role="user", content=context),
                    ],
                    temperature=0.1,
                    max_tokens=self.settings.llm_max_tokens,
                ),
                timeout=call_timeout,
            )
            # 记录 LLM 返回内容（用于排查空结果问题）
            logger.info(
                "extraction_llm_response",
                text_len=len(resp.text),
                text_preview=resp.text[:300] if resp.text else "(empty)",
                model=resp.model,
            )
            # 解析 JSON
            data = self._parse_json(resp.text)
            entities = [d for d in data if "entity_type" in d]
            relations = [d for d in data if "relation_type" in d]
            if not entities and not relations:
                logger.warning(
                    "extraction_llm_empty_result",
                    text_len=len(resp.text),
                    text_preview=resp.text[:500] if resp.text else "(empty)",
                )
            return entities, relations, None
        except asyncio.TimeoutError:
            error_msg = f"LLM 抽取超时（{call_timeout}s）"
            logger.error("extraction_llm_timeout", timeout=call_timeout)
            return [], [], error_msg
        except Exception as e:
            error_msg = f"LLM 抽取失败: {e}"
            logger.error("extraction_llm_failed", error_type=type(e).__name__, error_str=str(e))
            return [], [], error_msg

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

    @staticmethod
    def _safe_confidence(raw, default: float = 0.5) -> float:
        """安全解析 confidence 字段，容忍 None/字符串/超出范围等畸形输入。

        修复：旧版 `float(raw.get("confidence", 0.5))` 在 LLM 返回
        `{"confidence": null}` 或 `{"confidence": "high"}` 时抛 TypeError/ValueError，
        且该调用在列表推导中无 try/except，单条坏数据会中止整个 extract()。
        """
        try:
            value = float(raw) if not isinstance(raw, dict) else float(raw.get("confidence", default))
        except (TypeError, ValueError):
            return default
        # 超出 [0,1] 范围视为畸形，回退默认值
        if value < 0 or value > 1:
            return default
        return value

    def _parse_entity(self, raw: dict, doc_id: str) -> ExtractedEntity:
        return ExtractedEntity(
            entity_type=raw.get("entity_type", "Concept"),
            name=raw.get("name", raw.get("term", "")),
            properties=raw.get("properties", {}),
            confidence=self._safe_confidence(raw, default=0.5),
            evidence_span=raw.get("evidence_span", ""),
            source_doc_id=doc_id,
        )

    # Fallback 实体名 LLM 清理

    _FALLBACK_CLEAN_PROMPT = """你是一个运维知识图谱的实体命名专家。请根据章节内容为每个实体生成简洁、规范的标准名称。

## 规则
1. 实体名称应为简洁的标准术语，不超过 15 个字符
2. 必须去除所有装饰性文字：括号注释（如"重要"、"划重点"）、感叹号、强调标记
3. 保留核心技术术语，不要改变实体含义
4. 按 JSON 格式输出，只输出 JSON

## 输出格式
[{{"index": 0, "cleaned_name": "清理后的标准名称"}}]"""

    async def _llm_clean_fallback_entity_names(
        self, entities: list, doc_title: str = "", on_progress=None,
    ) -> tuple[list, bool]:
        """对实体名进行 LLM 清理（确保实体名简洁规范）

        Args:
            entities: 实体列表（支持 ExtractedEntity 或 compiled_extractor.ExtractedEntity）
            doc_title: 文档标题
            on_progress: 进度回调

        Returns:
            (清理后的实体列表, 是否使用了 LLM 清理)
        """
        if not entities:
            return entities, False

        logger.info(
            "entity_name_clean_start",
            doc_title=doc_title,
            entity_count=len(entities),
            original_names=[e.name[:40] for e in entities[:5]],
        )

        # 构建 LLM 输入 — 传入原始名称和上下文
        input_items = [
            {
                "index": i,
                "original_name": e.name,
                "evidence": (getattr(e, 'evidence_span', '') or '')[:100],
                "type": getattr(e, 'entity_type', 'Concept'),
            }
            for i, e in enumerate(entities[:30])
        ]

        user_prompt = f"""## 文档标题
{doc_title or '未指定'}

## 实体列表
{json.dumps(input_items, ensure_ascii=False, indent=2)}

## 要求
为每个实体生成简洁规范的标准名称。
示例：原始名"子网掩码（这个很重要，划重点！！！）" → 清理为"子网掩码"
示例：原始名"啥是IP地址" → 清理为"IP地址"
示例：原始名"Nginx 配置详解（必看！！！）" → 清理为"Nginx 配置"

请直接输出 JSON 数组。"""

        call_timeout = getattr(self.settings, 'llm_call_timeout', 300)
        try:
            if on_progress:
                try:
                    on_progress("progress", {
                        "percent": 72,
                        "message": "🤖 调用 LLM 清理实体名...",
                        "cleanup_path": "llm_calling",
                    })
                except Exception:
                    pass
            
            resp = await asyncio.wait_for(
                self.llm.chat(
                    messages=[
                        ChatMessage(role="system", content=self._FALLBACK_CLEAN_PROMPT),
                        ChatMessage(role="user", content=user_prompt),
                    ],
                    temperature=0.1,
                    max_tokens=2000,
                ),
                timeout=call_timeout,
            )
            data = self._parse_json(resp.text)
            
            if not isinstance(data, list):
                logger.warning("entity_name_clean_invalid_response", response_type=type(data).__name__)
                # LLM 返回无效格式，使用规则清理兜底
                if on_progress:
                    try:
                        on_progress("progress", {
                            "percent": 73,
                            "message": "⚠️ LLM 返回无效格式，使用规则清理",
                            "cleanup_path": "llm_invalid_fallback_rule",
                        })
                    except Exception:
                        pass
                return self._rule_clean_entity_names(entities), False

            # 应用清理结果
            cleaned_count = 0
            for item in data:
                idx = item.get("index", -1)
                cleaned = item.get("cleaned_name", "")
                if 0 <= idx < len(entities) and cleaned:
                    # 验证清理后的名称是否合理
                    if len(cleaned) > 0 and len(cleaned) <= 50:
                        entities[idx].name = cleaned
                        cleaned_count += 1

            logger.info(
                "entity_name_clean_done",
                cleaned_count=cleaned_count,
                total_count=len(entities),
                cleaned_names=[e.name[:40] for e in entities[:5]],
                used_llm=True,
            )
            return entities, True
            
        except asyncio.TimeoutError:
            logger.warning("entity_name_clean_timeout", timeout=call_timeout)
            if on_progress:
                try:
                    on_progress("progress", {
                        "percent": 73,
                        "message": f"⏱️ LLM 清理超时（{call_timeout}s），使用规则清理",
                        "cleanup_path": "llm_timeout_fallback_rule",
                    })
                except Exception:
                    pass
            # 超时，使用规则清理兜底
            return self._rule_clean_entity_names(entities), False
        except Exception as e:
            logger.warning("entity_name_clean_failed", error=str(e), count=len(entities))
            if on_progress:
                try:
                    on_progress("progress", {
                        "percent": 73,
                        "message": f"❌ LLM 清理失败: {str(e)[:40]}，使用规则清理",
                        "cleanup_path": "llm_error_fallback_rule",
                        "error": str(e),
                    })
                except Exception:
                    pass
            # 其他错误，使用规则清理兜底
            return self._rule_clean_entity_names(entities), False

    def _rule_clean_entity_names(self, entities: list) -> list:
        """规则清理实体名（作为 LLM 清理的兜底）

        使用正则去除装饰性文字，并提取核心术语：
        - 括号内容：（xxx）、(xxx)
        - 感叹号、强调标记
        - 开头的疑问词：什么是、啥是、如何、怎么
        - 数字前缀：如 "1 白橙" → "白橙"（颜色编码等列表项）
        - 冒号后长句截断：如 "路由器接口：物理接口分..." → "路由器接口"
        - 长句截断：超过30字符的名称提取核心短语
        """
        import re

        logger.info("entity_name_rule_clean_start", count=len(entities))

        # 不应作为实体的名称模式（颜色编码、纯数字等）
        invalid_patterns = [
            re.compile(r'^\d+\s+\S{1,10}$'),  # "1 白橙" 等颜色编码
            re.compile(r'^\d+$'),              # 纯数字
            re.compile(r'^[#\-*=]{2,}$'),      # markdown 分隔线
        ]

        valid_entities = []
        for e in entities:
            name = e.name
            # 0. 检查是否是无效实体名（颜色编码等），直接跳过
            is_invalid = False
            for pattern in invalid_patterns:
                if pattern.match(name.strip()):
                    is_invalid = True
                    logger.info("entity_name_rule_clean_skip", original=name, reason="invalid_pattern")
                    break
            if is_invalid:
                continue

            original_name = name

            # 1. 去除括号及括号内内容
            name = re.sub(r'[（(][^)）]*[)）]', '', name)
            # 2. 去除感叹号和强调标记
            name = re.sub(r'[！!]+', '', name)
            # 3. 去除开头的疑问词（保留核心术语）
            name = re.sub(r'^(什么是|啥是|何谓|如何理解|谈谈)', '', name)
            # 4. 去除尾部的强调词
            name = re.sub(r'(划重点|必看|重要|必读|详解|介绍|必看！！！|划重点！！！)$', '', name)
            # 5. 去除开头的数字编号前缀（如 "1. 概述" → "概述"）
            name = re.sub(r'^\d+(?:\.\d+)*[.、\s]+', '', name)
            # 6. 去除数字编号+空格（如 "1 白橙" → "白橙"，但仅限短名称）
            name = re.sub(r'^\d+\s+(?=[^a-zA-Z])', '', name)
            # 7. 清理多余空白
            name = name.strip()

            # 8. 核心术语提取：如果名称过长（>30字符），尝试提取核心术语
            #    规则：以冒号、破折号、分号、句号为分隔，取第一个短语
            if len(name) > 30:
                # 尝试按冒号分割，取冒号前的部分（如 "路由器接口：详细说明..." → "路由器接口"）
                colon_match = re.match(r'^([^：:;；,，。.]{2,30})[：:;；,，。.]\s*', name)
                if colon_match:
                    name = colon_match.group(1).strip()
                else:
                    # 尝试按句号分割，取第一句
                    sentence_match = re.match(r'^([^。！？!?.\n]{2,30})', name)
                    if sentence_match:
                        name = sentence_match.group(1).strip()

            # 9. 如果清理后为空或过短，跳过
            if not name or len(name) < 2:
                logger.info("entity_name_rule_clean_skip", original=original_name, reason="too_short_after_clean")
                continue

            # 10. 限制最大长度（防止长句作为实体名）
            if len(name) > 50:
                name = name[:50].rsplit('，', 1)[0].rsplit(',', 1)[0].strip()
                if len(name) < 2:
                    name = original_name[:30].strip()

            # 11. 如果清理后名称和原始名称差异太大，保留原始名称的核心部分
            if len(original_name) > 100 and len(name) < 5:
                # 清理过度，保留前30字符
                name = original_name[:30].strip().rstrip('，,。.：:;；')

            e.name = name
            valid_entities.append(e)

        logger.info("entity_name_rule_clean_done", cleaned_names=[e.name[:40] for e in valid_entities[:5]], skipped=len(entities) - len(valid_entities))
        return valid_entities

    def _parse_relation(self, raw: dict, doc_id: str) -> ExtractedRelation:
        return ExtractedRelation(
            relation_type=raw.get("relation_type", "RELATED_TO"),
            from_entity=raw.get("from_entity", ""),
            to_entity=raw.get("to_entity", ""),
            properties=raw.get("properties", {}),
            confidence=self._safe_confidence(raw, default=0.5),
            evidence_span=raw.get("evidence_span", ""),
            source_doc_id=doc_id,
        )

    def _enrich_entities_with_paragraphs(
        self,
        entities: list,
        doc: ParsedDocument,
    ) -> list:
        """为实体绑定来源段落的完整内容和索引。

        逐段处理：从 doc.elements 中找到与实体相关的段落，
        将其完整内容保存到实体的 evidence_span 中，
        同时记录段落索引到 properties['source_paragraph_index']。
        """
        doc_elements = doc.elements or []
        if not doc_elements:
            return entities

        paragraphs: list[dict] = []
        for idx, elem in enumerate(doc_elements):
            content = elem.content if hasattr(elem, 'content') else (elem.get('content', '') if isinstance(elem, dict) else '')
            section = elem.section if hasattr(elem, 'section') else (elem.get('section', '') if isinstance(elem, dict) else '')
            elem_type = elem.type.value if hasattr(elem, 'type') and hasattr(elem.type, 'value') else str(elem.type) if hasattr(elem, 'type') else ''
            if content and content.strip():
                paragraphs.append({
                    'content': content.strip(),
                    'index': idx,
                    'section': section,
                    'type': elem_type,
                })

        if not paragraphs:
            return entities

        enriched_entities = []
        for entity in entities:
            original_evidence = (entity.evidence_span or '').strip()
            entity_name = (entity.name or '').strip()

            matched_content, matched_index = self._find_matching_paragraph(
                entity_name, original_evidence, paragraphs,
            )

            if matched_content and len(matched_content) > len(original_evidence):
                entity.evidence_span = matched_content[:2000]
                if not hasattr(entity, 'properties') or entity.properties is None:
                    entity.properties = {}
                entity.properties['source_paragraph_index'] = matched_index
                entity.properties['source_paragraph_found'] = True
            else:
                # 即使没匹配到更长内容，也记录段落索引（如果可能）
                if not hasattr(entity, 'properties') or entity.properties is None:
                    entity.properties = {}
                entity.properties.setdefault('source_paragraph_index', -1)

            enriched_entities.append(entity)

        logger.info(
            "entity_paragraph_enrichment_done",
            doc_id=doc.doc_id,
            total_entities=len(enriched_entities),
            enriched_count=sum(
                1 for e in enriched_entities
                if e.properties.get('source_paragraph_found')
            ),
        )
        return enriched_entities

    def _find_matching_paragraph(
        self,
        entity_name: str,
        evidence: str,
        paragraphs: list[dict],
    ) -> tuple[str | None, int]:
        """在段落列表中找到与实体匹配的段落内容和索引。

        Returns:
            (matched_content, paragraph_index) 或 (None, -1)
        """
        # 策略 1: evidence 前 30 字符精确子串匹配
        if evidence and len(evidence) >= 4:
            evidence_prefix = evidence[:30]
            for p in paragraphs:
                if evidence_prefix in p['content']:
                    return p['content'], p['index']

        # 策略 2: 实体名匹配
        if entity_name and len(entity_name) >= 2:
            for p in paragraphs:
                if entity_name in p['content']:
                    return p['content'], p['index']

        # 策略 3: evidence 全文匹配
        if evidence and len(evidence) >= 4:
            for p in paragraphs:
                if evidence in p['content']:
                    return p['content'], p['index']

        # 策略 4: evidence 分词后关键词匹配
        if evidence and len(evidence) >= 2:
            keywords = set()
            for token in re.split(r'[\s,，。；;、\(\)（）\[\]【】]+', evidence):
                token = token.strip()
                if len(token) >= 2:
                    keywords.add(token)
            if keywords:
                for p in paragraphs:
                    for kw in keywords:
                        if kw in p['content']:
                            return p['content'], p['index']

        return None, -1

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
