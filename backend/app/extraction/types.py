"""知识抽取数据模型（F2 / V2.1 §7.4）

对齐本体 ops_ontology.yaml：12 类实体 + 11 类关系。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    HOST = "Host"
    SERVICE = "Service"
    COMPONENT = "Component"
    PARAMETER = "Parameter"
    COMMAND = "Command"
    PROCEDURE = "Procedure"
    INCIDENT = "Incident"
    SYMPTOM = "Symptom"
    EXPERIENCE = "Experience"
    CONCEPT = "Concept"
    DOCUMENT = "Document"


class RelationType(str, Enum):
    RUNS_ON = "RUNS_ON"
    USES = "USES"
    DEPENDS_ON = "DEPENDS_ON"
    HAS_PARAMETER = "HAS_PARAMETER"
    CONFIGURED_BY = "CONFIGURED_BY"
    DESCRIBED_IN = "DESCRIBED_IN"
    INVOLVES = "INVOLVES"
    MANIFESTS_AS = "MANIFESTS_AS"
    RESOLVED_BY = "RESOLVED_BY"
    DERIVED_FROM = "DERIVED_FROM"
    RELATED_TO = "RELATED_TO"


@dataclass
class ExtractedEntity:
    """单条抽取实体"""

    entity_type: str  # EntityType value
    name: str  # 唯一标识
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0  # 抽取置信度
    evidence_span: str = ""  # 原文证据片段
    source_doc_id: str = ""  # 来源文档 ID


@dataclass
class ExtractedRelation:
    """单条抽取关系"""

    relation_type: str  # RelationType value
    from_entity: str  # 源实体 name
    to_entity: str  # 目标实体 name
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    evidence_span: str = ""
    source_doc_id: str = ""


@dataclass
class ExtractionResult:
    """单次抽取的完整结果"""

    doc_id: str
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)
    auto_accepted_entities: list[ExtractedEntity] = field(default_factory=list)
    review_entities: list[ExtractedEntity] = field(default_factory=list)
    auto_accepted_relations: list[ExtractedRelation] = field(default_factory=list)
    review_relations: list[ExtractedRelation] = field(default_factory=list)
    discarded_count: int = 0


@dataclass
class ExtractionStats:
    """抽取统计"""

    total_entities: int = 0
    auto_accepted: int = 0
    review_needed: int = 0
    discarded: int = 0
    confidence_avg: float = 0.0
