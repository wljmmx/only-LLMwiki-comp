"""规则化抽取器（LLM 不可用时的 fallback）

基于运维领域模式匹配，从 ParsedDocument 中抽取实体/关系：
- Host: 主机名/IP（hostname 命名规范 + IP v4）
- Service: 服务名（service_id 命名 + 中文"服务"标注）
- Component: 已知组件关键词（nginx/redis/mysql/kafka 等）
- Parameter: 配置项（key: value / key = value）
- Command: 反引号或缩进命令块
- Incident: "故障/告警/异常" 段落
- Procedure: "步骤/runbook/处理" 段落

抽取产出经置信度门控后流入 ExtractionResult。
"""

from __future__ import annotations

import re

import structlog

from app.parsers.base import ParsedDocument, ParsedElement
from app.extraction.types import (
    ExtractedEntity,
    ExtractedRelation,
)

logger = structlog.get_logger()

# 已知组件词典（用于 Component 实体识别）
COMPONENT_KEYWORDS = {
    "nginx": {"component_type": "web-server", "category": "reverse-proxy"},
    "apache": {"component_type": "web-server", "category": "web"},
    "redis": {"component_type": "cache", "category": "in-memory"},
    "mysql": {"component_type": "database", "category": "rdbms"},
    "postgresql": {"component_type": "database", "category": "rdbms"},
    "postgres": {"component_type": "database", "category": "rdbms"},
    "mongodb": {"component_type": "database", "category": "document"},
    "kafka": {"component_type": "mq", "category": "stream"},
    "rabbitmq": {"component_type": "mq", "category": "amqp"},
    "elasticsearch": {"component_type": "search", "category": "search-engine"},
    "prometheus": {"component_type": "monitoring", "category": "metrics"},
    "grafana": {"component_type": "monitoring", "category": "dashboard"},
    "docker": {"component_type": "container", "category": "runtime"},
    "kubernetes": {"component_type": "container", "category": "orchestration"},
    "k8s": {"component_type": "container", "category": "orchestration"},
    "nodejs": {"component_type": "runtime", "category": "js"},
    "java": {"component_type": "runtime", "category": "jvm"},
    "python": {"component_type": "runtime", "category": "py"},
}

# 主机名模式：xxx-yyy-zz 或 xxx-yyy-zzz-NN
HOSTNAME_RE = re.compile(
    r"\b([a-z][a-z0-9-]{2,}-\d+[a-z0-9-]*|"
    r"[a-z]+-prod-\d+|[a-z]+-test-\d+|[a-z]+-staging-\d+|"
    r"web-\d+|db-\d+|app-\d+|cache-\d+)\b",
    re.IGNORECASE,
)
# IPv4
IPV4_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?)\b")
# service_id：含 -service 后缀或 snake_case 服务名
SERVICE_RE = re.compile(
    r"\b([a-z][a-z0-9_-]*(?:-service|service|svc|api))\b", re.IGNORECASE
)
# 配置项：key: value 或 key = value
CONFIG_RE = re.compile(
    r"^\s*([a-z_][a-z0-9_]*):\s*([^\s].+?)\s*$", re.MULTILINE | re.IGNORECASE
)
CONFIG_EQ_RE = re.compile(
    r"^\s*([a-z_][a-z0-9_]*?)\s*=\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
)
# 反引号命令
COMMAND_RE = re.compile(r"`([^`\n]{2,200})`")
# "依赖服务:" / "上游应用:" 列表项
DEPENDENCY_LINE_RE = re.compile(
    r"(?:依赖服务|上游应用|上游服务|下游服务|关联组件)[:：]\s*(.+)",
)
# 主机: web-01 模式
HOST_LABEL_RE = re.compile(
    r"(?:主机|host|hostname|节点)[:：]\s*([^\s,，)]+)", re.IGNORECASE
)
SERVICE_LABEL_RE = re.compile(
    r"(?:服务|service|service_id)[:：]\s*([^\s,，)]+)", re.IGNORECASE
)
# 故障关键词
INCIDENT_KEYWORDS = ("故障", "告警", "异常", "事故", "incident", "alert", "outage")
PROCEDURE_KEYWORDS = (
    "runbook",
    "处理步骤",
    "操作步骤",
    "处置",
    "sop",
    "故障处理",
    "步骤",
)


class RuleBasedExtractor:
    """规则化抽取器（LLM 不可用时的兜底）"""

    def __init__(self) -> None:
        self._hostnames: dict[str, ExtractedEntity] = {}
        self._services: dict[str, ExtractedEntity] = {}
        self._components: dict[str, ExtractedEntity] = {}

    def extract(
        self, doc: ParsedDocument
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
        """抽取实体和关系"""
        entities: list[ExtractedEntity] = []
        relations: list[ExtractedRelation] = []

        for elem in doc.elements:
            ents, rels = self._extract_from_element(elem, doc.doc_id)
            entities.extend(ents)
            relations.extend(rels)

        # 从依赖声明中提取关系
        relations.extend(self._extract_dependency_relations(doc, doc.doc_id))

        # 去重（同名实体保留最高置信度）
        entities = self._dedup(entities)

        logger.info(
            "rule_extraction_done",
            doc_id=doc.doc_id,
            entities=len(entities),
            relations=len(relations),
        )
        return entities, relations

    def _extract_from_element(
        self, elem: ParsedElement, doc_id: str
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
        if not elem.content:
            return [], []

        text = elem.content
        section = (elem.section or "").lower()
        entities: list[ExtractedEntity] = []
        relations: list[ExtractedRelation] = []

        # 1. Host
        for m in HOSTNAME_RE.finditer(text):
            name = m.group(1).lower()
            if self._is_likely_hostname(name):
                self._hostnames[name] = self._make_entity(
                    "Host",
                    name,
                    {"hostname": name, "source_section": section},
                    confidence=0.78,
                    evidence=m.group(0),
                    doc_id=doc_id,
                )
                entities.append(self._hostnames[name])

        for m in IPV4_RE.finditer(text):
            ip = m.group(1)
            name = ip.split(":")[0]
            self._hostnames[name] = self._make_entity(
                "Host",
                name,
                {"ip": ip, "source_section": section},
                confidence=0.82,
                evidence=ip,
                doc_id=doc_id,
            )
            entities.append(self._hostnames[name])

        for m in HOST_LABEL_RE.finditer(text):
            name = m.group(1).strip().lower()
            if name and not name.startswith("$"):
                self._hostnames[name] = self._make_entity(
                    "Host",
                    name,
                    {"source_section": section},
                    confidence=0.85,
                    evidence=m.group(0),
                    doc_id=doc_id,
                )
                entities.append(self._hostnames[name])

        # 2. Service
        for m in SERVICE_LABEL_RE.finditer(text):
            name = m.group(1).strip().lower()
            if name and not name.startswith("$") and len(name) < 60:
                self._services[name] = self._make_entity(
                    "Service",
                    name,
                    {"source_section": section, "tier": "unknown"},
                    confidence=0.82,
                    evidence=m.group(0),
                    doc_id=doc_id,
                )
                entities.append(self._services[name])

        # 3. Component
        lower = text.lower()
        for kw, props in COMPONENT_KEYWORDS.items():
            if re.search(rf"\b{re.escape(kw)}\b", lower):
                self._components[kw] = self._make_entity(
                    "Component",
                    kw,
                    {**props, "source_section": section},
                    confidence=0.80,
                    evidence=kw,
                    doc_id=doc_id,
                )
                entities.append(self._components[kw])

        # 4. Parameter（key: value 或 key = value）
        for m in CONFIG_RE.finditer(text):
            key, value = m.group(1).strip(), m.group(2).strip()
            if self._is_valid_param(key, value):
                entities.append(
                    self._make_entity(
                        "Parameter",
                        key,
                        {
                            "key": key,
                            "value": value[:200],
                            "scope": section or "global",
                        },
                        confidence=0.72,
                        evidence=m.group(0)[:200],
                        doc_id=doc_id,
                    )
                )
        for m in CONFIG_EQ_RE.finditer(text):
            key, value = m.group(1).strip(), m.group(2).strip()
            if self._is_valid_param(key, value):
                entities.append(
                    self._make_entity(
                        "Parameter",
                        key,
                        {
                            "key": key,
                            "value": value[:200],
                            "scope": section or "global",
                        },
                        confidence=0.70,
                        evidence=m.group(0)[:200],
                        doc_id=doc_id,
                    )
                )

        # 5. Command（反引号包裹）
        for m in COMMAND_RE.finditer(text):
            cmd = m.group(1).strip()
            if self._is_likely_command(cmd):
                entities.append(
                    self._make_entity(
                        "Command",
                        cmd[:80],
                        {
                            "cmd": cmd[:500],
                            "shell": self._detect_shell(cmd),
                            "risk_level": "low",
                        },
                        confidence=0.75,
                        evidence=m.group(0),
                        doc_id=doc_id,
                    )
                )

        # 6. Incident / Procedure（基于 section 标题）
        section_orig = elem.section or ""
        if any(kw in section_orig.lower() for kw in INCIDENT_KEYWORDS):
            entities.append(
                self._make_entity(
                    "Incident",
                    section_orig or "未命名故障",
                    {
                        "title": section_orig,
                        "source_section": section,
                        "severity": "unknown",
                    },
                    confidence=0.68,
                    evidence=text[:200],
                    doc_id=doc_id,
                )
            )
        if any(kw in section_orig.lower() for kw in PROCEDURE_KEYWORDS):
            entities.append(
                self._make_entity(
                    "Procedure",
                    section_orig or "未命名步骤",
                    {
                        "title": section_orig,
                        "source_section": section,
                        "raw_text": text[:500],
                    },
                    confidence=0.70,
                    evidence=text[:200],
                    doc_id=doc_id,
                )
            )

        return entities, relations

    def _extract_dependency_relations(
        self, doc: ParsedDocument, doc_id: str
    ) -> list[ExtractedRelation]:
        """从 "依赖服务: A, B" 这类声明中提取 DEPENDS_ON 关系"""
        relations: list[ExtractedRelation] = []
        for elem in doc.elements:
            if not elem.content:
                continue
            for m in DEPENDENCY_LINE_RE.finditer(elem.content):
                deps_str = m.group(1)
                # 分割依赖项
                deps = re.split(r"[,，、\s]+", deps_str)
                for dep in deps:
                    dep = dep.strip().lower()
                    if not dep or len(dep) > 50:
                        continue
                    # 尝试匹配已抽取的 service
                    target = self._find_service(dep) or self._find_component(dep)
                    if target:
                        # 关系 from → target（暂时用 doc 标题当 from）
                        from_name = (doc.title or doc.doc_id).lower()
                        relations.append(
                            ExtractedRelation(
                                relation_type="DEPENDS_ON",
                                from_entity=from_name,
                                to_entity=target.name,
                                properties={"source_line": m.group(0)[:200]},
                                confidence=0.65,
                                evidence_span=m.group(0)[:200],
                                source_doc_id=doc_id,
                            )
                        )
        return relations

    def _find_service(self, name: str) -> ExtractedEntity | None:
        return self._services.get(name)

    def _find_component(self, name: str) -> ExtractedEntity | None:
        return self._components.get(name.lower())

    @staticmethod
    def _make_entity(
        entity_type: str,
        name: str,
        properties: dict,
        confidence: float,
        evidence: str,
        doc_id: str,
    ) -> ExtractedEntity:
        return ExtractedEntity(
            entity_type=entity_type,
            name=name,
            properties=properties,
            confidence=confidence,
            evidence_span=evidence[:300],
            source_doc_id=doc_id,
        )

    @staticmethod
    def _is_likely_hostname(name: str) -> bool:
        # 排除单词（必须含数字或 -）
        if not name:
            return False
        if "-" not in name and not any(c.isdigit() for c in name):
            return False
        # 排除常见英文单词
        if name in {"yes", "no", "true", "false", "null", "none"}:
            return False
        return True

    @staticmethod
    def _is_valid_param(key: str, value: str) -> bool:
        if not key or len(key) > 60:
            return False
        if not value or len(value) > 500:
            return False
        # 排除明显不是配置的情况
        if value.startswith(("http://", "https://")) and len(value) > 100:
            return False
        return True

    @staticmethod
    def _is_likely_command(cmd: str) -> bool:
        # 含 shell 特征
        cmd_chars = (
            "sudo",
            "systemctl",
            "service ",
            "tail ",
            "grep ",
            "ps ",
            "kill ",
            "cat ",
            "echo ",
            "cd ",
            "ls ",
            "awk ",
            "sed ",
            "curl ",
            "wget ",
            "docker ",
            "kubectl ",
            "nginx ",
            "mysql ",
            "redis-",
            "ssh ",
        )
        return any(cmd.startswith(c) or f" {c}" in cmd for c in cmd_chars)

    @staticmethod
    def _detect_shell(cmd: str) -> str:
        if "powershell" in cmd.lower() or cmd.startswith("ps "):
            return "powershell"
        return "bash"

    @staticmethod
    def _dedup(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        """同名同类型实体保留最高置信度"""
        seen: dict[tuple[str, str], ExtractedEntity] = {}
        for e in entities:
            key = (e.entity_type, e.name)
            if key not in seen or e.confidence > seen[key].confidence:
                seen[key] = e
        return list(seen.values())
