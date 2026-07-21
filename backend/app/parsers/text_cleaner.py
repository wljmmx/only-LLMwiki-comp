"""文本清洗与规范化管道

处理混乱格式的文档，在解析前进行预处理：
- Word 特有噪声清洗（smart quotes / 分页符 / 域代码 / 可选连字符 / 制表符）
- 空白字符规范化
- 编码规范化（BOM / 换行符 / 全角空格）
- HTML 残留清理（标签去除 + 实体解码）
- 智能段落分割
- 标题规范化（非标准格式检测）
- 无标题文档的结构推断（从纯平文本构建章节树）
- 段落语义分类（概述/原因/分析/解决/配置/步骤/示例/警告/参考）
- 代码块保护（清洗期间保护，清洗后还原）
- 表格检测（Markdown 表格 + 对齐列）
- 重复行去除
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class CleanedDocument:
    """清洗后的文档"""
    original_text: str
    cleaned_text: str
    paragraphs: list[str] = field(default_factory=list)
    detected_headings: list[dict] = field(default_factory=list)
    inferred_headings: list[dict] = field(default_factory=list)
    paragraph_classes: list[dict] = field(default_factory=list)
    detected_code_blocks: list[dict] = field(default_factory=list)
    detected_tables: list[dict] = field(default_factory=list)
    attachment_refs: list[dict] = field(default_factory=list)
    code_block_classes: list[dict] = field(default_factory=list)
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

    # ── Word 特有噪声映射 ──
    # Smart quotes → straight quotes
    _WORD_SMART_QUOTES = {
        '\u201c': '"', '\u201d': '"',  # left/right double quotation mark
        '\u2018': "'", '\u2019': "'",  # left/right single quotation mark
        '\u201a': "'", '\u201b': "'",  # single low-9 / reversed-9
        '\u201e': '"', '\u201f': '"',  # double low-9 / reversed-9
        '\u00ab': '"', '\u00bb': '"',  # guillemets
        '\u2039': "'", '\u203a': "'",  # single guillemets
    }
    # Word 特殊字符 → 替换
    _WORD_SPECIAL = {
        '\u00ad': '',       # optional hyphen (soft hyphen) — 不可见，删除
        '\u000c': '\n\n',   # form feed (page break) → 双换行
        '\u00a0': ' ',      # non-breaking space → 普通空格
        '\u202f': ' ',      # narrow non-breaking space
        '\u200b': '',       # zero-width space
        '\u200c': '',       # zero-width non-joiner
        '\u200d': '',       # zero-width joiner
        '\u200e': '',       # left-to-right mark
        '\u200f': '',       # right-to-left mark
        '\ufeff': '',       # zero-width no-break space (BOM already handled, but may appear mid-text)
        '\t': '    ',       # tab → 4 spaces
        '\u2022': '- ',     # bullet → dash
        '\u2023': '- ',     # triangular bullet
        '\u25e6': '- ',     # white bullet
        '\u2043': '- ',     # hyphen bullet
        '\u25cb': '- ',     # white circle
        '\u25cf': '- ',     # black circle
    }
    # Word 列表编号模式（Word 导出时常见 "1)"  "(1)"  "①"  "一、"  "（一）" 等）
    _WORD_LIST_NUM = re.compile(
        r'^[\(（]?(\d+|[一二三四五六七八九十]+|[①②③④⑤⑥⑦⑧⑨⑩])[\)）\.\、]?\s*'
    )
    # Word 域代码模式（{ HYPERLINK ... }, { PAGE }, { DATE ... }, { SEQ ... } 等）
    _WORD_FIELD_CODE = re.compile(
        r'\{\s*(?:HYPERLINK|PAGE|DATE|TIME|SEQ|REF|CITATION|BIBLIOGRAPHY|TOC|XE|INDEX|'
        r'FORMULA|SYMBOL|AUTONUM|AUTONUMLGL|AUTONUMOUT|BARCODE|COMMENTS|DOCPROPERTY|'
        r'FILLIN|GREETINGLINE|IF|INCLUDEPICTURE|INCLUDETEXT|LINK|LISTNUM|MERGEFIELD|'
        r'MERGEREC|MERGESEQ|NEXT|NEXTIF|NOTE|PRINT|PRIVATE|QUOTE|RD|SET|SKIPIF|STYLEREF|'
        r'SUBJECT|TEMPLATE|USERADDRESS|USERINITIALS|USERNAME)[^}]*\}',
        re.IGNORECASE,
    )
    # Word 段落编号 + 加粗样式的标题模式（如 "1.1  标题" 中点号后有多个空格）
    _WORD_NUMBERED_HEADING = re.compile(
        r'^(\d+(?:\.\d+)*)\s{2,}(.+)$', re.MULTILINE,
    )
    # Word 加粗/下划线转义后的残余标记
    _WORD_STYLE_RESIDUE = re.compile(r'[\*_]{1,3}([^\*_]+)[\*_]{1,3}')

    # ── 段落语义分类关键词 ──
    PARAGRAPH_CLASSIFIERS: dict[str, list[str]] = {
        'overview': ['概述', '简介', '背景', '介绍', '前言', '总览', '概览', '摘要',
                      'overview', 'introduction', 'background', 'summary', 'abstract'],
        'cause': ['原因', '成因', '起因', '根源', '源头', '为什么会', '触发条件',
                   'cause', 'root cause', 'trigger'],
        'analysis': ['分析', '排查', '诊断', '定位', '调查', '检查', '追踪',
                      'analysis', 'diagnosis', 'troubleshoot', 'investigate', 'debug'],
        'solution': ['解决', '方案', '处置', '修复', '处理', '应对', '恢复', '补救',
                      'solution', 'fix', 'resolve', 'mitigation', 'recovery', 'workaround'],
        'config': ['配置', '参数', '设置', '选项', '属性', '变量',
                    'config', 'parameter', 'setting', 'option', 'property', 'variable'],
        'steps': ['步骤', '流程', '操作', '过程', '指南', '做法',
                   'step', 'procedure', 'process', 'guide', 'howto', 'how-to'],
        'example': ['示例', '例如', '比如', '举例', '样例', '实例',
                     'example', 'sample', 'instance', 'e.g.', 'for instance'],
        'warning': ['注意', '警告', '重要', '危险', '须知', '切记', '谨慎',
                     'warning', 'caution', 'important', 'danger', 'note', 'notice'],
        'reference': ['参考', '参见', '引用', '来源', '相关文档', '延伸阅读',
                       'reference', 'see also', 'related', 'further reading'],
    }

    # ── 结构推断：标题候选评分权重 ──
    _STRUCTURE_MIN_PARAGRAPHS = 3       # 至少 3 段才尝试推断结构
    _STRUCTURE_MAX_HEADING_LEN = 80     # 超过此长度的行不可能是标题
    _STRUCTURE_SHORT_LINE_LEN = 40      # 短行更可能是标题
    _STRUCTURE_HEADING_GAP = 6          # 至少每隔 N 段才出现一个标题

    # P0: 代码块占位符模板（用于清洗期间保护代码块）
    _CODE_PLACEHOLDER = '__CODE_BLOCK_{}__'

    def clean(
        self, text: str, preserve_code_blocks: bool = True,
        extract_attachments: bool = True,
    ) -> CleanedDocument:
        """执行完整的文本清洗管道

        Args:
            text: 原始文本
            preserve_code_blocks: 是否在清洗期间保护代码块（默认 True）
            extract_attachments: 是否提取嵌入附件（默认 True）

        Returns:
            CleanedDocument 包含清洗后文本、段落、标题、推断结构、段落分类、
            代码块、表格、附件引用、代码块分类和统计信息
        """
        stats: dict[str, object] = {}
        original = text

        # 0. 附件提取（Base64 图片、文件引用）→ 占位符
        attachment_refs: list[dict] = []
        if extract_attachments:
            text, attachment_refs = self._extract_attachments(text)

        # 1. 编码规范化（BOM / 换行符 / 全角空格）
        text = self._normalize_encoding(text)
        stats['encoding_normalized'] = text != original

        # 1.5. Word 特有噪声清洗（smart quotes / 分页符 / 域代码 / 样式残留）
        text = self._clean_word_noise(text)

        # 2. HTML 实体解码
        text = self._decode_html_entities(text)

        # 3. HTML 标签去除
        text = self._strip_html_tags(text)

        # 3.5. 段落重建：修复 Word 导出导致的错误断行
        text = self._rebuild_paragraphs(text)

        # 3.6. 代码块分类：识别 SQL/Shell/Python/Config/Log 等语言类型
        code_block_classes: list[dict] = []
        if preserve_code_blocks:
            code_block_classes = self._classify_code_blocks(text)

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

        # 10. 结构推断：当标题不足以覆盖文档时，从纯平文本推断章节结构
        inferred: list[dict] = []
        para_count = len(paragraphs)
        heading_count = len(headings)
        if para_count >= self._STRUCTURE_MIN_PARAGRAPHS:
            if heading_count < 3 or (heading_count > 0 and para_count / heading_count > 5):
                inferred = self._infer_structure(paragraphs, headings)

        # 11. 段落语义分类
        para_classes = self._classify_paragraphs(paragraphs)

        # 12. 表格检测
        tables = self._detect_tables(text)

        stats['original_length'] = len(original)
        stats['cleaned_length'] = len(text)
        stats['paragraph_count'] = len(paragraphs)
        stats['heading_count'] = len(headings)
        stats['inferred_heading_count'] = len(inferred)
        stats['code_block_count'] = len(code_blocks)
        stats['table_count'] = len(tables)
        stats['attachment_count'] = len(attachment_refs)

        return CleanedDocument(
            original_text=original,
            cleaned_text=text,
            paragraphs=paragraphs,
            detected_headings=headings,
            inferred_headings=inferred,
            paragraph_classes=para_classes,
            detected_code_blocks=code_blocks,
            detected_tables=tables,
            attachment_refs=attachment_refs,
            code_block_classes=code_block_classes,
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

    # ── 附件提取 ─────────────────────────────────────────────────

    # Base64 图片模式: data:image/{type};base64,{data}
    _BASE64_IMAGE_RE = re.compile(
        r'data:image/(\w+);base64,([A-Za-z0-9+/=]+)',
    )
    # 图片引用模式: ![alt](path) 或 <img src="path">
    _IMAGE_REF_RE = re.compile(
        r'!\[([^\]]*)\]\(([^)]+\.(?:png|jpg|jpeg|gif|svg|bmp|webp))\)',
        re.IGNORECASE,
    )
    _IMG_TAG_RE = re.compile(
        r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>',
        re.IGNORECASE,
    )
    # 附件引用模式: [附件: name](path) 或 (参见 file.xlsx)
    _ATTACHMENT_REF_RE = re.compile(
        r'[\[\(]?\s*(?:附件|参见|详见|参考|见|attachment|see|refer)\s*[:：]?\s*'
        r'([^\s\]\)]+\.(?:yaml|yml|json|xml|conf|cfg|ini|toml|'
        r'xlsx|xls|docx|doc|pptx|ppt|pdf|csv|zip|tar|gz|sql|log|txt))',
        re.IGNORECASE,
    )
    # 中文引用: 见图1, 如图1所示, 参见图2, (图1)
    _FIGURE_REF_RE = re.compile(
        r'(?:见|如图|参见|参考|参照)\s*(?:图|表|附件|附录)\s*(\d+[a-zA-Z]?)',
    )

    def _extract_attachments(self, text: str) -> tuple[str, list[dict]]:
        """提取嵌入的附件和引用，返回占位符替换后的文本和引用列表

        处理:
        - Base64 内嵌图片: data:image/png;base64,... → 解码为文件
        - Markdown 图片引用: ![alt](path) → 保留引用
        - HTML img 标签: <img src="path"> → 保留引用
        - 附件引用: [附件: config.yaml] → 保留引用
        - 图表引用: 见图1, 参见图2 → 保留引用

        Returns:
            (text_with_placeholders, attachment_refs)
        """
        refs: list[dict] = []
        seq = 0

        # 1. Base64 图片提取
        def _save_base64(m):
            nonlocal seq
            img_type = m.group(1)
            data = m.group(2)
            ref_id = f'img_{seq:03d}'
            refs.append({
                'ref_id': ref_id,
                'type': 'image',
                'subtype': 'base64',
                'format': img_type,
                'data': data,
                'original_text': m.group(0)[:80],
                'placeholder': f'<!-- attachment_ref: {ref_id} -->',
            })
            seq += 1
            return f'<!-- attachment_ref: {ref_id} -->\n📷 内嵌图片 ({img_type})'

        text = self._BASE64_IMAGE_RE.sub(_save_base64, text)

        # 2. Markdown 图片引用
        def _save_md_img(m):
            nonlocal seq
            alt = m.group(1) or '图片'
            path = m.group(2)
            ref_id = f'img_{seq:03d}'
            refs.append({
                'ref_id': ref_id,
                'type': 'image',
                'subtype': 'markdown_ref',
                'path': path,
                'alt': alt,
                'original_text': m.group(0),
                'placeholder': f'<!-- attachment_ref: {ref_id} -->',
            })
            seq += 1
            return f'<!-- attachment_ref: {ref_id} -->\n📷 {alt}'

        text = self._IMAGE_REF_RE.sub(_save_md_img, text)

        # 3. HTML img 标签
        def _save_html_img(m):
            nonlocal seq
            path = m.group(1)
            ref_id = f'img_{seq:03d}'
            refs.append({
                'ref_id': ref_id,
                'type': 'image',
                'subtype': 'html_tag',
                'path': path,
                'original_text': m.group(0)[:80],
                'placeholder': f'<!-- attachment_ref: {ref_id} -->',
            })
            seq += 1
            return f'<!-- attachment_ref: {ref_id} -->'

        text = self._IMG_TAG_RE.sub(_save_html_img, text)

        # 4. 附件文件引用
        def _save_attachment(m):
            nonlocal seq
            filename = m.group(1)
            ref_id = f'att_{seq:03d}'
            refs.append({
                'ref_id': ref_id,
                'type': 'file',
                'filename': filename,
                'original_text': m.group(0),
                'placeholder': f'<!-- attachment_ref: {ref_id} -->',
            })
            seq += 1
            return f'<!-- attachment_ref: {ref_id} -->\n📎 附件: {filename}'

        text = self._ATTACHMENT_REF_RE.sub(_save_attachment, text)

        # 5. 图表引用（见图1, 参见表2）
        def _save_figure_ref(m):
            nonlocal seq
            num = m.group(1)
            ref_id = f'fig_{seq:03d}'
            refs.append({
                'ref_id': ref_id,
                'type': 'figure_ref',
                'figure_num': num,
                'original_text': m.group(0),
                'placeholder': f'<!-- attachment_ref: {ref_id} -->',
            })
            seq += 1
            return f'<!-- attachment_ref: {ref_id} -->📊 {m.group(0)}'

        text = self._FIGURE_REF_RE.sub(_save_figure_ref, text)

        return text, refs

    def _clean_word_noise(self, text: str) -> str:
        """清洗 Word 特有噪声

        处理从 Word 文档粘贴/导出时产生的干扰字符：
        - Smart quotes → straight quotes
        - 可选连字符（soft hyphen）→ 删除
        - 分页符（form feed）→ 双换行
        - 域代码（{ HYPERLINK ... } 等）→ 删除
        - 非断行空格 → 普通空格
        - 零宽字符 → 删除
        - 制表符 → 4 空格
        - 特殊 bullet 字符 → 标准 dash
        - 样式残留（** / __ 标记）→ 保留文本
        """
        # 1. 域代码：先删除（在 smart quotes 转换前，因为域代码含花括号）
        text = self._WORD_FIELD_CODE.sub('', text)

        # 2. Smart quotes → straight quotes
        for smart, straight in self._WORD_SMART_QUOTES.items():
            text = text.replace(smart, straight)

        # 3. 特殊字符替换
        for char, replacement in self._WORD_SPECIAL.items():
            text = text.replace(char, replacement)

        # 4. 样式残留：**text** / __text__ → text（保留加粗/下划线标记的内容）
        #    注意：仅在非标题行、非代码块行处理（避免破坏 Markdown 语法）
        text = self._WORD_STYLE_RESIDUE.sub(r'\1', text)

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

    # ── 段落重建 ─────────────────────────────────────────────────

    # 行末无句末标点（可能被错误截断）
    _BROKEN_LINE_END_RE = re.compile(r'[^。！？.!?\u3002\uff01\uff1f\uff0c,;；:\uff1a\uff1b\-\u2014]$')
    # 下一行以小写或中文开头（应合并）
    _CONTINUATION_START_RE = re.compile(r'^[a-z\u4e00-\u9fff]')

    def _rebuild_paragraphs(self, text: str) -> str:
        """修复 Word 导出导致的错误断行

        Word 导出时可能在句子中间插入换行符，导致错误分段。
        例如:
          "Nginx 502错误表示作为反向代理\n时，上游服务器无法提供"
          → "Nginx 502错误表示作为反向代理时，上游服务器无法提供"

        策略:
        1. 识别非自然断行（行末无句末标点 + 下一行以小写/中文开头）
        2. 合并被错误截断的行
        3. 保护代码块、表格、列表、标题等不应合并的行
        """
        lines = text.split('\n')
        result: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 保护特殊行：空行、标题、代码块、表格、列表
            if not stripped or self._is_protected_line(stripped):
                result.append(line)
                i += 1
                continue

            # 检查是否应该与下一行合并
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and self._should_merge_lines(stripped, next_line):
                    # 合并: 当前行 + 下一行
                    merged = stripped + next_line
                    result.append(line.rstrip() + next_line)
                    i += 2
                    continue

            result.append(line)
            i += 1

        return '\n'.join(result)

    def _is_protected_line(self, stripped: str) -> bool:
        """判断是否是不应合并的保护行"""
        # 标题
        if re.match(r'^#{1,6}\s', stripped):
            return True
        # 代码块围栏
        if stripped.startswith('```'):
            return True
        # 表格行
        if stripped.startswith('|') and stripped.endswith('|'):
            return True
        if re.match(r'^\|[-| :]+\|$', stripped):
            return True
        # 列表项
        if re.match(r'^[-*+]\s', stripped):
            return True
        if re.match(r'^\d+[\.\)]\s', stripped):
            return True
        # HTML 注释
        if stripped.startswith('<!--'):
            return True
        # 附件引用占位符
        if stripped.startswith('📷') or stripped.startswith('📎') or stripped.startswith('📊'):
            return True
        return False

    def _should_merge_lines(self, current: str, next_line: str) -> bool:
        """判断两行是否应该合并"""
        # 当前行以句末标点结尾 → 自然断行，不应合并
        if current.endswith(('。', '！', '？', '.', '!', '?', '\u3002', '\uff01', '\uff1f')):
            return False
        # 当前行以冒号结尾 → 自然断行（可能是列表或说明）
        if current.endswith(('：', ':', '\uff1a')):
            return False
        # 当前行以逗号或分号结尾 → 自然断行
        if current.endswith((',', '，', ';', '；', '\uff0c', '\uff1b')):
            return False
        # 下一行以大写字母开头 → 可能是新句子
        if re.match(r'^[A-Z]', next_line):
            return False
        # 下一行以数字+点号开头 → 可能是新列表项
        if re.match(r'^\d+[\.\)]', next_line):
            return False
        # 当前行较短且下一行以中文/小写开头 → 可能是被截断
        if len(current) < 80 and self._CONTINUATION_START_RE.match(next_line):
            return True
        # 当前行不以标点结尾且下一行是连续文字 → 合并
        if self._BROKEN_LINE_END_RE.search(current) and self._CONTINUATION_START_RE.match(next_line):
            return True
        return False

    # ── 代码块分类 ─────────────────────────────────────────────────

    # 各语言的关键词模式
    _CODE_LANG_PATTERNS: dict[str, re.Pattern] = {
        'sql': re.compile(
            r'\b(SELECT|INSERT|UPDATE|DELETE|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE|'
            r'FROM|WHERE|JOIN|GROUP\s+BY|ORDER\s+BY|HAVING|UNION|LIMIT|OFFSET|'
            r'BEGIN|COMMIT|ROLLBACK|TRUNCATE|GRANT|REVOKE)\b',
            re.IGNORECASE,
        ),
        'shell': re.compile(
            r'(^\s*[\$#]\s|^\s*\w+@\w+|^\s*(?:apt|yum|brew|pip|npm|docker|kubectl|'
            r'systemctl|service|chmod|chown|mkdir|rm|cp|mv|grep|awk|sed|curl|wget|'
            r'echo|export|source|cd|ls|cat|tail|head|ps|kill|top|df|du|netstat|'
            r'ssh|scp|rsync|git|make|cmake)\b)',
            re.MULTILINE | re.IGNORECASE,
        ),
        'python': re.compile(
            r'\b(def|class|import|from|return|yield|async|await|try|except|raise|'
            r'with|as|if\s+__name__|lambda|self\.|print\(|\.py\b)',
        ),
        'config': re.compile(
            r'(^\s*\[[^\]]+\]\s*$|^\s*[A-Za-z_][\w.]*\s*[=:]\s*|'
            r'^\s*<\w+>\s*$|^\s*server\s*\{|^\s*location\s+)',
            re.MULTILINE,
        ),
        'json': re.compile(
            r'^\s*[{\[]\s*$|^\s*"[^"]+"\s*:\s*|^\s*[}\]]\s*,?\s*$',
            re.MULTILINE,
        ),
        'log': re.compile(
            r'^\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}:\d{2}|'
            r'^\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}|'
            r'\b(ERROR|WARN|INFO|DEBUG|TRACE|FATAL|CRITICAL)\b',
            re.MULTILINE,
        ),
    }

    def _classify_code_blocks(self, text: str) -> list[dict]:
        """识别代码块的语言类型

        扫描文本中的代码块（``` 围栏），通过关键词模式判断语言类型。

        Returns:
            [{start_line, end_line, lang, confidence, context}, ...]
        """
        classifications: list[dict] = []
        lines = text.split('\n')
        in_code = False
        code_start = 0
        code_content: list[str] = []
        fence_lang = ''

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('```'):
                if not in_code:
                    # 进入代码块
                    in_code = True
                    code_start = i
                    fence_lang = stripped[3:].strip().lower()
                    code_content = []
                else:
                    # 退出代码块
                    in_code = False
                    code_text = '\n'.join(code_content)

                    # 如果已有语言标注，直接使用
                    if fence_lang:
                        lang = fence_lang
                        confidence = 0.95
                    else:
                        lang, confidence = self._detect_code_lang(code_text)

                    # 提取上下文（代码块前的段落）
                    context = self._extract_code_context(lines, code_start)

                    classifications.append({
                        'start_line': code_start,
                        'end_line': i,
                        'lang': lang,
                        'confidence': confidence,
                        'context': context,
                        'line_count': len(code_content),
                    })
            elif in_code:
                code_content.append(line)

        return classifications

    def _detect_code_lang(self, code: str) -> tuple[str, float]:
        """通过关键词模式检测代码语言

        Returns:
            (lang, confidence)
        """
        scores: dict[str, float] = {}
        for lang, pattern in self._CODE_LANG_PATTERNS.items():
            matches = len(pattern.findall(code))
            if matches > 0:
                # 匹配数越多，置信度越高（上限 0.9）
                scores[lang] = min(matches * 0.2, 0.9)

        if not scores:
            return 'text', 0.3

        best = max(scores, key=lambda k: scores[k])
        return best, scores[best]

    def _extract_code_context(self, lines: list[str], code_start: int) -> str:
        """提取代码块前面的上下文描述"""
        context_lines: list[str] = []
        for j in range(code_start - 1, max(code_start - 5, -1), -1):
            line = lines[j].strip()
            if not line or line.startswith('```') or line.startswith('#'):
                break
            context_lines.insert(0, line)
        return ' '.join(context_lines)[:200]

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

    # ─── 结构推断 ─────────────────────────────────────────────────

    def _infer_structure(
        self, paragraphs: list[str], existing_headings: list[dict],
    ) -> list[dict]:
        """从纯平文本推断文档章节结构

        当文档没有显式标题（如 Word 导出后的纯文本）时，
        通过分析段落特征推断标题层级。

        策略：
        1. 扫描每个段落，计算"标题候选"评分
        2. 按评分阈值筛选候选标题
        3. 按文档位置和编号模式分配层级
        4. 合并过于密集的候选标题

        Returns:
            inferred_headings 列表，格式与 detected_headings 一致
        """
        candidates: list[dict] = []

        for idx, para in enumerate(paragraphs):
            score, reason = self._score_heading_candidate(para, idx, paragraphs)
            if score > 0:
                candidates.append({
                    'para_idx': idx,
                    'text': para,
                    'score': score,
                    'reason': reason,
                })

        if not candidates:
            return []

        # 按评分降序排序，取前 60% 或最多 8 个候选
        candidates.sort(key=lambda c: c['score'], reverse=True)
        max_candidates = min(len(candidates), max(3, len(paragraphs) // self._STRUCTURE_HEADING_GAP))
        candidates = candidates[:max_candidates]

        # 按段落位置排序
        candidates.sort(key=lambda c: c['para_idx'])

        # 分配层级：第一段（如有）为 h1，后续按编号模式或关键词权重分配
        inferred: list[dict] = []
        used_indices: set[int] = set()
        for h in existing_headings:
            used_indices.add(h.get('line', -1))

        for i, cand in enumerate(candidates):
            if cand['para_idx'] in used_indices:
                continue

            level = self._assign_heading_level(cand, i, len(candidates), paragraphs)
            inferred.append({
                'para_idx': cand['para_idx'],
                'text': cand['text'],
                'level': level,
                'type': 'inferred',
                'confidence': min(cand['score'] / 10.0, 1.0),
                'reason': cand['reason'],
            })

        return inferred

    def _score_heading_candidate(
        self, para: str, idx: int, paragraphs: list[str],
    ) -> tuple[int, str]:
        """计算段落作为标题候选的评分

        评分维度（满分 10）：
        - 长度：短行 +2～+3（< 20 chars: +3, < 40: +2, < 80: +1）
        - 无句末标点：+2（标题不以句号结尾）
        - 编号检测：+3（含数字编号或列表编号）
        - 关键词匹配：+3（含分类关键词）
        - 全大写：+1
        - 后续内容：+1（后面紧跟较长的正文段落）
        - 位置：+1（文档前 1/3 的标题更可信）

        Returns:
            (score, reason) 评分和理由
        """
        score = 0
        reasons: list[str] = []
        stripped = para.strip()

        # 过长 → 不可能是标题
        if len(stripped) > self._STRUCTURE_MAX_HEADING_LEN:
            return 0, 'too long'

        # 长度评分
        if len(stripped) < 20:
            score += 3
            reasons.append('very short')
        elif len(stripped) < self._STRUCTURE_SHORT_LINE_LEN:
            score += 2
            reasons.append('short')
        elif len(stripped) < self._STRUCTURE_MAX_HEADING_LEN:
            score += 1
            reasons.append('moderate')

        # 无句末标点
        sentence_ends = ('.', '\u3002', '!', '\uff01', '?', '\uff1f', ';', '\uff1b', '：', ':')
        if not stripped.endswith(sentence_ends):
            score += 2
            reasons.append('no sentence end')

        # 编号检测
        if re.match(r'^\d+(?:\.\d+)*\s', stripped):
            score += 3
            reasons.append('numbered')
        elif re.match(r'^[\(（]?\d+[\)）\.\、]', stripped):
            score += 2
            reasons.append('list-like')
        elif re.match(r'^[一二三四五六七八九十]+[、\s]', stripped):
            score += 2
            reasons.append('cn-numbered')

        # 关键词匹配
        for kw_list in self.PARAGRAPH_CLASSIFIERS.values():
            for kw in kw_list:
                if kw in stripped.lower():
                    score += 3
                    reasons.append(f'keyword:{kw}')
                    break
            else:
                continue
            break

        # 全大写（至少 3 个字符）
        if len(stripped) >= 3 and stripped == stripped.upper() and re.search(r'[A-Z]', stripped):
            score += 1
            reasons.append('all caps')

        # 后续内容：后一段是更长正文
        if idx + 1 < len(paragraphs):
            next_para = paragraphs[idx + 1].strip()
            if len(next_para) > len(stripped) * 1.5:
                score += 1
                reasons.append('followed by content')

        # 位置：文档前 1/3 更可能含标题
        if idx < len(paragraphs) / 3:
            score += 1
            reasons.append('early position')

        return score, '+'.join(reasons)

    def _assign_heading_level(
        self, cand: dict, index: int, total: int, paragraphs: list[str],
    ) -> int:
        """为标题候选分配层级（1-6）"""
        text = cand['text']

        # 规则 1：编号深度决定层级（如 "1.2.3" → level 3）
        m = re.match(r'^(\d+(?:\.\d+)*)', text)
        if m:
            depth = len(m.group(1).split('.'))
            return min(depth, 6)

        # 规则 2：中文编号（一、二、三）→ h2
        if re.match(r'^[一二三四五六七八九十]+[、\s]', text):
            return 2

        # 规则 3：第一段为 h1，最后一段如果短则为 h2
        if index == 0:
            return 1
        if index == total - 1 and len(text) < 30:
            return 2

        # 规则 4：关键词决定层级
        for kw in self.PARAGRAPH_CLASSIFIERS.get('overview', []):
            if kw in text.lower():
                return 1
        for kw in self.PARAGRAPH_CLASSIFIERS.get('solution', []):
            if kw in text.lower():
                return 2
        for kw_list in [self.PARAGRAPH_CLASSIFIERS.get(k, []) for k in
                        ['cause', 'analysis', 'steps', 'config']]:
            for kw in kw_list:
                if kw in text.lower():
                    return 3

        # 规则 5：位置决定层级（早期 → 更高层级）
        if index < 2:
            return 1
        if index < total * 0.3:
            return 2
        return 3

    # ─── 段落语义分类 ─────────────────────────────────────────────

    def _classify_paragraphs(self, paragraphs: list[str]) -> list[dict]:
        """对段落进行语义分类

        基于关键词匹配，将每个段落归类到以下类别之一：
        overview / cause / analysis / solution / config / steps /
        example / warning / reference / general

        Returns:
            [{para_idx, text_preview, class, confidence}, ...]
        """
        results: list[dict] = []
        for idx, para in enumerate(paragraphs):
            stripped = para.strip()
            if not stripped:
                continue

            # 跳过可能的标题（短文本）
            if len(stripped) < 15 and self._looks_like_heading(stripped):
                results.append({
                    'para_idx': idx,
                    'text_preview': stripped[:80],
                    'class': 'heading',
                    'confidence': 0.8,
                })
                continue

            best_class = 'general'
            best_score = 0

            for cls_name, keywords in self.PARAGRAPH_CLASSIFIERS.items():
                matches = sum(1 for kw in keywords if kw in stripped.lower())
                if matches > best_score:
                    best_score = matches
                    best_class = cls_name

            # 置信度：匹配关键词数 / 该类别关键词总数
            max_kw = len(self.PARAGRAPH_CLASSIFIERS.get(best_class, []))
            confidence = min(best_score / max(max_kw, 1), 1.0) if best_score > 0 else 0.3

            results.append({
                'para_idx': idx,
                'text_preview': stripped[:120],
                'class': best_class,
                'confidence': round(confidence, 2),
            })

        return results
