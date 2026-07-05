"""知识编译引擎（W6）

三阶段流水线：
  1. 去重（精确匹配 + 结构相似 + 哈希索引）
  2. 合并（同义实体合并，属性聚合）
  3. 权威评分（Authority = 0.5*SourceWeight + 0.3*Recency + 0.2*Consensus）

触发策略：event（事件驱动）/ batch（定时批量）/ manual（手动触发）
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import structlog

from app.config import get_settings
from app.knowledge.graph_store import GraphEntity, GraphRelation, get_graph_store

logger = structlog.get_logger()

# 来源权重（文档类型 → 可信度）
SOURCE_WEIGHTS = {
    "incident_report": 1.0,  # 故障报告：最高权威
    "sop": 0.9,  # 标准操作流程
    "config_file": 0.85,  # 配置文件
    "runbook": 0.8,  # 运维手册
    "monitoring_log": 0.7,  # 监控日志
    "chat_log": 0.4,  # 聊天记录：最低权威
    "default": 0.6,
}


@dataclass
class DedupResult:
    """去重结果"""

    duplicates_found: int = 0
    merged_count: int = 0
    groups: list[list[GraphEntity]] = field(default_factory=list)
    unique: list[GraphEntity] = field(default_factory=list)  # 无重复的实体


@dataclass
class CompileResult:
    """编译结果"""

    input_entities: int = 0
    after_dedup: int = 0
    merged: int = 0
    scored: int = 0
    errors: list[str] = field(default_factory=list)


class KnowledgeCompiler:
    """知识编译引擎"""

    def __init__(self) -> None:
        self.settings = get_settings()

    def compile(
        self, entities: list[GraphEntity], relations: list[GraphRelation]
    ) -> CompileResult:
        """完整编译流水线"""
        result = CompileResult(input_entities=len(entities))

        # 1. 去重
        dedup_result = self.deduplicate(entities)
        result.duplicates_found = dedup_result.duplicates_found
        entities = self._flatten_merged(dedup_result)

        # 2. 合并
        entities = self.merge_entities(entities)
        result.merged = dedup_result.merged_count

        # 3. 权威评分
        entities = self.score_authority(entities)
        result.scored = len(entities)

        result.after_dedup = len(entities)
        logger.info(
            "compile_done",
            input=result.input_entities,
            after_dedup=result.after_dedup,
            merged=result.merged,
        )
        return result

    # ── 1. 去重 ──

    def deduplicate(self, entities: list[GraphEntity]) -> DedupResult:
        """三级去重：精确哈希 → 同名同类型 → 结构相似"""
        result = DedupResult()

        # 第一级：精确哈希去重（最快）
        seen = {}
        unique = []
        for e in entities:
            key = self._entity_hash(e)
            if key in seen:
                # 合并：保留高置信度
                if e.confidence > seen[key].confidence:
                    seen[key] = e
                result.duplicates_found += 1
            else:
                seen[key] = e
                unique.append(e)
        entities = unique

        # 第二级：同名 + 同类型去重
        groups: dict[str, list[GraphEntity]] = defaultdict(list)
        for e in entities:
            norm_key = f"{e.entity_type}:{e.name.strip().lower()}"
            groups[norm_key].append(e)

        deduped = []
        for group in groups.values():
            if len(group) > 1:
                result.duplicates_found += len(group) - 1
                merged = self._merge_group(group)
                deduped.append(merged)
                result.groups.append(group)
            else:
                deduped.append(group[0])
                result.unique.append(group[0])

        result.merged_count = sum(max(0, len(g) - 1) for g in groups.values())
        return result

    def _entity_hash(self, entity: GraphEntity) -> str:
        """计算实体精确哈希"""
        content = f"{entity.entity_type}|{entity.name}|{json.dumps(entity.properties, sort_keys=True)}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _merge_group(self, group: list[GraphEntity]) -> GraphEntity:
        """合并一组重复实体，保留最高置信度，合并属性"""
        best = max(group, key=lambda e: e.confidence)
        merged_props = {}
        for e in sorted(group, key=lambda e: e.confidence, reverse=True):
            for k, v in e.properties.items():
                if k not in merged_props:
                    merged_props[k] = v
        best.properties = merged_props
        return best

    def _flatten_merged(self, dedup_result: DedupResult) -> list[GraphEntity]:
        """从去重结果中提取合并后的实体列表"""
        entities = []
        for group in dedup_result.groups:
            entities.append(self._merge_group(group))
        entities.extend(dedup_result.unique)
        return entities

    # ── 2. 合并 ──

    def merge_entities(self, entities: list[GraphEntity]) -> list[GraphEntity]:
        """同义实体合并（基于名称相似度）"""
        # 简化实现：对同名不同大小写、含别名的情况合并
        name_index: dict[str, GraphEntity] = {}
        for e in entities:
            key = e.name.strip().lower()
            if key in name_index:
                existing = name_index[key]
                # 合并属性
                for k, v in e.properties.items():
                    if k not in existing.properties:
                        existing.properties[k] = v
                existing.confidence = max(existing.confidence, e.confidence)
            else:
                name_index[key] = e
        return list(name_index.values())

    # ── 3. 权威评分 ──

    def score_authority(self, entities: list[GraphEntity]) -> list[GraphEntity]:
        """权威评分：Authority = 0.5*SourceWeight + 0.3*Recency + 0.2*Consensus"""
        aw = self.settings.authority_source_weight
        rw = self.settings.authority_recency_weight
        cw = self.settings.authority_consensus_weight

        for e in entities:
            source_weight = self._get_source_weight(e)
            recency = self._get_recency_score(e)
            consensus = self._get_consensus_score(e)

            authority = aw * source_weight + rw * recency + cw * consensus
            e.properties["authority_score"] = round(authority, 3)
            e.properties["authority_components"] = {
                "source_weight": source_weight,
                "recency": recency,
                "consensus": consensus,
            }

        return entities

    def _get_source_weight(self, entity: GraphEntity) -> float:
        """根据来源类型计算权重"""
        source_type = entity.properties.get("source_type", "default")
        return SOURCE_WEIGHTS.get(source_type, SOURCE_WEIGHTS["default"])

    def _get_recency_score(self, entity: GraphEntity) -> float:
        """根据最近更新时间计算时效性分数（0-1）"""
        updated = entity.properties.get("updated_at")
        if not updated:
            return 0.5  # 未知时间默认中等

        try:
            if isinstance(updated, str):
                updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            age = datetime.now() - updated
            # 0 天 = 1.0, 30 天 = 0.5, 365 天 = 0.0
            days = age.total_seconds() / 86400
            return max(0.0, 1.0 - days / 365.0)
        except (ValueError, TypeError):
            return 0.5

    def _get_consensus_score(self, entity: GraphEntity) -> float:
        """根据多源一致性计算共识分数"""
        sources = entity.properties.get("sources", [])
        if isinstance(sources, list):
            unique_sources = len(set(sources))
            if unique_sources >= 3:
                return 1.0
            elif unique_sources >= 2:
                return 0.7
            elif unique_sources == 1:
                return 0.4
        return 0.3  # 单源默认为低共识

    # ── 持久化 ──

    def compile_and_store(
        self, entities: list[GraphEntity], relations: list[GraphRelation]
    ) -> CompileResult:
        """编译后写入图谱"""
        result = self.compile(entities, relations)

        # 重新获取去重合并后的实体列表
        dedup_result = self.deduplicate(entities)
        final_entities = self._flatten_merged(dedup_result)
        final_entities = self.merge_entities(final_entities)
        final_entities = self.score_authority(final_entities)

        store = get_graph_store()
        store.batch_upsert(final_entities, relations)

        result.after_dedup = len(final_entities)
        return result


# 全局单例
_compiler: KnowledgeCompiler | None = None


def get_compiler() -> KnowledgeCompiler:
    global _compiler
    if _compiler is None:
        _compiler = KnowledgeCompiler()
    return _compiler
