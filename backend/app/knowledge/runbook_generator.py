"""Runbook 自动生成器（P2-1 + P3-5 升级）

基于已有知识库（文档 + 实体）自动生成故障处理 Runbook。

P3-5 升级：规则召回 + LLM 编译成 wiki 风格 Runbook
  - 规则召回（保留）：FTS5 关键词检索 → RuleBasedExtractor 抽取 → 按类别聚合
  - LLM 编译（新增）：LLM 把召回素材编译为连贯的 wiki 风格正文
  - wiki 风格输出（新增）：YAML frontmatter + [[wikilink]] + AGENTS.md 骨架

输入：
  - symptom: 故障现象描述（如 "nginx 502 Bad Gateway"）
  - service: 受影响服务（可选）
  - host: 受影响主机（可选）

输出：
  - runbook_md: wiki 风格 Markdown（含 frontmatter）
  - sources: 引用的来源文档列表
  - stats: 统计信息

生成流程：
  1. 检索相关文档（基于 symptom 关键词搜索 + service/host 过滤）
  2. 解析文档内容，复用 RuleBasedExtractor 抽取实体
  3. 按类别聚合：Host/Service/Component/Command/Procedure/Incident/Parameter
  4. P3-5: LLM 编译 wiki 风格正文（LLM 不可用时退化为模板）
  5. 生成 YAML frontmatter + 组装完整 wiki 页面
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog
import yaml

from app.config import get_settings
from app.core.llm import ChatMessage, get_llm_client
from app.extraction.compiled_extractor import CompiledKnowledgeExtractor
from app.knowledge.wiki_compiler import make_slug
from app.parsers import get_parser
from app.search import get_search_engine
from app.storage import get_document_store

logger = structlog.get_logger()


@dataclass
class RunbookSources:
    """引用来源"""

    docs: list[dict] = field(default_factory=list)  # 来源文档
    commands: list[dict] = field(default_factory=list)  # 命令清单
    procedures: list[dict] = field(default_factory=list)  # 处置步骤
    incidents: list[dict] = field(default_factory=list)  # 历史故障
    parameters: list[dict] = field(default_factory=list)  # 配置参数
    hosts: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)


class RunbookGenerator:
    """Runbook 自动生成器（P3-5: 规则召回 + LLM 编译成 wiki 风格）"""

    def __init__(self) -> None:
        self.search = get_search_engine()
        self.store = get_document_store()
        self.extractor = CompiledKnowledgeExtractor()
        # P3-5: LLM 编译
        self.llm = get_llm_client()
        self.settings = get_settings()

    def generate(
        self,
        symptom: str,
        service: str = "",
        host: str = "",
        max_docs: int = 5,
    ) -> dict:
        """生成 Runbook

        Returns:
            {
                "runbook_md": str,
                "sources": {...},
                "stats": {...},
            }
        """
        symptom = (symptom or "").strip()
        if not symptom:
            raise ValueError("symptom 不能为空")

        # 1. 构造检索查询
        # FTS5 默认 AND；用 OR 连接各关键词以提高召回，再按 score 排序
        keywords: list[str] = []
        keywords.extend(self._tokenize(symptom))
        if service:
            keywords.extend(self._tokenize(service))
        if host:
            keywords.extend(self._tokenize(host))
        # 去重 + 转义 FTS5 特殊字符
        seen: set[str] = set()
        unique_kw: list[str] = []
        for kw in keywords:
            kw = kw.strip().lower()
            # 跳过纯数字/过短词
            if not kw or len(kw) < 2 or kw in seen:
                continue
            # 跳过 FTS5 保留字
            if kw.upper() in {"AND", "OR", "NOT", "NEAR"}:
                continue
            seen.add(kw)
            # 用双引号包起来避免被当作 FTS5 操作符
            unique_kw.append(f'"{kw}"')
        query = " OR ".join(unique_kw) if unique_kw else symptom

        # 2. 检索相关文档（仅关键字检索，不依赖向量）
        search_results = self.search.search(query, limit=max_docs)
        logger.info(
            "runbook_search_done",
            query=query,
            hits=len(search_results),
        )

        # 3. 抽取实体（从命中文档原文）
        sources = RunbookSources()
        for hit in search_results:
            doc_id = hit.get("doc_id")
            if not doc_id:
                continue
            doc_meta = self.store.get(doc_id) or {}
            content_bytes = self.store.read_content(doc_id)
            if not content_bytes:
                continue

            # 用对应 parser 重新解析（仅为抽取）
            fmt = doc_meta.get("format", "txt")
            try:
                parser = get_parser(fmt)
                parsed = parser.parse(doc_meta.get("stored_path", ""), doc_id)
                result = self.extractor.extract_from_document(parsed)
                entities = result.entities
            except Exception as e:
                logger.warning("runbook_parse_failed", doc_id=doc_id, error=str(e))
                entities = []

            sources.docs.append(
                {
                    "doc_id": doc_id,
                    "title": doc_meta.get("title") or doc_id,
                    "format": fmt,
                    "score": hit.get("combined_score", 0.0),
                    "snippet": hit.get("snippet", "")[:200],
                    "entity_count": len(entities),
                }
            )

            # 聚合实体
            for e in entities:
                self._collect_entity(e, sources)

        # 4. 去重 + 排序
        self._dedup_sources(sources)

        # 5. P3-5: 生成 wiki 风格 Markdown（模板兜底，LLM 不可用时用此路径）
        runbook_md = self._render_wiki_runbook(
            symptom=symptom,
            service=service,
            host=host,
            sources=sources,
        )

        stats = {
            "docs_searched": len(search_results),
            "docs_used": len(sources.docs),
            "commands": len(sources.commands),
            "procedures": len(sources.procedures),
            "incidents": len(sources.incidents),
            "parameters": len(sources.parameters),
            "hosts": len(sources.hosts),
            "services": len(sources.services),
            "components": len(sources.components),
        }
        logger.info("runbook_generated", **stats)

        return {
            "runbook_md": runbook_md,
            "sources": {
                "docs": sources.docs,
                "commands": sources.commands,
                "procedures": sources.procedures,
                "incidents": sources.incidents,
                "parameters": sources.parameters,
                "hosts": sources.hosts,
                "services": sources.services,
                "components": sources.components,
            },
            "stats": stats,
        }

    # ────────── P3-5: LLM 编译 ──────────

    async def generate_async(
        self,
        symptom: str,
        service: str = "",
        host: str = "",
        max_docs: int = 5,
    ) -> dict:
        """P3-5: 规则召回 + LLM 编译成 wiki 风格 Runbook

        与 generate() 相同的召回流程，但正文由 LLM 编译（更连贯、更智能）。
        LLM 不可用时退化为模板生成（与 generate() 一致）。

        Returns:
            与 generate() 相同的 dict 结构，额外含 "llm_compiled": bool
        """
        symptom = (symptom or "").strip()
        if not symptom:
            raise ValueError("symptom 不能为空")

        # 复用 generate() 的召回逻辑
        result = self.generate(symptom, service, host, max_docs)

        # LLM 编译正文
        sources = RunbookSources(
            docs=result["sources"]["docs"],
            commands=result["sources"]["commands"],
            procedures=result["sources"]["procedures"],
            incidents=result["sources"]["incidents"],
            parameters=result["sources"]["parameters"],
            hosts=result["sources"]["hosts"],
            services=result["sources"]["services"],
            components=result["sources"]["components"],
        )

        body_md = await self._llm_compile_body(symptom, service, host, sources)
        llm_compiled = bool(body_md)
        if not llm_compiled:
            # LLM 不可用 → 使用模板正文
            body_md = self._render_runbook_body(symptom, service, host, sources)

        # 组装 wiki 页面（frontmatter + body）
        frontmatter = self._build_frontmatter(symptom, service, host, sources)
        result["runbook_md"] = frontmatter + "\n" + body_md
        result["llm_compiled"] = llm_compiled
        return result

    async def _llm_compile_body(
        self,
        symptom: str,
        service: str,
        host: str,
        sources: RunbookSources,
    ) -> str:
        """P3-5: LLM 编译 wiki 风格正文

        LLM 不可用或出错时返回空串（调用方用模板兜底）。
        """
        system = (
            "你是 OpsKG Wiki 管理员。把召回的运维知识素材编译为结构化的 wiki 风格 Runbook 正文。"
            "严格遵循 AGENTS.md 规定的 runbook 页面骨架。"
            "使用 [[slug]] 双向链接到相关概念（如 [[service-nginx]]、[[host-web-prod-01]]）。"
            "只输出 Markdown 正文，不要 YAML frontmatter，不要 ```md 包裹。"
        )
        prompt = self._build_compile_prompt(symptom, service, host, sources)
        try:
            messages = [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=prompt),
            ]
            resp = await self.llm.chat(
                messages=messages,
                temperature=0.2,
                max_tokens=self.settings.llm_max_tokens,
            )
            text = (resp.text or "").strip()
            # 防御：剥离代码块围栏
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return text
        except Exception as e:
            logger.warning("runbook_llm_compile_failed", error=str(e))
            return ""

    def _build_compile_prompt(
        self,
        symptom: str,
        service: str,
        host: str,
        sources: RunbookSources,
    ) -> str:
        """构造 LLM 编译 prompt"""
        parts: list[str] = []
        parts.append("请把以下运维知识素材编译为一个 wiki 风格的 Runbook 页面正文。\n")
        parts.append("# 编译目标")
        parts.append(f"- 故障现象: {symptom}")
        if service:
            parts.append(f"- 受影响服务: {service}")
        if host:
            parts.append(f"- 受影响主机: {host}")
        parts.append("- 页面骨架: runbook 页（必含：概述/影响分析/排查步骤/处置方案/来源）\n")

        parts.append("# 召回素材\n")
        if sources.commands:
            parts.append("## 诊断命令")
            for c in sources.commands[:10]:
                risk = "⚠️" if c.get("risk_level") == "high" else ""
                parts.append(f"- **{c['name']}** {risk}: `{c['cmd']}`")
            parts.append("")

        if sources.procedures:
            parts.append("## 处置步骤")
            for p in sources.procedures[:5]:
                parts.append(f"- **{p['title']}**: {p.get('raw_text', '')[:200]}")
            parts.append("")

        if sources.incidents:
            parts.append("## 历史故障")
            for inc in sources.incidents[:5]:
                parts.append(f"- **{inc['title']}** (severity: {inc.get('severity', 'unknown')})")
            parts.append("")

        if sources.parameters:
            parts.append("## 配置参数")
            for p in sources.parameters[:15]:
                parts.append(f"- `{p['key']}` = `{p.get('value', '')}` (scope: {p.get('scope', '-')})")
            parts.append("")

        if sources.services:
            parts.append(f"## 涉及服务: {', '.join(sources.services)}")
        if sources.hosts:
            parts.append(f"## 涉及主机: {', '.join(sources.hosts)}")
        if sources.components:
            parts.append(f"## 涉及组件: {', '.join(sources.components)}")
        parts.append("")

        parts.append("# 编译要求")
        parts.append("1. 严格按 runbook 页面骨架输出：概述/影响分析/排查步骤/处置方案/来源")
        parts.append("2. 排查步骤按逻辑顺序组织，不要简单罗列命令")
        parts.append("3. 处置方案整合召回的 procedures，去重并按严重程度排序")
        parts.append("4. 首次提及服务/主机/组件时用 [[slug]] 建链（如 [[service-nginx]]）")
        parts.append("5. 来源章节列出参考文档（用 [[doc_id]] 或文档标题）")
        parts.append("6. 只输出正文，不要 frontmatter")

        return "\n".join(parts)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """简单分词：按空白/标点切分，保留中英文混合词"""
        import re

        # 中英文+数字+短横线
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]+|[一-鿿]+|\d+", text)
        return tokens

    @staticmethod
    def _collect_entity(entity, sources: RunbookSources) -> None:
        et = entity.entity_type
        if et == "Command":
            sources.commands.append(
                {
                    "name": entity.name,
                    "cmd": entity.properties.get("cmd", entity.name),
                    "shell": entity.properties.get("shell", "bash"),
                    "risk_level": entity.properties.get("risk_level", "low"),
                    "evidence": entity.evidence_span,
                    "source_doc_id": entity.source_doc_id,
                }
            )
        elif et == "Procedure":
            sources.procedures.append(
                {
                    "title": entity.name,
                    "raw_text": entity.properties.get("raw_text", ""),
                    "source_doc_id": entity.source_doc_id,
                }
            )
        elif et == "Incident":
            sources.incidents.append(
                {
                    "title": entity.name,
                    "severity": entity.properties.get("severity", "unknown"),
                    "source_doc_id": entity.source_doc_id,
                }
            )
        elif et == "Parameter":
            sources.parameters.append(
                {
                    "key": entity.name,
                    "value": entity.properties.get("value", ""),
                    "scope": entity.properties.get("scope", ""),
                    "source_doc_id": entity.source_doc_id,
                }
            )
        elif et == "Host":
            sources.hosts.append(entity.name)
        elif et == "Service":
            sources.services.append(entity.name)
        elif et == "Component":
            sources.components.append(entity.name)

    @staticmethod
    def _dedup_sources(sources: RunbookSources) -> None:
        # Hosts/Services/Components 去重 + 计数
        sources.hosts = sorted(set(sources.hosts))
        sources.services = sorted(set(sources.services))
        sources.components = sorted(set(sources.components))

        # 命令按 cmd 去重，保留首次出现
        seen_cmds: set[str] = set()
        unique_cmds: list[dict] = []
        for c in sources.commands:
            key = c["cmd"][:200]
            if key not in seen_cmds:
                seen_cmds.add(key)
                unique_cmds.append(c)
        sources.commands = unique_cmds

        # 参数按 key 去重
        seen_params: set[str] = set()
        unique_params: list[dict] = []
        for p in sources.parameters:
            if p["key"] not in seen_params:
                seen_params.add(p["key"])
                unique_params.append(p)
        sources.parameters = unique_params

        # 来源文档按 doc_id 去重
        seen_docs: set[str] = set()
        unique_docs: list[dict] = []
        for d in sources.docs:
            if d["doc_id"] not in seen_docs:
                seen_docs.add(d["doc_id"])
                unique_docs.append(d)
        sources.docs = unique_docs

    # ────────── P3-5: wiki 风格渲染 ──────────

    def _render_wiki_runbook(
        self,
        symptom: str,
        service: str,
        host: str,
        sources: RunbookSources,
    ) -> str:
        """P3-5: 渲染完整 wiki 页面（YAML frontmatter + 正文）

        被 generate() 调用（模板兜底路径）。
        """
        frontmatter = self._build_frontmatter(symptom, service, host, sources)
        body = self._render_runbook_body(symptom, service, host, sources)
        return frontmatter + "\n" + body

    def _build_frontmatter(
        self,
        symptom: str,
        service: str,
        host: str,
        sources: RunbookSources,
    ) -> str:
        """P3-5: 生成 YAML frontmatter（遵循 AGENTS.md §三 骨架）"""
        slug = make_slug("Procedure", symptom)  # → runbook-{symptom}
        title = f"故障处理 Runbook: {symptom}"

        # tags：从 symptom/service/host 分词 + 固定标签
        tags: list[str] = ["runbook", "troubleshooting"]
        for text in (symptom, service, host):
            for tok in self._tokenize(text):
                tok_low = tok.lower()
                if tok_low not in tags and len(tok_low) >= 2:
                    tags.append(tok_low)

        # sources：引用的 raw 文档
        src_list = [
            {
                "doc_id": d["doc_id"],
                "title": d.get("title", d["doc_id"]),
            }
            for d in sources.docs
        ]

        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "slug": slug,
            "title": title,
            "type": "runbook",
            "tags": tags,
            "sources": src_list,
            "created_at": now,
            "updated_at": now,
            "review_status": "auto",
        }
        fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm}\n---\n\n"

    def _render_runbook_body(
        self,
        symptom: str,
        service: str,
        host: str,
        sources: RunbookSources,
    ) -> str:
        """P3-5: 渲染 wiki 风格正文（不含 frontmatter）

        遵循 AGENTS.md §三 runbook 页面骨架：
        概述 / 影响分析 / 排查步骤 / 处置方案 / 来源

        实体引用使用 [[wikilink]]（通过 make_slug 生成 slug）。
        被 generate_async() 的 LLM 降级路径调用，也作为 generate() 的模板正文。
        """
        lines: list[str] = []
        lines.append(f"# 故障处理 Runbook: {symptom}")
        lines.append("")
        lines.append("> 自动生成于 OpsKG Runbook Generator，基于已有知识库聚合。")
        lines.append("")

        # ── 概述 ──
        lines.append("## 概述")
        lines.append("")
        lines.append(f"- **现象**: {symptom}")
        if service:
            lines.append(f"- **受影响服务**: [[{make_slug('Service', service)}|{service}]]")
        if host:
            lines.append(f"- **受影响主机**: [[{make_slug('Host', host)}|{host}]]")
        lines.append(f"- **生成时间**: 基于知识库 {len(sources.docs)} 篇相关文档聚合")
        lines.append("")

        # ── 影响分析 ──
        lines.append("## 影响分析")
        lines.append("")
        if sources.components:
            lines.append("### 涉及组件")
            for c in sources.components:
                lines.append(f"- [[{make_slug('Component', c)}|{c}]]")
            lines.append("")
        if sources.services:
            lines.append("### 涉及服务")
            for s in sources.services:
                lines.append(f"- [[{make_slug('Service', s)}|{s}]]")
            lines.append("")
        if sources.hosts:
            lines.append("### 涉及主机")
            for h in sources.hosts:
                lines.append(f"- [[{make_slug('Host', h)}|{h}]]")
            lines.append("")
        if not (sources.components or sources.services or sources.hosts):
            lines.append("_未在知识库中识别到具体组件/服务/主机，建议补充相关文档。_")
            lines.append("")

        # ── 排查步骤 ──
        lines.append("## 排查步骤")
        lines.append("")
        if sources.commands:
            lines.append("### 关键诊断命令")
            lines.append("")
            for i, c in enumerate(sources.commands[:10], 1):
                risk_badge = "⚠️" if c.get("risk_level") == "high" else ""
                lines.append(f"{i}. **{c['name']}** {risk_badge}")
                lines.append(f"   ```{c.get('shell', 'bash')}")
                lines.append(f"   {c['cmd']}")
                lines.append("   ```")
                lines.append("")
        else:
            lines.append("_未识别到具体诊断命令。_")
            lines.append("")

        # ── 处置方案 ──
        lines.append("## 处置方案")
        lines.append("")
        if sources.procedures:
            for i, p in enumerate(sources.procedures[:5], 1):
                lines.append(f"### {i}. {p['title']}")
                lines.append("")
                raw = p.get("raw_text", "").strip()
                if raw:
                    # 取前 500 字
                    lines.append("```")
                    lines.append(raw[:500])
                    lines.append("```")
                    lines.append("")
        else:
            lines.append("_未识别到标准化处置步骤，建议参考以下历史故障案例。_")
            lines.append("")

        # 历史故障案例（作为处置方案的补充）
        if sources.incidents:
            lines.append("### 历史故障案例")
            lines.append("")
            for inc in sources.incidents[:5]:
                sev = inc.get("severity", "unknown")
                lines.append(f"- **{inc['title']}** (severity: {sev})")
            lines.append("")

        # 关键配置参数
        if sources.parameters:
            lines.append("### 关键配置参数")
            lines.append("")
            lines.append("| 参数 | 值 | 作用域 |")
            lines.append("| --- | --- | --- |")
            for p in sources.parameters[:15]:
                val = (p.get("value") or "").replace("|", "\\|").replace("\n", " ")[:60]
                lines.append(f"| `{p['key']}` | `{val}` | {p.get('scope', '-')} |")
            lines.append("")

        # ── 来源 ──
        lines.append("## 来源")
        lines.append("")
        for d in sources.docs:
            lines.append(
                f"- [{d['title']}]({d['doc_id']}) (相关度 {d['score']:.3f}, 命中实体 {d['entity_count']})"
            )
        lines.append("")

        # 备注
        lines.append("## 备注")
        lines.append("")
        lines.append("- 本 Runbook 由系统自动聚合生成，建议人工审阅后再用于生产环境。")
        lines.append("- 若内容不充分，请上传更多相关文档以扩充知识库。")
        lines.append("")

        return "\n".join(lines)


# 全局单例
_generator: RunbookGenerator | None = None


def get_runbook_generator() -> RunbookGenerator:
    global _generator
    if _generator is None:
        _generator = RunbookGenerator()
    return _generator
