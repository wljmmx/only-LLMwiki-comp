"""附件提取器

从文本中提取嵌入的图片、文件引用等非文本内容，生成独立的 L0 原始文档或文件。
保留引用关系以便后续 LLM 编译时传递上下文。

支持的附件类型:
- Base64 内嵌图片 (data:image/...;base64,...)
- Markdown 图片引用 (![alt](path))
- HTML img 标签
- 图表引用 (见图1, 参见表2)
- 文件附件引用 (附件: config.yaml)
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AttachmentRef:
    """附件引用"""
    ref_id: str              # "img_001"
    attachment_type: str     # "image" | "file" | "figure_ref"
    subtype: str             # "base64" | "markdown_ref" | "html_tag" | "file_ref" | "figure_ref"
    original_text: str       # 原文中的引用文本
    placeholder: str         # 插入的占位符
    # 图片/文件特有
    format: str | None = None       # 图片格式: "png", "jpg"
    data: str | None = None         # Base64 数据 (base64 类型)
    path: str | None = None         # 文件路径 (markdown_ref/html_tag 类型)
    alt: str | None = None          # 图片 alt 文本
    filename: str | None = None     # 附件文件名
    figure_num: str | None = None   # 图表编号
    # 提取后的 L0 文档
    linked_doc_id: str | None = None   # 提取为独立 L0 文档的 ID
    linked_file_path: str | None = None  # 提取后的文件路径
    context: str = ""                     # 上下文描述（提取时填充）


@dataclass
class AttachmentExtractionResult:
    """附件提取结果"""
    cleaned_text: str               # 替换占位符后的文本
    refs: list[AttachmentRef]       # 提取的引用列表
    extracted_files: list[dict]     # 提取的文件列表 [{ref_id, path, mime_type, size}]
    stats: dict = field(default_factory=dict)


class AttachmentExtractor:
    """附件提取器

    从文本中提取附件引用，将 Base64 数据解码为文件，生成占位符替换。
    主要的模式匹配由 TextCleaner._extract_attachments 完成，
    本类负责文件落地和 L0 文档注册的后续处理。
    """

    # 支持的图片格式
    IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'svg', 'bmp', 'webp', 'ico'}
    # 支持的附件格式
    ATTACHMENT_EXTENSIONS = {
        'yaml', 'yml', 'json', 'xml', 'conf', 'cfg', 'ini', 'toml',
        'xlsx', 'xls', 'docx', 'doc', 'pptx', 'ppt', 'pdf',
        'csv', 'zip', 'tar', 'gz', 'sql', 'log', 'txt',
    }

    def __init__(self, data_dir: str = "data/attachments"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def extract(self, text: str, doc_id: str) -> AttachmentExtractionResult:
        """提取附件并落地文件

        Args:
            text: 原始文本（可能含 Base64 图片等）
            doc_id: 来源文档 ID

        Returns:
            AttachmentExtractionResult
        """
        refs: list[AttachmentRef] = []
        extracted: list[dict] = []
        cleaned = text
        seq = 0

        # 1. Base64 图片
        cleaned, base64_refs = self._extract_base64_images(cleaned, doc_id, seq)
        refs.extend(base64_refs)
        for r in base64_refs:
            if r.linked_file_path:
                extracted.append({
                    'ref_id': r.ref_id,
                    'path': r.linked_file_path,
                    'mime_type': f'image/{r.format}',
                    'type': 'image',
                })
        seq += len(base64_refs)

        # 2. Markdown 图片引用
        cleaned, md_img_refs = self._extract_markdown_images(cleaned, doc_id, seq)
        refs.extend(md_img_refs)
        seq += len(md_img_refs)

        # 3. 附件文件引用
        cleaned, file_refs = self._extract_file_attachments(cleaned, doc_id, seq)
        refs.extend(file_refs)
        seq += len(file_refs)

        stats = {
            'total_refs': len(refs),
            'image_count': sum(1 for r in refs if r.attachment_type == 'image'),
            'file_count': sum(1 for r in refs if r.attachment_type == 'file'),
            'figure_ref_count': sum(1 for r in refs if r.attachment_type == 'figure_ref'),
            'extracted_files': len(extracted),
        }

        return AttachmentExtractionResult(
            cleaned_text=cleaned,
            refs=refs,
            extracted_files=extracted,
            stats=stats,
        )

    def _extract_base64_images(
        self, text: str, doc_id: str, start_seq: int,
    ) -> tuple[str, list[AttachmentRef]]:
        """提取 Base64 内嵌图片"""
        refs: list[AttachmentRef] = []
        pattern = re.compile(
            r'data:image/(\w+);base64,([A-Za-z0-9+/=]+)',
        )

        def _handler(m: re.Match) -> str:
            nonlocal start_seq
            img_type = m.group(1)
            data = m.group(2)
            ref_id = f'img_{start_seq:03d}'
            start_seq += 1

            # 解码并保存文件
            file_path = None
            try:
                img_bytes = base64.b64decode(data)
                ext = img_type if img_type in self.IMAGE_EXTENSIONS else 'png'
                filename = f'{doc_id}_{ref_id}.{ext}'
                file_path = str(self.data_dir / filename)
                with open(file_path, 'wb') as f:
                    f.write(img_bytes)
            except Exception:
                file_path = None

            ref = AttachmentRef(
                ref_id=ref_id,
                attachment_type='image',
                subtype='base64',
                original_text=m.group(0)[:80],
                placeholder=f'<!-- attachment_ref: {ref_id} -->',
                format=img_type,
                data=data,
                linked_file_path=file_path,
                context=f'内嵌图片 ({img_type})',
            )
            refs.append(ref)
            return f'<!-- attachment_ref: {ref_id} -->\n📷 内嵌图片 ({img_type})'

        cleaned = pattern.sub(_handler, text)
        return cleaned, refs

    def _extract_markdown_images(
        self, text: str, doc_id: str, start_seq: int,
    ) -> tuple[str, list[AttachmentRef]]:
        """提取 Markdown 图片引用"""
        refs: list[AttachmentRef] = []
        pattern = re.compile(
            r'!\[([^\]]*)\]\(([^)]+\.(?:png|jpg|jpeg|gif|svg|bmp|webp))\)',
            re.IGNORECASE,
        )

        def _handler(m: re.Match) -> str:
            nonlocal start_seq
            alt = m.group(1) or '图片'
            path = m.group(2)
            ref_id = f'img_{start_seq:03d}'
            start_seq += 1

            ref = AttachmentRef(
                ref_id=ref_id,
                attachment_type='image',
                subtype='markdown_ref',
                original_text=m.group(0),
                placeholder=f'<!-- attachment_ref: {ref_id} -->',
                path=path,
                alt=alt,
                context=alt,
            )
            refs.append(ref)
            return f'<!-- attachment_ref: {ref_id} -->\n📷 {alt}'

        cleaned = pattern.sub(_handler, text)
        return cleaned, refs

    def _extract_file_attachments(
        self, text: str, doc_id: str, start_seq: int,
    ) -> tuple[str, list[AttachmentRef]]:
        """提取文件附件引用"""
        refs: list[AttachmentRef] = []
        pattern = re.compile(
            r'[\[\(]?\s*(?:附件|参见|详见|参考|见|attachment|see|refer)\s*[:：]?\s*'
            r'([^\s\]\)]+\.(?:yaml|yml|json|xml|conf|cfg|ini|toml|'
            r'xlsx|xls|docx|doc|pptx|ppt|pdf|csv|zip|tar|gz|sql|log|txt))',
            re.IGNORECASE,
        )

        def _handler(m: re.Match) -> str:
            nonlocal start_seq
            filename = m.group(1)
            ref_id = f'att_{start_seq:03d}'
            start_seq += 1

            ref = AttachmentRef(
                ref_id=ref_id,
                attachment_type='file',
                subtype='file_ref',
                original_text=m.group(0),
                placeholder=f'<!-- attachment_ref: {ref_id} -->',
                filename=filename,
                context=f'附件: {filename}',
            )
            refs.append(ref)
            # 附件文件作为 L0 文档注册
            ref.linked_doc_id = f'{doc_id}_{ref_id}'
            return f'<!-- attachment_ref: {ref_id} -->\n📎 附件: {filename}'

        cleaned = pattern.sub(_handler, text)
        return cleaned, refs

    def get_file_path(self, ref_id: str) -> str | None:
        """根据 ref_id 获取提取后的文件路径"""
        pattern = f'{ref_id}.'
        if self.data_dir.exists():
            for f in self.data_dir.iterdir():
                if pattern in f.name:
                    return str(f)
        return None