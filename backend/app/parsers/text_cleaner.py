"""文本清洗与规范化管道

处理混乱格式的文档，在解析前进行预处理：
- 空白字符规范化
- 编码规范化（BOM / 换行符 / 全角空格）
- HTML 残留清理（标签去除 + 实体解码）
- 智能段落分割
- 标题规范化（非标准格式检测）
- 代码块保护（清洗期间保护，清洗后还原）
- 表格检测（Markdown 表格 + 对齐列）
- 重复行去除
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field


@dataclass
class CleanedDocument:
    """清洗后的文档"""
    original_text: str
    cleaned_text: str
    paragraphs: list[str] = field(default_factory=list)
    detected_headings: list[dict] = field(default_factory=list)
    detected_code_blocks: list[dict] = field(default_factory=list)
    detected_tables: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class TextCleaner:
    """文本清洗管道

    按顺序执行：编码规范化 → HTML 实体解码 → HTML 标签去除 →
    代码块保护（可选）→ 空白规范化 → 重复行去除 → 代码块还原 →
    智能段落分割 → 标题检测 → 表格检测。

    用法：
        cleaner = TextCleaner()
        cleaned = cleaner.clean(dirty_text)
        print(cleaned.cleaned_text)
        print(cleaned.paragraphs)
        print(cleaned.detected_headings)
    """

    # P0: 常见 HTML 实体映射表（先于 html.unescape 做精确替换，避免遗漏）
    HTML_ENTITIES = {
        '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"',
        '&#39;': "'", '&apos;': "'", '&nbsp;': ' ', '&emsp;': '  ',
        '&ensp;': ' ', '&thinsp;': ' ', '&zwnj;': '', '&zwj;': '',
        '&lrm;': '', '&rlm;': '', '&ndash;': '\u2013', '&mdash;': '\u2014',
        '&lsquo;': "'", '&rsquo;': "'", '&ldquo;': '"', '&rdquo;': '"',
        '&hellip;': '\u2026', '&trade;': '\u2122', '&reg;': '\u00ae', '&copy;': '\u00a9',
        '&bull;': '\u2022', '&middot;': '\u00b7', '&laquo;': '\u00ab', '&raquo;': '\u00bb',
    }

    # P0: 非标准标题检测模式（正则, 层级计算函数或固定层级）
    HEADING_PATTERNS = [
        # 编号标题：1. Title, 1.1 Title, 1.1.1 Title（注意：点号后可能直接跟空格）
        (re.compile(r'^(\d+(?:\.\d+)*)\.?\s+(.+)$', re.MULTILINE),
         lambda m: min(len(m.group(1).split('.')) + 1, 6)),
        # 方括号标题：[Title]
        (re.compile(r'^\[([^\]]+)\]$', re.MULTILINE), 2),
        # 分隔线标题：==== Title ====
        (re.compile(r'^={3,}\s*(.+?)\s*={3,}$', re.MULTILINE), 1),
        # 全大写行（可能是标题，含中文）
        (re.compile(r'^([A-Z\u4e00-\u9fff][A-Z\u4e00-\u9fff\s]{2,60})$', re.MULTILINE), 2),
    ]

    # P0: 代码块占位符模板（用于清洗期间保护代码块）
    _CODE_PLACEHOLDER = '__CODE_BLOCK_{}__'

    def clean(self, text: str, preserve_code_blocks: bool = True) -> CleanedDocument:
        """执行完整的文本清洗管道

        Args:
            text: 原始文本
            preserve_code_blocks: 是否在清洗期间保护代码块（默认 True）

        Returns:
            CleanedDocument 包含清洗后文本、段落、标题、代码块、表格和统计信息
        """
        stats = {}
        original = text

        # 1. 编码规范化（BOM / 换行符 / 全角空格）
        text = self._normalize_encoding(text)
        stats['encoding_normalized'] = text != original

        # 2. HTML 实体解码
        text = self._decode_html_entities(text)

        # 3. HTML 标签去除
        text = self._strip_html_tags(text)

        # 4. 代码块保护（提取代码块→占位符，避免后续清洗破坏代码）
        code_blocks: list[dict] = []
        if preserve_code_blocks:
            text, code_blocks = self._protect_code_blocks(text)

        # 5. 空白规范化（合并多余空行/空格，去除行首行尾空白）
        text = self._normalize_whitespace(text)

        # 6. 重复行去除
        text = self._remove_duplicate_lines(text)

        # 7. 代码块还原（占位符→原始代码块）
        if preserve_code_blocks:
            text = self._restore_code_blocks(text, code_blocks)

        # 8. 智能段落分割
        paragraphs = self._split_paragraphs(text)

        # 9. 标题检测
        headings = self._detect_headings(text)

        # 10. 表格检测
        tables = self._detect_tables(text)

        stats['original_length'] = len(original)
        stats['cleaned_length'] = len(text)
        stats['paragraph_count'] = len(paragraphs)
        stats['heading_count'] = len(headings)
        stats['code_block_count'] = len(code_blocks)
        stats['table_count'] = len(tables)

        return CleanedDocument(
            original_text=original,
            cleaned_text=text,
            paragraphs=paragraphs,
            detected_headings=headings,
            detected_code_blocks=code_blocks,
            detected_tables=tables,
            stats=stats,
        )

    # ─── 私有方法 ────────────────────────────────────────────────

    def _normalize_encoding(self, text: str) -> str:
        """规范化编码：去除 BOM、统一换行符、转换全角空格"""
        # P0: 去除 UTF-8 BOM（\ufeff），常见于 Windows 保存的 UTF-8 文件
        if text.startswith('\ufeff'):
            text = text[1:]
        # P0: 统一换行符：CRLF → LF, CR → LF
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # P0: 全角空格（U+3000，中文文档常见）→ 半角空格
        text = text.replace('\u3000', ' ')
        return text

    def _decode_html_entities(self, text: str) -> str:
        """解码 HTML 实体：先精确替换已知实体，再用标准库兜底"""
        for entity, char in self.HTML_ENTITIES.items():
            text = text.replace(entity, char)
        # P0: html.unescape 处理 &#NNN; / &#xNN; 等数字实体
        text = _html.unescape(text)
        return text

    def _strip_html_tags(self, text: str) -> str:
        """去除 HTML 标签，保留内容

        - <style> 和 <script> 块整体移除
        - 块级标签（div/p/li/tr/br/hr/h1-h6）替换为换行
        - 其余标签直接去除
        """
        # P0: 移除 <style> 和 <script> 及其内容
        text = re.sub(
            r'<style[^>]*>.*?</style>', '',
            text, flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            r'<script[^>]*>.*?</script>', '',
            text, flags=re.DOTALL | re.IGNORECASE,
        )
        # P0: 将标题标签转换为 Markdown 格式（# ## ### 等）
        for level in range(6, 0, -1):
            text = re.sub(
                rf'<h{level}[^>]*>(.*?)</h{level}>',
                lambda m, l=level: f'\n{"#" * l} {m.group(1).strip()}\n',
                text, flags=re.DOTALL | re.IGNORECASE,
            )
        # P0: 将 <pre><code> 转换为 Markdown 代码块
        text = re.sub(
            r'<pre[^>]*><code[^>]*>(.*?)</code></pre>',
            r'\n```\n\1\n```\n',
            text, flags=re.DOTALL | re.IGNORECASE,
        )
        # P0: 常见块级标签替换为换行，保留段落结构
        text = re.sub(
            r'</?(?:div|p|li|tr|br|hr|ul|ol)[^>]*>',
            '\n', text, flags=re.IGNORECASE,
        )
        # P0: 去除其余标签
        text = re.sub(r'<[^>]+>', '', text)
        return text

    def _protect_code_blocks(self, text: str) -> tuple[str, list[dict]]:
        """保护代码块：提取代码块并用占位符替换，避免被清洗破坏

        支持两种代码块格式：
        - 反引号代码块：```lang\\ncode\\n```
        - 缩进代码块：以 4 空格开头的连续行
        """
        code_blocks: list[dict] = []

        # P0: 反引号代码块（```...```）
        def _save_backtick(m):
            idx = len(code_blocks)
            lang = m.group(1) or ''
            code = m.group(2)
            code_blocks.append({'type': 'backtick', 'lang': lang, 'code': code})
            return self._CODE_PLACEHOLDER.format(idx)

        text = re.sub(
            r'```(\w*)\n(.*?)```',
            _save_backtick, text, flags=re.DOTALL,
        )

        # P0: 缩进代码块（4 空格开头连续行）
        def _save_indent(m):
            idx = len(code_blocks)
            code_blocks.append({'type': 'indent', 'lang': '', 'code': m.group(1)})
            return self._CODE_PLACEHOLDER.format(idx)

        text = re.sub(
            r'((?:^    .*\n)+)',
            _save_indent, text, flags=re.MULTILINE,
        )

        return text, code_blocks

    def _restore_code_blocks(self, text: str, code_blocks: list[dict]) -> str:
        """还原代码块：将占位符替换回原始代码块"""
        for i, block in enumerate(code_blocks):
            placeholder = self._CODE_PLACEHOLDER.format(i)
            if block['type'] == 'backtick':
                lang = block['lang']
                replacement = f'\n```{lang}\n{block["code"]}\n```\n'
            else:
                replacement = f'\n```\n{block["code"]}\n```\n'
            text = text.replace(placeholder, replacement)
        return text

    def _normalize_whitespace(self, text: str) -> str:
        """规范化空白字符

        - 合并 3+ 连续空行为双空行
        - 去除每行首尾空白（保留缩进代码块）
        - 合并行内多余空格
        """
        # P0: 合并 3+ 连续空行 → 双空行（保留段落分隔）
        text = re.sub(r'\n{3,}', '\n\n', text)
        # P0: 去除行首行尾空白
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)
        # P0: 合并行内 2+ 连续空格 → 单空格
        text = re.sub(r' {2,}', ' ', text)
        # P0: 去除首尾空行
        text = text.strip()
        return text

    def _remove_duplicate_lines(self, text: str) -> str:
        """去除连续重复行（仅处理非空行）"""
        lines = text.split('\n')
        result: list[str] = []
        prev: str | None = None
        for line in lines:
            stripped = line.strip()
            # P0: 仅检查非空行；空行不做去重（保留段落分隔）
            if stripped and stripped == prev:
                continue
            result.append(line)
            prev = stripped if stripped else None
        return '\n'.join(result)

    def _split_paragraphs(self, text: str) -> list[str]:
        """智能段落分割

        按双换行分割，然后合并过短的段落（< 50 字符且无句末标点）。
        标题和代码块不参与合并。
        """
        raw_paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]

        merged: list[str] = []
        buffer = ''
        for p in paragraphs:
            is_heading = self._looks_like_heading(p)
            is_code = p.startswith('```') or p.startswith('    ')
            if is_heading or is_code:
                if buffer:
                    merged.append(buffer.strip())
                    buffer = ''
                merged.append(p)
            elif len(p) < 50 and not p.endswith(('.', '\u3002', '!', '\uff01', '?', '\uff1f')):
                buffer += (' ' if buffer else '') + p
            else:
                if buffer:
                    merged.append(buffer.strip())
                    buffer = ''
                merged.append(p)
        if buffer:
            merged.append(buffer.strip())
        return merged

    def _looks_like_heading(self, text: str) -> bool:
        """检测文本是否具有标题特征

        规则：
        - 以 # 开头的 Markdown 标题
        - 编号标题（如 "1.1 概述"）
        - 短文本（< 80 字符）且不含标点
        """
        if re.match(r'^#{1,6}\s', text):
            return True
        if re.match(r'^\d+(?:\.\d+)*\s+\S', text):
            return True
        if len(text) < 80 and not re.search(r'[.,;:!?\u3002\uff0c\uff1b\uff1a\uff01\uff1f]', text):
            return True
        return False

    def _detect_headings(self, text: str) -> list[dict]:
        """检测文档中的标题

        支持：
        - 标准 Markdown 标题（# ~ ######）
        - 编号标题（1. / 1.1 / 1.1.1）
        - 方括号标题（[标题]）
        - 分隔线标题（=== 标题 ===）
        - 全大写行（含中文）
        """
        headings: list[dict] = []
        lines = text.split('\n')
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # P0: 标准 Markdown 标题
            m = re.match(r'^(#{1,6})\s+(.+)$', line)
            if m:
                headings.append({
                    'line': i,
                    'text': m.group(2),
                    'level': len(m.group(1)),
                    'type': 'markdown',
                })
                continue

            # P0: 非标准标题模式
            for pattern, level_fn in self.HEADING_PATTERNS:
                m = pattern.match(line)
                if m:
                    level = level_fn(m) if callable(level_fn) else level_fn
                    text_groups = m.groups()
                    heading_text = text_groups[-1] if text_groups else line
                    headings.append({
                        'line': i,
                        'text': heading_text,
                        'level': min(level, 6),
                        'type': 'detected',
                    })
                    break

        return headings

    def _detect_tables(self, text: str) -> list[dict]:
        """检测类表格结构

        支持：
        - Markdown 表格（| col | col |\\n| --- | --- |）
        - 对齐列表格（空格分隔的列对齐文本）
        """
        tables: list[dict] = []

        # P0: Markdown 表格（含标题行 + 分隔行 + 数据行）
        pattern = re.compile(
            r'^\|.+\|\n\|[-| :]+\|\n(?:\|.+\|\n?)+', re.MULTILINE,
        )
        for m in pattern.finditer(text):
            tables.append({
                'type': 'markdown',
                'span': m.span(),
                'text': m.group(),
            })

        # P0: 对齐列表格（连续 2+ 行，每行含 2+ 双空格分隔）
        lines = text.split('\n')
        i = 0
        while i < len(lines) - 1:
            if lines[i].count('  ') >= 2 and lines[i + 1].count('  ') >= 2:
                table_lines = []
                j = i
                while j < len(lines) and lines[j].count('  ') >= 2:
                    table_lines.append(lines[j])
                    j += 1
                if len(table_lines) >= 2:
                    tables.append({
                        'type': 'aligned',
                        'start_line': i,
                        'end_line': j,
                        'lines': table_lines,
                    })
                i = j
            else:
                i += 1

        return tables
