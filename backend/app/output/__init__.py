"""输出文档模块

包含:
- templates/: 文档模板库（YAML 定义）
- generator.py: 标准化输出文档生成器
"""

from app.output.generator import DocumentGenerator, GenerateResult, GeneratedDocument, DocumentSource
from app.output.templates import (
    DocumentTemplate,
    TemplateSection,
    load_template,
    list_templates,
    get_builtin_templates,
)

__all__ = [
    'DocumentGenerator',
    'GenerateResult',
    'GeneratedDocument',
    'DocumentSource',
    'DocumentTemplate',
    'TemplateSection',
    'load_template',
    'list_templates',
    'get_builtin_templates',
]