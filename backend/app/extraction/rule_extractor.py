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

import hashlib
import re
from collections import OrderedDict

import structlog

from app.extraction.types import (
    ExtractedEntity,
    ExtractedRelation,
)
from app.parsers.base import ParsedDocument, ParsedElement

logger = structlog.get_logger()

# P2-1.2 抽取结果缓存上限（LRU 淘汰）
_EXTRACTION_CACHE_MAX = 500

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
# P2-4.6 节点 metadata 抽取模式
# 版本号：v1.2.3 / version: 1.20.1 / nginx 1.20.1
VERSION_RE = re.compile(
    r"(?:^|[\s：(,:])"
    r"(?:version|版本|ver|v)[:：\s]*"
    r"(v?\d+(?:\.\d+){1,3}(?:-[a-z0-9.]+)?)",
    re.IGNORECASE,
)
COMPONENT_VERSION_RE = re.compile(
    r"\b(nginx|redis|mysql|postgresql|kafka|rabbitmq|mongodb|elasticsearch|"
    r"prometheus|grafana|docker|kubernetes|k8s|java|python|nodejs)\s+"
    r"(v?\d+(?:\.\d+){1,3})",
    re.IGNORECASE,
)
# 负责人 / owner：owner: xxx / 负责人：xxx / 负责团队: xxx
OWNER_RE = re.compile(
    r"(?:owner|负责人|负责团队|maintainer|team)[:：]\s*([^\s,，)]{2,40})",
    re.IGNORECASE,
)
# 环境：env: prod / 环境：测试 / environment: staging
ENV_RE = re.compile(
    r"(?:env|environment|环境|部署环境)[:：]\s*([^\s,，)]+)",
    re.IGNORECASE,
)
# 环境 inline 关键词（在主机名或文档中出现 prod/test/staging/dev）
ENV_INLINE_RE = re.compile(
    r"\b(prod|production|test|testing|staging|dev|development)\b",
    re.IGNORECASE,
)
# region / 地区：region: cn-east-1 / 地区：华东 / 区域: us-west-2
REGION_RE = re.compile(
    r"(?:region|地区|区域|机房|zone|az)[:：]\s*([^\s,，)]{2,40})",
    re.IGNORECASE,
)
# P2-4.7 容量元数据：capacity / replicas / 副本数
CAPACITY_RE = re.compile(
    r"(?:capacity|容量| replicas|副本数|实例数|instances)[:：]\s*(\d+)",
    re.IGNORECASE,
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
        # P2-1.2 抽取结果缓存：key=(doc_id, content_hash) → (entities, relations)
        # 进程级共享缓存（所有实例共用），LRU 淘汰
        self._cache: OrderedDict[tuple[str, str], tuple[list[ExtractedEntity], list[ExtractedRelation]]] = (
            OrderedDict()
        )

    def extract(
        self, doc: ParsedDocument
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
        """抽取实体和关系（P2-1.2 带内容哈希缓存）"""
        # P2-1.2 计算内容指纹，命中缓存直接返回
        cache_key = self._cache_key(doc)
        if cache_key is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                # LRU: 命中时移到末尾（最近使用）
                self._cache.move_to_end(cache_key)
                logger.info(
                    "rule_extraction_cache_hit",
                    doc_id=doc.doc_id,
                    entities=len(cached[0]),
                    relations=len(cached[1]),
                )
                return cached

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

        # P2-1.2 写入缓存
        if cache_key is not None:
            self._cache[cache_key] = (entities, relations)
            # LRU 淘汰
            while len(self._cache) > _EXTRACTION_CACHE_MAX:
                self._cache.popitem(last=False)

        logger.info(
            "rule_extraction_done",
            doc_id=doc.doc_id,
            entities=len(entities),
            relations=len(relations),
        )
        return entities, relations

    @staticmethod
    def _cache_key(doc: ParsedDocument) -> tuple[str, str] | None:
        """P2-1.2 生成缓存键：(doc_id, content_hash)

        content_hash 基于所有 element 的 content 拼接计算，
        文档内容变化时自动失效。
        """
        if not doc.doc_id:
            return None
        parts = [doc.title or ""]
        for e in doc.elements:
            parts.append(e.content or "")
        content = "\n".join(parts)
        if not content.strip():
            return None
        h = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
        return (doc.doc_id, h)

    def _extract_from_element(
        self, elem: ParsedElement, doc_id: str
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
        if not elem.content:
            return [], []

        text = elem.content
        section = (elem.section or "").lower()
        entities: list[ExtractedEntity] = []
        relations: list[ExtractedRelation] = []

        # P2-4.6 提前抽取本 element 的全局 metadata（version/owner/env/region/capacity）
        # 这些字段会附加到本 element 抽取出的 Host/Service/Component 实体
        global_meta = self._extract_metadata_from_text(text, section)

        # 1. Host
        for m in HOSTNAME_RE.finditer(text):
            name = m.group(1).lower()
            if self._is_likely_hostname(name):
                props = {"hostname": name, "source_section": section}
                # 主机名中可能含环境信息（xxx-prod-01 → env=prod）
                env_from_name = self._env_from_name(name)
                if env_from_name:
                    props["env"] = env_from_name
                props.update(global_meta)
                self._hostnames[name] = self._make_entity(
                    "Host",
                    name,
                    props,
                    confidence=0.78,
                    evidence=m.group(0),
                    doc_id=doc_id,
                )
                entities.append(self._hostnames[name])

        for m in IPV4_RE.finditer(text):
            ip = m.group(1)
            name = ip.split(":")[0]
            props = {"ip": ip, "source_section": section}
            props.update(global_meta)
            self._hostnames[name] = self._make_entity(
                "Host",
                name,
                props,
                confidence=0.82,
                evidence=ip,
                doc_id=doc_id,
            )
            entities.append(self._hostnames[name])

        for m in HOST_LABEL_RE.finditer(text):
            name = m.group(1).strip().lower()
            if name and not name.startswith("$"):
                props = {"source_section": section}
                env_from_name = self._env_from_name(name)
                if env_from_name:
                    props["env"] = env_from_name
                props.update(global_meta)
                self._hostnames[name] = self._make_entity(
                    "Host",
                    name,
                    props,
                    confidence=0.85,
                    evidence=m.group(0),
                    doc_id=doc_id,
                )
                entities.append(self._hostnames[name])

        # 2. Service
        for m in SERVICE_LABEL_RE.finditer(text):
            name = m.group(1).strip().lower()
            if name and not name.startswith("$") and len(name) < 60:
                props = {"source_section": section, "tier": "unknown"}
                env_from_name = self._env_from_name(name)
                if env_from_name:
                    props["env"] = env_from_name
                props.update(global_meta)
                self._services[name] = self._make_entity(
                    "Service",
                    name,
                    props,
                    confidence=0.82,
                    evidence=m.group(0),
                    doc_id=doc_id,
                )
                entities.append(self._services[name])

        # 3. Component
        lower = text.lower()
        for kw, props in COMPONENT_KEYWORDS.items():
            if re.search(rf"\b{re.escape(kw)}\b", lower):
                merged = {**props, "source_section": section}
                merged.update(global_meta)
                # 组件 + 版本号共现 → 抓版本
                cv_match = COMPONENT_VERSION_RE.search(lower)
                if cv_match and cv_match.group(1).lower() == kw:
                    merged["version"] = cv_match.group(2)
                self._components[kw] = self._make_entity(
                    "Component",
                    kw,
                    merged,
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

    @staticmethod
    def _extract_metadata_from_text(text: str, section: str) -> dict:
        """P2-4.6 从文本中抽取节点 metadata 字段

        返回 dict，可能含：version / owner / env / region / capacity / replicas
        每个字段只取首次匹配。
        """
        meta: dict = {}
        # 版本号（version: 1.20.1 / 版本：v1.2.3）
        m = VERSION_RE.search(text)
        if m:
            meta["version"] = m.group(1).strip()
        # 负责人
        m = OWNER_RE.search(text)
        if m:
            meta["owner"] = m.group(1).strip()
        # 环境（显式 env: xxx 标注优先）
        m = ENV_RE.search(text)
        if m:
            meta["env"] = m.group(1).strip().lower()
        else:
            # 否则从文本中的 inline 关键词推断（取首个，避免噪音）
            m = ENV_INLINE_RE.search(text)
            if m:
                env = m.group(1).lower()
                # 归一化：production → prod, testing → test, development → dev
                env_norm = {
                    "production": "prod",
                    "testing": "test",
                    "development": "dev",
                }.get(env, env)
                meta["env"] = env_norm
        # region
        m = REGION_RE.search(text)
        if m:
            meta["region"] = m.group(1).strip()
        # 容量 / 副本数（P2-4.7 使用）
        m = CAPACITY_RE.search(text)
        if m:
            meta["capacity"] = int(m.group(1))
            meta["replicas"] = int(m.group(1))
        return meta

    @staticmethod
    def _env_from_name(name: str) -> str | None:
        """从主机名/服务名推断环境（xxx-prod-01 → prod）"""
        if not name:
            return None
        lower = name.lower()
        for env in ("prod", "production", "test", "testing", "staging", "dev", "development"):
            if env in lower.split("-") or f"-{env}-" in lower or lower.endswith(f"-{env}"):
                # 归一化
                return {
                    "production": "prod",
                    "testing": "test",
                    "development": "dev",
                }.get(env, env)
        return None

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
