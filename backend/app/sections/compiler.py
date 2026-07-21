"""章节 LLM 编译器

将 Section 编译为 Compiled Section .md 文件。
每个章节独立调用 LLM，产出结构化 Markdown 文件。

编译产物包含:
- YAML frontmatter（元数据）
- 语义角色标注
- 结构化正文
- HTML 注释标签（entities + relations + attachment_refs）
- [[wikilink]] 内链

这是整个系统中最核心的 LLM 调用，决定了后续所有处理的质量。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.sections.splitter import Section


@dataclass
class CompiledSection:
    """编译后的章节（解析后的 Compiled Section .md）"""
    section_id: str
    source_doc_id: str
    title: str
    semantic_role: str
    content: str                    # 正文（不含 HTML 注释标签）
    entities: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    attachment_refs: list[dict] = field(default_factory=list)
    wikilinks: list[str] = field(default_factory=list)
    version: int = 1
    compiled_at: str = ""
    checksum: str = ""
    raw_markdown: str = ""          # 完整编译产物（含注释标签）


@dataclass
class SectionCompileResult:
    """章节编译结果"""
    compiled: CompiledSection | None
    markdown: str                   # 完整的 .md 文件内容
    file_path: str                  # 保存路径
    success: bool
    error: str | None = None
    llm_used: bool = False
    elapsed: float = 0.0


class SectionCompiler:
    """章节 LLM 编译器

    对每个 Section 独立调用 LLM，生成 Compiled Section .md 文件。
    支持:
    - 有 LLM: 完整编译（格式清洗 + 语义理解 + 结构化重写）
    - 无 LLM: 模板兜底（基本格式规范化）
    """

    # 编译 Prompt 模板
    COMPILE_PROMPT = """你是一个运维知识编译专家。请将以下文档章节内容编译为结构化的运维知识片段。

## 上下文
- 文档标题: {doc_title}
- 前一章节: {context_before}
- 后一章节: {context_after}

## 原始内容
{raw_content}

## 编译要求

1. **语义角色确认**: 判断本章节属于以下哪种角色，填入 semantic_role:
   - overview: 概述/背景/简介
   - cause: 成因/原因分析
   - troubleshoot: 排查/诊断步骤
   - solution: 处置/修复方案
   - config: 配置/参数说明
   - steps: 操作步骤/流程
   - warning: 注意事项/警告
   - reference: 参考/引用

2. **结构化重写**: 按语义角色组织内容，使格式标准化:
   - 保留原文的核心信息和技术细节
   - 使语言简洁、专业
   - 表格、代码块、列表保持原格式
   - 补充缺失的上下文（如代码块的语言标注）

3. **实体提取**: 在本章节中提取关键知识实体，用 YAML 格式标注:
   - 实体类型: Cause | Solution | Concept | Parameter | Command | Procedure | Symptom | Incident
   - 每个实体包含: type, name, slug(kebab-case), definition(一句话)

4. **关系提取**: 标注实体间的逻辑关系:
   - 关系类型: CAUSES | MANIFESTS_AS | RESOLVED_BY | HAS_STEP | CONFIGURES | RELATES_TO

5. **内链插入**: 在正文中提及关键概念时，使用 [[slug]] 格式插入链接

6. **附件引用**: 保留已有的附件引用（<!-- attachment_ref: xxx --> 和 📷/📎 标记）

## 输出格式

严格按照以下格式输出，不要有任何额外内容:

```markdown
---
section_id: {section_id}
source_doc_id: {source_doc_id}
title: {title}
semantic_role: <role>
compiled_at: {compiled_at}
version: {version}
---
<!-- semantic_role: <role> -->
<!-- entities:
  - type: <type>
    name: "<name>"
    slug: <kebab-case-slug>
    definition: "<definition>"
  ...更多实体
-->
<!-- relations:
  - type: <relation_type>
    from: <source-slug>
    to: <target-slug>
  ...更多关系
-->

## {title}

<正文内容>
```

## 正文模板（按角色）

### overview
简要说明本章节的主题和范围，突出核心内容。

### cause
每个成因独立成段，格式: **成因名称** — 详细描述，说明为何会导致该问题。

### troubleshoot
按步骤编号，每步包含: 目的 + 具体命令/操作 + 预期结果。

### solution
每个方案独立成段，格式: **方案名称** — 适用场景 + 具体操作 + 注意事项。

### config
参数说明使用表格格式: | 参数 | 默认值 | 说明 |

### steps
按操作顺序编号，每步清晰说明操作内容。

### warning
使用 > 引用块格式突出重要内容。

### reference
使用列表格式，每个引用包含名称和简要说明。
"""

    def __init__(
        self,
        compiled_dir: str = "data/compiled",
        llm_call: Any | None = None,
    ):
        self.compiled_dir = Path(compiled_dir)
        self.compiled_dir.mkdir(parents=True, exist_ok=True)
        self._llm_call = llm_call

    async def compile(
        self,
        section: Section,
        doc_title: str = "",
        force: bool = False,
        use_llm: bool = True,
    ) -> SectionCompileResult:
        """编译单个章节

        Args:
            section: 待编译的章节
            doc_title: 来源文档标题
            force: 是否强制重新编译（忽略已有编译产物）
            use_llm: 是否使用 LLM（False 时使用模板兜底）

        Returns:
            SectionCompileResult
        """
        import time
        started = time.monotonic()

        file_path = str(self.compiled_dir / f'{section.section_id}.md')

        # 检查是否已有编译产物
        if not force and Path(file_path).exists():
            compiled = self._parse_compiled(file_path, section.section_id)
            if compiled:
                return SectionCompileResult(
                    compiled=compiled,
                    markdown=compiled.raw_markdown,
                    file_path=file_path,
                    success=True,
                    elapsed=time.monotonic() - started,
                )

        # 编译
        if use_llm and self._llm_call is not None:
            result = await self._compile_with_llm(section, doc_title, file_path)
        else:
            result = self._compile_with_template(section, file_path)

        result.elapsed = time.monotonic() - started
        return result

    async def _compile_with_llm(
        self, section: Section, doc_title: str, file_path: str,
    ) -> SectionCompileResult:
        """使用 LLM 编译章节"""
        now = datetime.now(timezone.utc).isoformat()

        prompt = self.COMPILE_PROMPT.format(
            doc_title=doc_title or section.title,
            context_before=section.context_before or '无',
            context_after=section.context_after or '无',
            raw_content=section.raw_content,
            section_id=section.section_id,
            source_doc_id=section.source_doc_id,
            title=section.title,
            compiled_at=now,
            version=section.compiled_version + 1,
        )

        try:
            response = await self._llm_call(prompt)
            markdown = self._clean_llm_response(response)

            # 验证编译产物
            compiled = self._parse_compiled_from_text(markdown, section.section_id)
            if compiled is None:
                # LLM 输出格式不正确，降级为模板
                return self._compile_with_template(section, file_path)

            compiled.raw_markdown = markdown
            compiled.checksum = hashlib.sha256(markdown.encode()).hexdigest()
            compiled.version = section.compiled_version + 1
            compiled.compiled_at = now

            # 保存到文件
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(markdown)

            return SectionCompileResult(
                compiled=compiled,
                markdown=markdown,
                file_path=file_path,
                success=True,
                llm_used=True,
            )
        except Exception as e:
            # LLM 调用失败，降级为模板
            return self._compile_with_template(section, file_path, error=str(e))

    def _compile_with_template(
        self, section: Section, file_path: str, error: str | None = None,
    ) -> SectionCompileResult:
        """模板兜底编译（无 LLM 或 LLM 失败时）"""
        now = datetime.now(timezone.utc).isoformat()

        role = section.semantic_role or 'general'
        markdown = self._build_template_markdown(section, role, now)

        compiled = self._parse_compiled_from_text(markdown, section.section_id)
        if compiled is None:
            compiled = CompiledSection(
                section_id=section.section_id,
                source_doc_id=section.source_doc_id,
                title=section.title,
                semantic_role=role,
                content=section.raw_content,
                version=section.compiled_version + 1,
                compiled_at=now,
                checksum=hashlib.sha256(markdown.encode()).hexdigest(),
                raw_markdown=markdown,
            )

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(markdown)

        return SectionCompileResult(
            compiled=compiled,
            markdown=markdown,
            file_path=file_path,
            success=True,
            llm_used=False,
            error=error,
        )

    def _build_template_markdown(
        self, section: Section, role: str, now: str,
    ) -> str:
        """构建模板兜底的 Markdown"""
        content = section.raw_content.strip()
        return f"""---
section_id: {section.section_id}
source_doc_id: {section.source_doc_id}
title: {section.title}
semantic_role: {role}
compiled_at: {now}
version: {section.compiled_version + 1}
---

<!-- semantic_role: {role} -->
<!-- entities: [] -->
<!-- relations: [] -->

## {section.title}

{content}
"""

    def _clean_llm_response(self, response: str) -> str:
        """清理 LLM 响应中的多余内容"""
        # 移除可能的 markdown 代码块包裹
        response = response.strip()
        if response.startswith('```markdown'):
            response = response[len('```markdown'):]
        elif response.startswith('```md'):
            response = response[len('```md'):]
        elif response.startswith('```'):
            response = response[3:]
        if response.endswith('```'):
            response = response[:-3]
        return response.strip()

    def _parse_compiled(self, file_path: str, section_id: str) -> CompiledSection | None:
        """从文件解析编译产物"""
        try:
            with open(file_path, encoding='utf-8') as f:
                markdown = f.read()
            return self._parse_compiled_from_text(markdown, section_id)
        except Exception:
            return None

    def _parse_compiled_from_text(
        self, markdown: str, section_id: str,
    ) -> CompiledSection | None:
        """从文本解析编译产物"""
        # 解析 YAML frontmatter
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', markdown, re.DOTALL)
        if not fm_match:
            return None

        fm = self._parse_yaml_simple(fm_match.group(1))
        body = markdown[fm_match.end():]

        # 解析 HTML 注释标签
        entities = self._extract_yaml_block(body, 'entities')
        relations = self._extract_yaml_block(body, 'relations')
        attachment_refs = self._extract_yaml_block(body, 'attachment_refs')

        # 提取 semantic_role
        role_match = re.search(r'<!--\s*semantic_role:\s*(\w+)\s*-->', body)
        semantic_role = role_match.group(1) if role_match else 'general'

        # 提取正文（去除注释标签）
        content = self._strip_comments(body)

        # 提取 wikilinks
        wikilinks = re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', content)

        return CompiledSection(
            section_id=section_id,
            source_doc_id=fm.get('source_doc_id', ''),
            title=fm.get('title', ''),
            semantic_role=semantic_role or fm.get('semantic_role', 'general'),
            content=content.strip(),
            entities=entities if isinstance(entities, list) else [],
            relations=relations if isinstance(relations, list) else [],
            attachment_refs=attachment_refs if isinstance(attachment_refs, list) else [],
            wikilinks=wikilinks,
            version=fm.get('version', 1),
            compiled_at=fm.get('compiled_at', ''),
            raw_markdown=markdown,
        )

    def _parse_yaml_simple(self, text: str) -> dict[str, Any]:
        """简单的 YAML frontmatter 解析"""
        result: dict[str, Any] = {}
        for line in text.strip().split('\n'):
            if ':' in line:
                key, _, value = line.partition(':')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                result[key] = value
        return result

    def _extract_yaml_block(self, text: str, tag: str) -> Any:
        """从 HTML 注释中提取 YAML 块"""
        pattern = rf'<!--\s*{tag}:\s*\n?(.*?)\s*-->'
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            return []

        try:
            return yaml_safe_load(match.group(1))
        except Exception:
            return []

    def _strip_comments(self, text: str) -> str:
        """移除 HTML 注释标签"""
        return re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL).strip()


def yaml_safe_load(text: str) -> Any:
    """安全的 YAML 解析，不依赖 pyyaml"""
    # 简单的 YAML 列表解析
    text = text.strip()
    if not text:
        return []

    # 尝试解析列表
    items = []
    current_item: dict[str, Any] | None = None
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('- type:') or stripped.startswith('- type :'):
            if current_item:
                items.append(current_item)
            current_item = {}
            _, value = stripped.split(':', 1) if ':' in stripped else ('', '')
            current_item['type'] = value.strip().strip('"').strip("'")
        elif current_item is not None and ':' in stripped:
            key, _, value = stripped.partition(':')
            key = key.strip().lstrip('- ')
            value = value.strip().strip('"').strip("'")
            if key:
                current_item[key] = value
    if current_item:
        items.append(current_item)

    return items if items else text