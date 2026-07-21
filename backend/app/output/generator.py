"""标准化输出文档生成器（P4-4）

基于 Wiki 内容和文档模板，使用 LLM 生成标准化输出文档。

流程:
1. 用户选择文档模板（如"前台操作手册"）
2. 系统收集相关 Wiki 页面（按关键词/标签/类型）
3. LLM 理解 Wiki 内容，按模板章节结构自组织编排
4. 生成最终 Markdown 文档
5. 保存到 data/output/ 目录
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from app.output.templates import (
    DocumentTemplate,
    TemplateSection,
    load_template,
)

logger = structlog.get_logger()


@dataclass
class DocumentSource:
    """文档来源（Wiki 页面引用）"""
    slug: str
    title: str
    section: str            # 在文档中对应的章节
    role: str               # primary | supplementary | reference
    content_snippet: str = ""


@dataclass
class GeneratedDocument:
    """生成的文档"""
    doc_id: str
    template_id: str
    title: str
    content: str                    # 完整 Markdown 内容
    generated_at: str
    sources: list[DocumentSource] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    file_path: str = ""


@dataclass
class GenerateResult:
    """文档生成结果"""
    document: GeneratedDocument | None
    success: bool
    error: str | None = None
    llm_used: bool = False
    warnings: list[str] = field(default_factory=list)


class DocumentGenerator:
    """标准化输出文档生成器

    使用 LLM 理解 Wiki 内容，按模板结构自组织编排生成文档。
    支持:
    - 有 LLM: 智能编排（语义理解 + 章节自组织）
    - 无 LLM: 模板拼接（按标签/类型匹配章节）
    """

    GENERATE_PROMPT = """你是一个技术文档编写专家。请根据以下要求生成一份标准化文档。

## 文档模板
- 模板名称: {template_name}
- 文档类型: {template_type}
- 目标读者: {target_audience}

## 章节结构要求
{section_structure}

## 相关 Wiki 知识库内容
{wiki_content}

## 生成要求
1. 严格按上述章节结构组织内容
2. 从 Wiki 内容中提取相关信息填入对应章节
3. 如果某章节在 Wiki 中没有对应内容，标注"待补充"并说明原因
4. 保持技术细节准确，不编造信息
5. 使用 [[slug]] 格式引用相关 Wiki 页面
6. 代码块标注语言类型
7. 表格用于参数/配置说明
8. 重要信息使用 > 引用块突出

## 输出格式
直接输出 Markdown 格式的完整文档，从 `# {document_title}` 开始。
不要输出任何解释性文字。"""

    # 文档标题提取 Prompt
    TITLE_PROMPT = """根据以下文档模板和目标系统信息，生成一个简洁的文档标题。
模板: {template_name}
目标系统: {system_name}
只输出标题，不要任何其他内容。"""

    def __init__(
        self,
        output_dir: str = "data/output",
        llm_call: Any | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._llm_call = llm_call

    async def generate(
        self,
        template_id: str,
        wiki_pages: list[dict],
        *,
        system_name: str = "",
        custom_title: str = "",
        use_llm: bool = True,
        max_pages: int = 50,
    ) -> GenerateResult:
        """生成标准化输出文档

        Args:
            template_id: 模板 ID（如 'operations_manual'）
            wiki_pages: 相关 Wiki 页面 [{slug, title, type, body_md, tags, ...}]
            system_name: 目标系统名称（用于标题生成）
            custom_title: 自定义标题（覆盖自动生成）
            use_llm: 是否使用 LLM
            max_pages: 最大 Wiki 页面数（防止 token 溢出）

        Returns:
            GenerateResult
        """
        # 1. 加载模板
        template = load_template(template_id)
        if template is None:
            return GenerateResult(
                document=None, success=False,
                error=f'模板不存在: {template_id}',
            )

        # 2. 限制页面数量
        if len(wiki_pages) > max_pages:
            wiki_pages = wiki_pages[:max_pages]

        # 3. 构建章节结构描述
        section_structure = self._build_section_structure(template.sections)

        # 4. 构建 Wiki 内容摘要
        wiki_content = self._build_wiki_summary(wiki_pages)

        # 5. 生成文档标题
        if custom_title:
            title = custom_title
        elif system_name and self._llm_call:
            title = await self._generate_title(template.name, system_name)
        else:
            title = f'{system_name or "系统"}{template.name}'

        now = datetime.now(timezone.utc).isoformat()

        # 6. 生成文档内容
        if use_llm and self._llm_call:
            content = await self._generate_with_llm(
                template, section_structure, wiki_content, title,
            )
            llm_used = True
        else:
            content = self._generate_by_template(
                template, wiki_pages, title,
            )
            llm_used = False

        if not content:
            return GenerateResult(
                document=None, success=False,
                error='生成失败：LLM 返回空内容',
                llm_used=llm_used,
            )

        # 7. 构建来源映射
        sources = self._build_sources(wiki_pages, template)

        # 8. 生成文档 ID
        doc_id = hashlib.sha256(
            f'{template_id}:{title}:{now}'.encode()
        ).hexdigest()[:12]

        # 9. 保存到文件
        file_path = str(self.output_dir / f'{doc_id}.md')
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        # 10. 统计
        stats = {
            'source_pages': len(wiki_pages),
            'sections': len(template.sections),
            'required_sections': sum(1 for s in template.sections if s.required),
            'char_count': len(content),
            'line_count': content.count('\n'),
            'wikilink_count': len(re.findall(r'\[\[([^\]]+)\]\]', content)),
        }

        return GenerateResult(
            document=GeneratedDocument(
                doc_id=doc_id,
                template_id=template_id,
                title=title,
                content=content,
                generated_at=now,
                sources=sources,
                stats=stats,
                file_path=file_path,
            ),
            success=True,
            llm_used=llm_used,
        )

    async def _generate_title(
        self, template_name: str, system_name: str,
    ) -> str:
        """LLM 生成文档标题"""
        prompt = self.TITLE_PROMPT.format(
            template_name=template_name,
            system_name=system_name,
        )
        try:
            response = await self._llm_call(prompt)
            return response.strip().strip('"').strip("'")[:100]
        except Exception:
            return f'{system_name}{template_name}'

    async def _generate_with_llm(
        self,
        template: DocumentTemplate,
        section_structure: str,
        wiki_content: str,
        title: str,
    ) -> str:
        """LLM 生成文档"""
        prompt = self.GENERATE_PROMPT.format(
            template_name=template.name,
            template_type=template.document_type,
            target_audience=template.target_audience,
            section_structure=section_structure,
            wiki_content=wiki_content,
            document_title=title,
        )
        try:
            response = await self._llm_call(prompt)
            return self._clean_response(response)
        except Exception as e:
            logger.warning("document_generate_llm_failed", error=str(e))
            return ""

    def _generate_by_template(
        self,
        template: DocumentTemplate,
        wiki_pages: list[dict],
        title: str,
    ) -> str:
        """模板拼接生成文档（无 LLM 兜底）"""
        lines: list[str] = []
        lines.append(f'# {title}\n')
        lines.append(f'> 生成时间: {datetime.now(timezone.utc).isoformat()}')
        lines.append(f'> 模板: {template.name}')
        lines.append(f'> 目标读者: {template.target_audience}')
        lines.append('')

        # 按章节组织
        for section in template.sections:
            lines.append(f'## {section.title}')
            lines.append('')

            if section.required:
                # 查找匹配的 Wiki 内容
                section_content = self._match_section_content(
                    section, wiki_pages,
                )
                if section_content:
                    lines.append(section_content)
                else:
                    lines.append('> 待补充 - 知识库中暂无相关内容')
            else:
                lines.append('> 可选章节，可根据需要补充')

            lines.append('')

        # 附录：来源页面
        lines.append('## 来源页面')
        lines.append('')
        for p in wiki_pages[:20]:
            lines.append(f'- [[{p.get("slug", "")}|{p.get("title", "")}]]')
        lines.append('')

        return '\n'.join(lines)

    def _match_section_content(
        self, section: TemplateSection, wiki_pages: list[dict],
    ) -> str:
        """为章节匹配 Wiki 内容"""
        # 关键词匹配
        keywords = self._extract_keywords(section.title)
        matched: list[dict] = []
        for p in wiki_pages:
            body = p.get('body_md', '') or p.get('content', '')
            title = p.get('title', '')
            if any(kw in title.lower() or kw in body.lower() for kw in keywords):
                matched.append(p)

        if not matched:
            # 回退到类型匹配
            type_map = {
                '故障分类总览': 'incident',
                '故障排查手册': 'incident',
                '处置方案': 'incident',
                '日常操作': 'runbook',
                '变更操作': 'runbook',
                '应急操作': 'runbook',
                '部署步骤': 'runbook',
                '配置说明': 'service',
                '组件说明': 'service',
                '架构': 'concept',
            }
            target_type = type_map.get(section.title, '')
            if target_type:
                matched = [
                    p for p in wiki_pages
                    if p.get('type', '') == target_type
                ]

        if not matched:
            return ''

        # 拼接内容
        parts: list[str] = []
        for p in matched[:section.max_items or 5]:
            slug = p.get('slug', '')
            title = p.get('title', '')
            body = p.get('body_md', '') or p.get('content', '')
            # 取前 500 字符
            snippet = body[:500]
            if len(body) > 500:
                snippet += f'\n\n> 详见 [[{slug}|{title}]]'
            parts.append(f'### {title}\n\n{snippet}')

        return '\n\n'.join(parts)

    @staticmethod
    def _extract_keywords(title: str) -> list[str]:
        """从章节标题提取关键词"""
        # 常见关键词映射
        keyword_map = {
            '概述': ['概述', '介绍', '简介', 'overview'],
            '故障': ['故障', '错误', '异常', 'incident', 'error', 'issue'],
            '排查': ['排查', '诊断', 'troubleshoot', 'debug'],
            '处置': ['处置', '修复', '解决', 'solution', 'fix', 'resolve'],
            '部署': ['部署', '安装', 'deploy', 'install'],
            '配置': ['配置', '参数', 'config', 'parameter'],
            '操作': ['操作', '步骤', '流程', 'procedure', 'step'],
            '架构': ['架构', '设计', 'architecture', 'design'],
            '登录': ['登录', '认证', 'login', 'auth'],
            '验证': ['验证', '测试', 'verify', 'test', 'check'],
            '回滚': ['回滚', '恢复', 'rollback', 'restore'],
            '规划': ['规划', '容量', 'capacity', 'planning'],
            '依赖': ['依赖', '关系', 'dependency'],
            '数据': ['数据', '流', 'data', 'flow'],
            '注意': ['注意', '警告', 'warning', 'caution'],
            '附录': ['附录', '参考', 'appendix', 'reference'],
        }
        for key, kws in keyword_map.items():
            if key in title:
                return kws
        return [title.lower()]

    def _build_section_structure(
        self, sections: list[TemplateSection],
    ) -> str:
        """构建章节结构描述"""
        lines: list[str] = []
        for i, s in enumerate(sections, 1):
            required = '必填' if s.required else '可选'
            type_label = {
                'text': '文本',
                'list': '列表',
                'table': '表格',
                'code': '代码块',
                'checklist': '检查清单',
            }.get(s.section_type, s.section_type)
            lines.append(f'{i}. **{s.title}**（{type_label}，{required}）')
            if s.description:
                lines.append(f'   - {s.description}')
            if s.max_items:
                lines.append(f'   - 最多 {s.max_items} 条')
        return '\n'.join(lines)

    def _build_wiki_summary(self, wiki_pages: list[dict]) -> str:
        """构建 Wiki 内容摘要（供 LLM 使用）"""
        parts: list[str] = []
        for p in wiki_pages:
            slug = p.get('slug', '')
            title = p.get('title', slug)
            ptype = p.get('type', 'concept')
            tags = p.get('tags', [])
            body = p.get('body_md', '') or p.get('content', '')
            # 截断正文（每页最多 2000 字符）
            if len(body) > 2000:
                body = body[:2000] + '\n\n> [内容已截断...]'

            parts.append(
                f'---\n'
                f'### {title}\n'
                f'- slug: `{slug}`\n'
                f'- type: {ptype}\n'
                f'- tags: {", ".join(tags[:5])}\n\n'
                f'{body}\n'
            )

        return '\n'.join(parts)

    def _build_sources(
        self,
        wiki_pages: list[dict],
        template: DocumentTemplate,
    ) -> list[DocumentSource]:
        """构建来源映射"""
        sources: list[DocumentSource] = []
        for p in wiki_pages:
            # 尝试匹配章节
            matched_section = '附录'
            for section in template.sections:
                keywords = self._extract_keywords(section.title)
                title = p.get('title', '').lower()
                body = (p.get('body_md', '') or p.get('content', '')).lower()
                if any(kw in title or kw in body for kw in keywords):
                    matched_section = section.title
                    break

            sources.append(DocumentSource(
                slug=p.get('slug', ''),
                title=p.get('title', ''),
                section=matched_section,
                role='primary',
                content_snippet=(p.get('body_md', '') or p.get('content', ''))[:200],
            ))
        return sources

    @staticmethod
    def _clean_response(response: str) -> str:
        """清理 LLM 响应"""
        response = response.strip()
        # 移除可能的 markdown 代码块包裹
        if response.startswith('```markdown'):
            response = response[len('```markdown'):]
        elif response.startswith('```md'):
            response = response[len('```md'):]
        elif response.startswith('```'):
            response = response[3:]
        if response.endswith('```'):
            response = response[:-3]
        return response.strip()

    def list_generated(self) -> list[dict]:
        """列出已生成的文档"""
        docs: list[dict] = []
        if not self.output_dir.exists():
            return docs
        for f in sorted(
            self.output_dir.glob('*.md'),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        ):
            stat = f.stat()
            docs.append({
                'doc_id': f.stem,
                'file_name': f.name,
                'size': stat.st_size,
                'generated_at': datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc,
                ).isoformat(),
            })
        return docs

    def get_document(self, doc_id: str) -> GeneratedDocument | None:
        """获取已生成的文档"""
        file_path = self.output_dir / f'{doc_id}.md'
        if not file_path.exists():
            return None
        with open(file_path, encoding='utf-8') as f:
            content = f.read()

        # 从文件名解析基本信息
        return GeneratedDocument(
            doc_id=doc_id,
            template_id='',
            title='',
            content=content,
            generated_at='',
            file_path=str(file_path),
        )
