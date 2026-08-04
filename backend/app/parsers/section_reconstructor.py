"""章节重构器

在格式清洗后，基于内容语义重新划分章节，而不是直接使用原始标题。

解决的问题：
- 格式混乱的文档可能有不合理的章节划分
- Word 导出的文档可能有冗余的标题层级
- 某些文档的章节过于细碎或过于笼统

策略：
1. 分析段落语义类别（概述/原因/分析/解决等）
2. 检测内容主题变化边界
3. 合并过短的章节（< 200 字符）
4. 拆分过长的章节（> 5000 字符）
5. 保持附件引用的关联关系
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.parsers.text_cleaner import CleanedDocument


@dataclass
class ReconstructedSection:
    """重构后的章节"""
    title: str  # 章节标题（可能是生成的）
    level: int  # 层级（1-6）
    content: str  # 章节内容
    original_title: str | None = None  # 原始标题（如果有）
    original_level: int | None = None  # 原始层级
    start_para_idx: int = 0  # 起始段落索引
    end_para_idx: int = 0  # 结束段落索引
    attachment_refs: list[dict] = field(default_factory=list)  # 附件引用
    metadata: dict = field(default_factory=dict)


@dataclass
class ReconstructedDocument:
    """重构后的文档"""
    original_text: str
    reconstructed_text: str  # 重构后的 Markdown 文本
    sections: list[ReconstructedSection]
    attachment_map: dict[str, int] = field(default_factory=dict)  # ref_id -> section_idx
    stats: dict = field(default_factory=dict)


class SectionReconstructor:
    """章节重构器

    基于内容语义重新划分章节，而不是直接使用原始标题。

    用法：
        from app.parsers.text_cleaner import TextCleaner
        from app.parsers.section_reconstructor import SectionReconstructor

        cleaner = TextCleaner()
        cleaned = cleaner.clean(raw_text)

        reconstructor = SectionReconstructor()
        reconstructed = reconstructor.reconstruct(cleaned)
        print(reconstructed.reconstructed_text)
    """

    # 章节最小/最大长度阈值（字符数）
    MIN_SECTION_LEN = 200
    MAX_SECTION_LEN = 5000

    # 主题变化检测的段落窗口
    TOPIC_WINDOW = 5

    # 章节分割的关键词模式（段落语义类别组合）
    SECTION_PATTERNS = [
        # 故障排查类文档的标准结构
        (['overview', 'cause', 'analysis', 'solution'], 1),
        (['overview', 'cause', 'solution'], 1),
        (['overview', 'analysis', 'solution'], 1),
        # 操作指南类文档
        (['overview', 'steps'], 1),
        (['overview', 'config', 'steps'], 2),
        # 配置类文档
        (['overview', 'config'], 2),
        (['config', 'example'], 3),
    ]

    # 章节起始关键词（强特征）
    SECTION_START_KEYWORDS = {
        1: ['概述', '简介', '背景', '前言', '总览', '概览', '摘要',
            'overview', 'introduction', 'background', 'summary'],
        2: ['原因', '成因', '起因', '根源', '触发条件',
             '分析', '排查', '诊断', '定位', '调查',
             '解决', '方案', '处置', '修复', '处理',
             '配置', '参数', '设置',
             'cause', 'analysis', 'solution', 'config'],
        3: ['步骤', '流程', '操作', '过程',
             '示例', '例如', '比如',
             'steps', 'example'],
    }

    def reconstruct(
        self,
        cleaned: 'CleanedDocument',
        original_headings: list[dict] | None = None,
    ) -> ReconstructedDocument:
        """执行章节重构

        Args:
            cleaned: 清洗后的文档对象
            original_headings: 原始标题列表（可选，用于保留引用关系）

        Returns:
            ReconstructedDocument 包含重构后的文本和章节列表
        """
        paragraphs = cleaned.paragraphs
        para_classes = cleaned.paragraph_classes
        attachment_refs = cleaned.attachment_refs

        # 1. 分析段落语义，检测主题边界
        boundaries = self._detect_topic_boundaries(paragraphs, para_classes)

        # 2. 合并相邻的小边界（避免章节过于细碎）
        boundaries = self._merge_close_boundaries(boundaries, paragraphs)

        # 3. 基于边界划分章节
        sections = self._create_sections(
            boundaries, paragraphs, para_classes, original_headings,
        )

        # 4. 处理附件引用的关联关系
        attachment_map = self._map_attachments(sections, attachment_refs)

        # 5. 生成重构后的 Markdown 文本
        reconstructed_text = self._generate_markdown(sections)

        stats = {
            'original_paragraphs': len(paragraphs),
            'original_headings': len(original_headings or []),
            'reconstructed_sections': len(sections),
            'attachment_count': len(attachment_refs),
            'avg_section_len': sum(len(s.content) for s in sections) // max(len(sections), 1),
        }

        return ReconstructedDocument(
            original_text=cleaned.cleaned_text,
            reconstructed_text=reconstructed_text,
            sections=sections,
            attachment_map=attachment_map,
            stats=stats,
        )

    def _detect_topic_boundaries(
        self,
        paragraphs: list[str],
        para_classes: list[dict],
    ) -> list[tuple[int, int]]:
        """检测主题变化边界

        分析段落语义类别变化，识别章节边界。

        Returns:
            [(para_idx, level), ...] 边界位置列表
        """
        boundaries: list[tuple[int, int]] = []

        # 构建段落索引 -> 类别映射
        para_idx_to_class: dict[int, str] = {}
        for pc in para_classes:
            para_idx_to_class[pc['para_idx']] = pc['class']

        # 扫描段落，检测语义变化
        prev_class = 'general'
        for i, para in enumerate(paragraphs):
            stripped = para.strip()
            if not stripped:
                continue

            # 获取当前段落类别
            curr_class = para_idx_to_class.get(i, 'general')

            # 检测是否是章节起始
            is_section_start, level = self._is_section_start(
                stripped, curr_class, prev_class, i, paragraphs,
            )

            if is_section_start:
                boundaries.append((i, level))

            prev_class = curr_class

        # 确保文档开头是 h1
        if not boundaries or boundaries[0][0] != 0:
            boundaries.insert(0, (0, 1))

        return boundaries

    def _is_section_start(
        self,
        para: str,
        curr_class: str,
        prev_class: str,
        idx: int,
        paragraphs: list[str],
    ) -> tuple[bool, int]:
        """判断段落是否是章节起始

        Returns:
            (is_start, level)
        """
        stripped = para.strip()

        # 规则 1: 显式标题标记（# 开头）
        if re.match(r'^#{1,6}\s', stripped):
            m = re.match(r'^(#{1,6})\s', stripped)
            if m:
                return True, len(m.group(1))

        # 规则 2: 编号标题（如 "1. 概述"）
        # 修复：必须有点号分隔，排除纯数字开头的行（如网线颜色编码）
        # 格式要求：数字 + 点号 + 空格 + 文本（非纯颜色/数字）
        m = re.match(r'^(\d+(?:\.\d+)*)\.\s+(.+)$', stripped)
        if m:
            number_part = m.group(1)
            title_part = m.group(2).strip()
            # 排除：纯颜色名、纯数字、过短的标题
            color_names = ['白橙', '橙', '白绿', '绿', '白蓝', '蓝', '白棕', '棕',
                          'white-orange', 'orange', 'white-green', 'green',
                          'white-blue', 'blue', 'white-brown', 'brown']
            if title_part.lower() not in [c.lower() for c in color_names]:
                if len(title_part) > 2:  # 标题至少 3 个字符
                    depth = len(number_part.split('.'))
                    return True, min(depth, 6)

        # 规则 3: 语义类别变化（从 general 到非 general）
        if prev_class == 'general' and curr_class != 'general':
            # 检查该类别对应的层级
            for level, keywords in self.SECTION_START_KEYWORDS.items():
                for kw in keywords:
                    if kw in stripped.lower():
                        return True, level
            # 默认为 h2
            return True, 2

        # 规则 4: 短文本 + 无句末标点（可能是标题）
        # 修复：排除纯数字开头的行（如 "1 白橙"、"2 橙"）
        if len(stripped) < 60 and not stripped.endswith(('.', '。', '!', '！', '?', '？')):
            # 排除：数字 + 空格 + 短文本（可能是颜色编码/列表项）
            if re.match(r'^\d+\s+\S{1,10}$', stripped):
                return False, 0
            # 检查是否含有关键词
            for level, keywords in self.SECTION_START_KEYWORDS.items():
                for kw in keywords:
                    if kw in stripped.lower():
                        return True, level

        # 规则 5: 段落长度跳变（前面是长段落，当前是短段落）
        if idx > 0:
            prev_para = paragraphs[idx - 1].strip()
            if len(prev_para) > 200 and len(stripped) < 80:
                return True, 2

        return False, 0

    def _merge_close_boundaries(
        self,
        boundaries: list[tuple[int, int]],
        paragraphs: list[str],
    ) -> list[tuple[int, int]]:
        """合并相邻的小边界

        如果两个边界之间的内容太短（< MIN_SECTION_LEN），合并到前一个章节。

        改进：
        1. 连续短章节会被合并，但不会全部合并成一个
        2. 只有当连续短章节的总长度仍然过短时才继续合并
        3. 保留原始章节数量的下限，避免过度合并
        """
        if len(boundaries) <= 1:
            return boundaries

        # 第一遍：计算每个边界对应的内容长度
        section_lens: list[int] = []
        for i, (start_idx, level) in enumerate(boundaries):
            if i + 1 < len(boundaries):
                next_idx = boundaries[i + 1][0]
            else:
                next_idx = len(paragraphs)
            content_len = sum(
                len(paragraphs[j].strip())
                for j in range(start_idx, next_idx)
                if j < len(paragraphs)
            )
            section_lens.append(content_len)

        # 第二遍：合并连续的短章节
        # 策略：将连续的短章节合并成一个较长的章节
        merged: list[tuple[int, int]] = []
        i = 0

        while i < len(boundaries):
            start_idx, level = boundaries[i]
            content_len = section_lens[i]

            if content_len < self.MIN_SECTION_LEN and i > 0:
                # 尝试找后续连续短章节一起合并
                merged_len = content_len
                j = i
                while j < len(boundaries) - 1:
                    next_len = section_lens[j + 1]
                    merged_len += next_len
                    if merged_len >= self.MIN_SECTION_LEN:
                        break
                    j += 1

                # 合并 i 到 j 的短章节到前一个章节
                # 不添加新边界，直接跳过这些短章节
                i = j + 1
            else:
                # 当前章节足够长
                merged.append((start_idx, level))
                i += 1

        # 如果合并后只有一个章节且原始有多个章节，尝试保留一些结构
        if len(merged) <= 1 and len(boundaries) > 2:
            # 找原始边界中较长的章节，保留它们
            long_sections = [
                (boundaries[i], section_lens[i])
                for i in range(len(boundaries))
                if section_lens[i] >= self.MIN_SECTION_LEN
            ]
            if long_sections:
                # 保留所有较长的章节
                merged = [b for b, _ in long_sections]
            else:
                # 所有章节都很短，按原结构保留（不合并）
                # 但限制最大章节数，避免碎片化
                max_sections = min(len(boundaries), max(3, len(paragraphs) // 300))
                if len(boundaries) > max_sections:
                    # 均匀采样保留
                    step = len(boundaries) // max_sections
                    merged = boundaries[::step][:max_sections]
                    if boundaries[-1] not in merged:
                        merged.append(boundaries[-1])
                else:
                    merged = list(boundaries)

        return merged

    def _create_sections(
        self,
        boundaries: list[tuple[int, int]],
        paragraphs: list[str],
        para_classes: list[dict],
        original_headings: list[dict] | None,
    ) -> list[ReconstructedSection]:
        """基于边界创建章节"""
        sections: list[ReconstructedSection] = []

        for i, (start_idx, level) in enumerate(boundaries):
            # 计算章节结束位置
            if i + 1 < len(boundaries):
                end_idx = boundaries[i + 1][0]
            else:
                end_idx = len(paragraphs)

            # 提取章节内容
            section_paras = [paragraphs[j].strip() for j in range(start_idx, end_idx) if j < len(paragraphs)]
            if not section_paras:
                continue

            # 生成章节标题
            first_para = section_paras[0]
            title, original_title, title_from_first = self._generate_section_title(
                first_para, level, para_classes, start_idx,
            )

            # 检查原始标题是否存在，保留引用
            orig_level = None
            if original_headings:
                for h in original_headings:
                    if h.get('line') == start_idx:
                        original_title = h.get('text')
                        orig_level = h.get('level')
                        break

            # 如果 title 从第一段派生，从内容中排除第一段避免重复
            if title_from_first and len(section_paras) > 1:
                content_paras = section_paras[1:]
            else:
                content_paras = section_paras

            content = '\n\n'.join(content_paras)

            sections.append(ReconstructedSection(
                title=title,
                level=level,
                content=content,
                original_title=original_title,
                original_level=orig_level,
                start_para_idx=start_idx,
                end_para_idx=end_idx,
            ))

        return sections

    def _generate_section_title(
        self,
        first_para: str,
        level: int,
        para_classes: list[dict],
        para_idx: int,
    ) -> tuple[str, str | None, bool]:
        """生成章节标题

        Returns:
            (title, original_title, title_from_first)
            - title: 章节标题文本
            - original_title: 原始标题引用（来自 original_headings）
            - title_from_first: title 是否从第一段内容派生（是则需排除第一段）
        """
        stripped = first_para.strip()

        # 如果已经是 Markdown 标题，提取标题文本
        m = re.match(r'^#{1,6}\s+(.+)$', stripped)
        if m:
            return m.group(1).strip(), m.group(1).strip(), False

        # 如果是编号标题，提取标题文本（必须有点号分隔）
        m = re.match(r'^(\d+(?:\.\d+)*)\.\s+(.+)$', stripped)
        if m:
            return m.group(2).strip(), m.group(2).strip(), False

        # 否则，基于内容生成标题
        # 查找该段落的语义类别
        para_class = 'general'
        for pc in para_classes:
            if pc['para_idx'] == para_idx:
                para_class = pc['class']
                break

        # 基于类别生成默认标题
        default_titles = {
            'overview': '概述',
            'cause': '原因分析',
            'analysis': '排查步骤',
            'solution': '解决方案',
            'config': '配置参数',
            'steps': '操作步骤',
            'example': '示例',
            'warning': '注意事项',
            'reference': '参考信息',
        }

        # 如果第一段是短文本，直接用作标题
        if len(stripped) < 80 and not stripped.endswith(('.', '。')):
            # 清理装饰性文字
            title = re.sub(r'[（\(][^)）]*[)）]', '', stripped)
            title = re.sub(r'[！!。.]+$', '', title)
            return title.strip(), None, True

        # 否则使用类别默认标题
        title = default_titles.get(para_class, '章节')
        return title, None, True

    def _map_attachments(
        self,
        sections: list[ReconstructedSection],
        attachment_refs: list[dict],
    ) -> dict[str, int]:
        """建立附件引用与章节的映射"""
        attachment_map: dict[str, int] = {}

        for ref in attachment_refs:
            ref_id = ref.get('ref_id', '')
            # 根据附件在原文中的位置，映射到重构后的章节
            # 简单策略：映射到第一个包含该附件占位符的章节
            placeholder = ref.get('placeholder', '')
            for i, section in enumerate(sections):
                if placeholder and placeholder in section.content:
                    attachment_map[ref_id] = i
                    section.attachment_refs.append(ref)
                    break

        return attachment_map

    def _generate_markdown(self, sections: list[ReconstructedSection]) -> str:
        """生成重构后的 Markdown 文本

        关键：直接追加 section.content（段落间已用 \\n\\n 分隔），
        保持段落完整性，避免 _parse_markdown 因额外空行断行膨胀。
        """
        lines: list[str] = []

        for section in sections:
            # 添加章节标题
            prefix = '#' * section.level
            lines.append(f'{prefix} {section.title}')
            lines.append('')

            # 直接追加 content（已排除标题段落，段落用 \n\n 分隔）
            lines.append(section.content)
            lines.append('')

        return '\n'.join(lines)