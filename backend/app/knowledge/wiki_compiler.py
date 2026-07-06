"""Wiki 编译器（P0-4）— LLM-as-Compiler

实现 Karpathy LLM Wiki 范式的"知识编译"核心：把 raw 文档（L1）编译为
结构化 Markdown Wiki 页面（L2），每页遵循 AGENTS.md 定义骨架，并自动建立
[[wikilink]] 双向链接。

核心职责（与 RAG 的关键区别）：
- 编译一次，持续保持最新（不每次检索原文）
- 增量合并：raw 更新 → 合并新事实 → 标注 stale 项 → 触发重编译
- 输出物是 wiki 页面（持久化），不是临时检索片段

输入：doc_id（来自 DocumentStore）
输出：list[WikiPage]，已写回 VersionControl（doc_key = wiki:{slug}）

调用关系：
    DocumentStore (raw)  ──┐
    KnowledgeExtractor   ──┼──► WikiCompiler ──► VersionControl (wiki:*)
    GraphStore (可选辅助) ──┘                   └─► update_backlinks / rebuild_index
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog
import yaml

from app.config import get_settings
from app.core.llm import ChatMessage, get_llm_client
from app.extraction import KnowledgeExtractor
from app.extraction.types import EntityType, ExtractedEntity
from app.knowledge.wiki_drift import clear_stale, record_compiled_checksum
from app.knowledge.wiki_index import _key_from_slug, list_wiki_pages, rebuild_index
from app.knowledge.wikilink import WIKILINK_RE, update_backlinks
from app.observability import span
from app.parsers import get_parser
from app.storage import get_document_store
from app.storage.version_control import get_version_control

logger = structlog.get_logger()

# CJK 字符检测（用于决定匹配策略与最小词长）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# ────────── EntityType → Wiki 页面类型映射 ──────────
# 见 AGENTS.md §三：entity | concept | incident | runbook | service | host
ENTITY_TYPE_TO_PAGE_TYPE: dict[str, str] = {
    EntityType.HOST.value: "host",
    EntityType.SERVICE.value: "service",
    EntityType.COMPONENT.value: "entity",
    EntityType.PARAMETER.value: "entity",
    EntityType.COMMAND.value: "entity",
    EntityType.PROCEDURE.value: "runbook",
    EntityType.INCIDENT.value: "incident",
    EntityType.SYMPTOM.value: "incident",
    EntityType.EXPERIENCE.value: "concept",
    EntityType.CONCEPT.value: "concept",
    EntityType.DOCUMENT.value: "concept",
}


@dataclass
class WikiPage:
    """编译产出的单个 wiki 页面（未持久化前）"""

    slug: str
    title: str
    type: str  # entity | concept | incident | runbook | service | host
    tags: list[str]
    sources: list[dict]  # [{doc_id, title, checksum}]
    body_md: str  # 不含 frontmatter 的正文
    review_status: str = "auto"  # auto | review_needed | approved
    source_doc_id: str = ""
    stale_items: list[str] = field(default_factory=list)  # 与已有版本冲突的事实


@dataclass
class WikiCompileResult:
    """一次编译任务的汇总结果"""

    doc_id: str
    pages_created: int = 0
    pages_updated: int = 0
    pages_unchanged: int = 0
    slugs: list[str] = field(default_factory=list)
    review_needed: list[str] = field(default_factory=list)
    stale_marked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    index_rebuilt: bool = False


# ────────── 命名约定（AGENTS.md §五）──────────

_SLUG_SAFE_RE = re.compile(r"[^a-zA-Z0-9\-_]")


def _slugify(name: str) -> str:
    """转 kebab-case slug 安全形式"""
    s = name.strip().lower()
    s = s.replace(" ", "-").replace("_", "-")
    s = _SLUG_SAFE_RE.sub("", s)
    # 合并连续 -
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unnamed"


def make_slug(entity_type: str, name: str) -> str:
    """根据 AGENTS.md 命名约定生成 slug

    - 实体页：{type}-{name}（host/service/component）
    - 故障页：{symptom}-troubleshooting
    - 概念页：直接用概念名
    - Runbook 页：runbook-{scenario}
    """
    page_type = ENTITY_TYPE_TO_PAGE_TYPE.get(entity_type, "concept")
    base = _slugify(name)
    if page_type == "host":
        return f"host-{base}"
    if page_type == "service":
        return f"service-{base}"
    if page_type == "incident":
        # 若 name 已含 troubleshooting 字样则不再追加
        if "troubleshoot" in base or "故障" in name:
            return base
        return f"{base}-troubleshooting"
    if page_type == "runbook":
        return f"runbook-{base}"
    # concept / entity
    return base


# ────────── 编译器主体 ──────────


class WikiCompiler:
    """把 raw 文档编译为 wiki 页面

    使用方式：
        compiler = get_wiki_compiler()
        result = await compiler.compile_raw_to_wiki(doc_id)
    """

    def __init__(self) -> None:
        self.llm = get_llm_client()
        self.settings = get_settings()
        self.extractor = KnowledgeExtractor()
        self.vc = get_version_control()
        self.store = get_document_store()

    # ── LLM 包装 ──

    async def _llm_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        """统一 LLM 调用入口（与 doc_generator 模式一致）"""
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))
        try:
            resp = await self.llm.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=self.settings.llm_max_tokens,
            )
            return resp.text or ""
        except Exception as e:
            logger.warning("wiki_compiler_llm_failed", error=str(e))
            return ""

    # ── 主入口 ──

    async def compile_raw_to_wiki(
        self,
        doc_id: str,
        *,
        force: bool = False,
        rebuild_index_after: bool = True,
    ) -> WikiCompileResult:
        """把一份 raw 文档编译为 wiki 页面

        流程（AGENTS.md §五）：
            1. 读取 raw 文档 → ParsedDocument
            2. LLM 抽取实体/概念
            3. 对每个实体/概念生成 wiki 页面（合并/新建）
            4. 写回 VersionControl（doc_key=wiki:{slug}）
            5. 更新 backlink
            6. 重建 index.md

        Args:
            doc_id: DocumentStore 中的文档 ID
            force: 强制重编译（即使内容未变）
            rebuild_index_after: 编译后是否重建 index.md
        """
        result = WikiCompileResult(doc_id=doc_id)

        # S15-1c: 知识编译 span 埋点，覆盖整个编译流程
        with span("wiki.compile", doc_id=doc_id) as _sp:
            # 1. 读取 raw
            meta = self.store.get(doc_id)
            if not meta:
                result.errors.append(f"文档不存在: {doc_id}")
                return result

            # 设置 format 属性（span 对象可能为 None，需容错）
            try:
                if _sp is not None:
                    _sp.set_attribute("format", meta.get("format", ""))
            except Exception:  # noqa: BLE001
                pass

            raw_bytes = self.store.read_content(doc_id)
            if not raw_bytes:
                result.errors.append(f"原始文件读取失败: {doc_id}")
                return result

            # 2. 解析 + 抽取
            try:
                parser = get_parser(meta["format"])
                # S15-1c: 文档解析 span 埋点
                with span(
                    "document.parse",
                    doc_id=doc_id,
                    format=meta.get("format", ""),
                ):
                    doc = parser.parse(meta["stored_path"], doc_id)
            except Exception as e:
                result.errors.append(f"解析失败: {e}")
                return result

            try:
                extraction = await self.extractor.extract(doc)
            except Exception as e:
                result.errors.append(f"抽取失败: {e}")
                return result

            entities = list(extraction.auto_accepted_entities) + list(
                extraction.review_entities
            )
            if not entities:
                logger.info("wiki_compiler_no_entities", doc_id=doc_id)
                # 无实体也更新状态
                self.store.update_status(doc_id, "compiled")
                return result

            # 3. 逐个编译
            source_entry = {
                "doc_id": doc_id,
                "title": meta.get("title") or meta.get("filename", doc_id),
                "checksum": meta.get("checksum", ""),
            }

            for entity in entities:
                try:
                    page = await self._compile_entity_page(entity, source_entry)
                    if page is None:
                        continue
                    outcome = self._save_page(page, force=force)
                    result.slugs.append(page.slug)
                    if outcome == "created":
                        result.pages_created += 1
                    elif outcome == "updated":
                        result.pages_updated += 1
                        if page.stale_items:
                            result.stale_marked.append(page.slug)
                    else:
                        result.pages_unchanged += 1
                    if page.review_status == "review_needed":
                        result.review_needed.append(page.slug)
                except Exception as e:
                    logger.exception("wiki_compiler_page_failed", slug=entity.name)
                    result.errors.append(f"{entity.name}: {e}")

            # 4. 状态推进
            self.store.update_status(doc_id, "compiled")

            # 5. 记录编译时 checksum（供 P1-1 漂移检测使用），清除已重编译页面的 stale
            try:
                record_compiled_checksum(doc_id, meta.get("checksum", ""))
                for slug in result.slugs:
                    clear_stale(slug)
            except Exception as e:
                result.errors.append(f"checksum/stale 同步失败: {e}")

            # 6. 重建 index
            if rebuild_index_after and result.pages_created + result.pages_updated > 0:
                try:
                    rebuild_index()
                    result.index_rebuilt = True
                except Exception as e:
                    result.errors.append(f"index 重建失败: {e}")

            # 设置 page_count 属性（编译完成后）
            try:
                if _sp is not None:
                    _sp.set_attribute(
                        "page_count",
                        result.pages_created
                        + result.pages_updated
                        + result.pages_unchanged,
                    )
            except Exception:  # noqa: BLE001
                pass

            logger.info(
                "wiki_compiled",
                doc_id=doc_id,
                created=result.pages_created,
                updated=result.pages_updated,
                unchanged=result.pages_unchanged,
                errors=len(result.errors),
            )
            return result

    # ── 单实体编译 ──

    async def _compile_entity_page(
        self,
        entity: ExtractedEntity,
        source_entry: dict,
    ) -> WikiPage | None:
        """把单个实体编译为 wiki 页面

        - 用 LLM 生成正文（按 AGENTS.md 骨架）
        - LLM 不可用时退化为模板化正文（基于 evidence_span）
        """
        slug = make_slug(entity.entity_type, entity.name)
        page_type = ENTITY_TYPE_TO_PAGE_TYPE.get(entity.entity_type, "concept")
        title = entity.name

        # 标签：实体类型 + properties 中的关键字段
        tags = [entity.entity_type.lower()]
        for k in ("category", "service", "host", "env", "level"):
            v = entity.properties.get(k)
            if isinstance(v, str) and v:
                tags.append(_slugify(v))

        # 调 LLM 写正文
        body_md = await self._llm_write_body(entity, page_type)
        if not body_md:
            body_md = self._template_body(entity, page_type)

        review_status = (
            "review_needed"
            if entity.confidence < self.settings.confidence_review
            else "auto"
        )

        return WikiPage(
            slug=slug,
            title=title,
            type=page_type,
            tags=tags[:5],
            sources=[source_entry],
            body_md=body_md,
            review_status=review_status,
            source_doc_id=source_entry.get("doc_id", ""),
        )

    async def _llm_write_body(self, entity: ExtractedEntity, page_type: str) -> str:
        """让 LLM 按 AGENTS.md 骨架写页面正文

        返回不含 frontmatter 的 Markdown 正文。
        """
        system = (
            "你是 OpsKG Wiki 管理员。把运维知识编译为结构化 Markdown wiki 页面。"
            "严格遵循 AGENTS.md 规定的页面骨架。"
            "使用 [[slug]] 双向链接到相关概念。"
            "只输出 Markdown 正文，不要 YAML frontmatter，不要 ```md 包裹。"
        )
        prompt = self._build_writing_prompt(entity, page_type)
        text = await self._llm_complete(prompt, system=system, temperature=0.2)
        # 防御：剥离可能误加的代码块围栏
        text = self._strip_codefence(text)
        return text.strip()

    def _build_writing_prompt(self, entity: ExtractedEntity, page_type: str) -> str:
        """构造写作 prompt"""
        props_str = (
            "\n".join(f"- {k}: {v}" for k, v in entity.properties.items() if v)
            or "（无）"
        )
        evidence = (entity.evidence_span or "").strip()[:1200]
        type_label = {
            "incident": "故障页（必含：概述/成因分析/排查步骤/处置方案/来源）",
            "runbook": "操作手册页（必含：概述/影响分析/排查步骤/处置方案/来源）",
            "service": "服务页（必含：概述/架构/依赖/配置参数/来源）",
            "host": "主机页（必含：概述/角色/运行服务/来源）",
            "entity": "实体页（必含：概述/属性/关系/来源）",
            "concept": "概念页（必含：概述/原理/应用场景/来源）",
        }.get(page_type, "概念页（必含：概述/原理/应用场景/来源）")

        return f"""请把以下运维知识编译为一个 wiki 页面。

# 编译目标
- 名称：{entity.name}
- 实体类型：{entity.entity_type}
- 页面类型：{page_type}
- 页面骨架：{type_label}

# 已知属性
{props_str}

# 原文证据片段
{evidence}

# 编译要求
1. 严格按上述骨架输出 Markdown 章节
2. 在首次提及相关概念/服务/主机时，用 [[kebab-case-slug]] 形式建链
3. 不要编造未在证据中出现的具体数值
4. 「## 来源」章节引用本页来源即可
5. 标题用 `# {entity.name}` 起首
"""

    @staticmethod
    def _strip_codefence(text: str) -> str:
        """剥离误加的 ```md ... ``` 围栏"""
        t = text.strip()
        if t.startswith("```"):
            # 去首行（可能含语言标记）
            lines = t.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            return "\n".join(lines)
        return t

    def _template_body(self, entity: ExtractedEntity, page_type: str) -> str:
        """LLM 不可用时的模板化兜底正文"""
        props = entity.properties or {}
        props_lines = (
            "\n".join(f"- **{k}**: {v}" for k, v in props.items() if v)
            or "- （暂无属性）"
        )
        evidence = (entity.evidence_span or "").strip()

        sections: list[str] = [f"# {entity.name}", ""]
        sections.append("## 概述")
        sections.append(
            f"{entity.name} 是一个 {entity.entity_type.lower()} 实体，"
            f"由文档 `{entity.source_doc_id}` 编译而来。"
        )
        sections.append("")

        if page_type in ("entity", "service", "host", "concept"):
            sections.append("## 属性")
            sections.append(props_lines)
            sections.append("")

        if page_type in ("incident", "runbook"):
            sections.append("## 成因分析")
            sections.append(f"> 待补全。原始证据：{evidence[:200] or '（无）'}")
            sections.append("")
            sections.append("## 排查步骤")
            sections.append("1. （待 LLM 重编译补充）")
            sections.append("")
            sections.append("## 处置方案")
            sections.append("- （待 LLM 重编译补充）")
            sections.append("")

        if page_type == "service":
            sections.append("## 依赖")
            sections.append("- （待补全，建议建立 [[wikilink]] 到上下游服务）")
            sections.append("")

        sections.append("## 来源")
        sections.append(f"- doc_id: `{entity.source_doc_id}`")
        sections.append("")
        return "\n".join(sections)

    # ── 持久化与合并 ──

    def _save_page(self, page: WikiPage, *, force: bool) -> str:
        """保存页面到 VersionControl

        Returns:
            "created" | "updated" | "unchanged"
        """
        doc_key = _key_from_slug(page.slug)
        existing = self.vc.get_latest(doc_key)

        # 合并：已有页面 → 增量合并；否则新建
        if existing:
            new_md, stale_items = self._merge_existing(existing["content"], page)
            page.stale_items = stale_items
            # 内容未变 → 跳过
            if not force and self._content_equal(existing["content"], new_md):
                # 仍然刷新 backlink 以保证一致
                update_backlinks(page.slug, existing["content"])
                return "unchanged"
            md_to_save = new_md
            outcome = "updated"
        else:
            md_to_save = self._render_page_md(page, is_new=True)
            outcome = "created"

        self.vc.save_version(
            doc_key=doc_key,
            title=page.title,
            content=md_to_save,
            author="wiki-compiler",
            change_summary=self._change_summary(page, outcome),
        )
        # 维护 backlink
        update_backlinks(page.slug, md_to_save)

        # S12-2 反向回链：新建页面时，扫描已有页面正文，
        # 在提及新概念处插入 [[new_slug]]（AGENTS.md §五 5.b）
        if outcome == "created":
            try:
                back = self._backlink_existing_pages(page.slug, page.title)
                if back > 0:
                    logger.info(
                        "wiki_backlink_retrofitted",
                        slug=page.slug,
                        updated=back,
                    )
            except Exception as e:
                logger.warning(
                    "wiki_backlink_retrofit_failed", slug=page.slug, error=str(e)
                )
        return outcome

    def _merge_existing(
        self, existing_md: str, new_page: WikiPage
    ) -> tuple[str, list[str]]:
        """把新事实合并到已有页面

        策略（保守合并，避免覆盖人工编辑）：
        - 保留已有 frontmatter，仅追加 source、刷新 updated_at、review_status
        - 在正文末尾追加一个 "## 增量补充（{doc_id}）" 章节，附新来源证据
        - 标注 stale：若新页面有但旧页面没有的属性 → 标 stale（提示用户人工校验）

        Returns:
            (merged_md, stale_items)
        """
        # 解析旧 frontmatter
        meta, body = self._split_frontmatter(existing_md)
        new_meta = self._build_frontmatter_meta(new_page, is_new=False)

        # 合并 sources（去重 by doc_id）
        old_sources = meta.get("sources", []) or []
        old_doc_ids = {s.get("doc_id") for s in old_sources if isinstance(s, dict)}
        for s in new_page.sources:
            if s.get("doc_id") not in old_doc_ids:
                old_sources.append(s)
        new_meta["sources"] = old_sources

        # stale 检测：新页面属性在旧正文中是否出现过
        stale_items: list[str] = []
        new_props = self._extract_property_lines(new_page.body_md)
        for line in new_props:
            # 简化：如果该属性键在旧正文中未出现，记为 stale
            key = line.split(":", 1)[0].strip().strip("*").strip()
            if key and key not in body:
                stale_items.append(line)

        # 拼接正文：旧正文 + 增量补充章节
        append_section = self._render_increment_section(new_page)
        if append_section:
            body = body.rstrip() + "\n\n" + append_section + "\n"

        merged_md = self._assemble_md(new_meta, body)
        return merged_md, stale_items

    @staticmethod
    def _content_equal(a: str, b: str) -> bool:
        return a.strip() == b.strip()

    @staticmethod
    def _split_frontmatter(md: str) -> tuple[dict, str]:
        if not md.startswith("---"):
            return {}, md
        parts = md.split("---", 2)
        if len(parts) < 3:
            return {}, md
        try:
            meta = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            meta = {}
        return meta, parts[2].lstrip("\n")

    @staticmethod
    def _extract_property_lines(body_md: str) -> list[str]:
        """从「## 属性」章节抽 `- **key**: value` 行"""
        lines = body_md.splitlines()
        in_section = False
        out: list[str] = []
        for ln in lines:
            s = ln.strip()
            if s.startswith("## "):
                in_section = s.lower().startswith("## 属性") or s.lower().startswith(
                    "## properties"
                )
                continue
            if in_section and s.startswith("- "):
                out.append(s[2:])
        return out

    def _render_increment_section(self, page: WikiPage) -> str:
        """构造增量补充章节"""
        # 抽出正文里除 frontmatter/标题/来源之外的核心段落
        body = page.body_md.strip()
        if not body:
            return ""
        return (
            f"## 增量补充（来自 `{page.source_doc_id}`）\n\n"
            f"> 此章节由 wiki_compiler 增量合并，可能需要人工整合到上文。\n\n"
            f"{body}\n"
        )

    # ── Markdown 渲染 ──

    def _render_page_md(self, page: WikiPage, *, is_new: bool) -> str:
        """渲染整页 Markdown（frontmatter + body）"""
        meta = self._build_frontmatter_meta(page, is_new=is_new)
        return self._assemble_md(meta, page.body_md)

    @staticmethod
    def _build_frontmatter_meta(page: WikiPage, *, is_new: bool) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "slug": page.slug,
            "title": page.title,
            "type": page.type,
            "tags": page.tags,
            "sources": page.sources,
            "created_at": now if is_new else None,
            "updated_at": now,
            "review_status": page.review_status,
            "stale": bool(page.stale_items),
        }

    @staticmethod
    def _assemble_md(meta: dict, body: str) -> str:
        """拼装 frontmatter + 正文"""
        # 移除 None 值
        clean = {k: v for k, v in meta.items() if v is not None}
        fm = yaml.safe_dump(clean, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm}\n---\n\n{body.strip()}\n"

    @staticmethod
    def _change_summary(page: WikiPage, outcome: str) -> str:
        if outcome == "created":
            return f"新建 wiki 页面（来源 {page.source_doc_id}）"
        if page.stale_items:
            return f"增量合并并标注 {len(page.stale_items)} 项 stale（来源 {page.source_doc_id}）"
        return f"增量合并（来源 {page.source_doc_id}）"

    # ── S12-2 反向回链 ──

    def _backlink_existing_pages(
        self, new_slug: str, new_title: str, aliases: list[str] | None = None
    ) -> int:
        """新建页面时，扫描已有页面正文，在提及新概念处插入 [[new_slug]]

        实现 AGENTS.md §五 5.b："已有页面中提及新概念时回链到新页面"

        Args:
            new_slug: 新建页面的 slug
            new_title: 新建页面的标题
            aliases: 标题的别名（如英文/缩写），可选

        Returns:
            被更新（插入回链）的已有页面数
        """
        # 收集候选词：标题 + 别名，过滤过短词
        candidates = [new_title] + (aliases or [])
        candidates = [c for c in candidates if c and self._is_meaningful_token(c)]
        # 按长度降序（优先匹配长词，避免短词子串污染）
        candidates.sort(key=len, reverse=True)
        if not candidates:
            return 0

        # 列出所有已有 wiki 页面
        existing_pages = list_wiki_pages(limit=10000)
        updated_count = 0

        for page_meta in existing_pages:
            slug = page_meta["slug"]
            # 跳过自身、index
            if slug == new_slug or slug == "index":
                continue

            doc_key = page_meta["doc_key"]
            latest = self.vc.get_latest(doc_key)
            if not latest:
                continue
            original_content = latest["content"]

            # 已有指向 new_slug 的链接 → 跳过
            if f"[[{new_slug}" in original_content:
                continue

            new_content, matched = self._insert_wikilink_in_body(
                original_content, new_slug, candidates
            )
            if not matched:
                continue

            # 保存新版本
            self.vc.save_version(
                doc_key=doc_key,
                title=page_meta["title"],
                content=new_content,
                author="wiki-backlink-bot",
                change_summary=f"反向回链：插入 [[{new_slug}]]",
            )
            # 刷新被修改页面的出链 backlink
            update_backlinks(slug, new_content)
            updated_count += 1
            logger.info(
                "wiki_backlink_inserted",
                source=slug,
                target=new_slug,
            )

        return updated_count

    @staticmethod
    def _is_meaningful_token(text: str) -> bool:
        """判断候选词是否值得建链（避免过短词造成噪音）

        - 含 CJK 字符：长度 >= 2
        - 纯 ASCII：长度 >= 3
        """
        if not text:
            return False
        has_cjk = bool(_CJK_RE.search(text))
        return len(text) >= (2 if has_cjk else 3)

    def _insert_wikilink_in_body(
        self, content: str, new_slug: str, candidates: list[str]
    ) -> tuple[str, bool]:
        """在正文中找到首次提及候选词的位置，替换为 [[new_slug|原文]]

        保护策略：
        - 不动 frontmatter
        - 不动代码块（``` ... ```）
        - 不动已有的 [[wikilink]]（避免嵌套）
        - 不动表格行（避免破坏对齐）
        - 不动 H1 标题行（页面自己的标题）
        - 仅替换整个文档中的首次出现（AGENTS.md "首次提及建链"）
        """
        # 拆分 frontmatter（保留原始 frontmatter 字符串以便重组）
        front, body = self._split_frontmatter_raw(content)

        lines = body.split("\n")
        in_code_block = False
        matched = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            # 代码块开关
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            # 跳过 H1 标题、表格行、frontmatter 边界（保险）
            if stripped.startswith("# ") or stripped.startswith("|") or stripped == "---":
                continue

            for candidate in candidates:
                new_line, did_replace = self._replace_first_outside_wikilink(
                    line, candidate, f"[[{new_slug}|{candidate}]]"
                )
                if did_replace:
                    lines[i] = new_line
                    matched = True
                    break
            if matched:
                break  # 全文仅替换首次出现

        if not matched:
            return content, False

        new_body = "\n".join(lines)
        new_content = (front + new_body) if front else new_body
        return new_content, True

    @staticmethod
    def _split_frontmatter_raw(md: str) -> tuple[str, str]:
        """拆分为 (frontmatter 原始字符串含边界, body)

        - 有 frontmatter：返回 ("---\\n...\\n---\\n\\n", body)
        - 无 frontmatter：返回 ("", md)
        """
        if not md.startswith("---"):
            return "", md
        parts = md.split("---", 2)
        if len(parts) < 3:
            return "", md
        # parts[0] 是空串，parts[1] 是 yaml，parts[2] 是 body
        front = "---" + parts[1] + "---" + "\n"
        body = parts[2].lstrip("\n")
        return front, body

    @staticmethod
    def _replace_first_outside_wikilink(
        line: str, needle: str, replacement: str
    ) -> tuple[str, bool]:
        """在行中替换首次出现的 needle（不在 [[...]] 内），返回新行和是否替换

        英文使用 \\b 词边界，中文直接子串匹配。
        """
        # 收集已有 [[...]] 区间
        blocked: list[tuple[int, int]] = [
            (m.start(), m.end()) for m in WIKILINK_RE.finditer(line)
        ]

        # 构造正则
        if _CJK_RE.search(needle):
            pattern = re.escape(needle)
        else:
            pattern = r"\b" + re.escape(needle) + r"\b"

        for m in re.finditer(pattern, line):
            s, e = m.start(), m.end()
            # 跳过位于已有 wikilink 内的匹配
            if any(bs <= s and e <= be for bs, be in blocked):
                continue
            # 跳过紧邻 | 或 ] 的位置（避免在 wikilink 边界插入）
            if s > 0 and line[s - 1] in "|[":
                continue
            if e < len(line) and line[e] in "|]":
                continue
            new_line = line[:s] + replacement + line[e:]
            return new_line, True
        return line, False


# ────────── 全局单例 ──────────

_wc: WikiCompiler | None = None


def get_wiki_compiler() -> WikiCompiler:
    global _wc
    if _wc is None:
        _wc = WikiCompiler()
    return _wc
