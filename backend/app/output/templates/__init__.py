"""文档模板库（P4-3）

标准化输出文档模板，YAML 格式定义。
每个模板包含:
- 模板元信息（名称、描述、适用场景）
- 章节结构（标题、类型、必填/可选）
- LLM 生成提示词
- 样式设置
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TemplateSection:
    """模板章节定义"""
    title: str                      # 章节标题
    section_type: str               # text | list | table | code | checklist
    required: bool = True            # 是否必填
    description: str = ""           # 章节说明
    max_items: int = 0              # 列表最大条目数（0=不限制）
    sort_by: str = ""               # 排序方式（priority | time | alphabetical）


@dataclass
class DocumentTemplate:
    """文档模板"""
    template_id: str
    name: str
    description: str
    document_type: str              # operations_manual | troubleshooting_guide | deployment_guide | architecture_doc | runbook_collection
    target_audience: str = "运维工程师"
    sections: list[TemplateSection] = field(default_factory=list)
    prompt_template: str = ""       # LLM 生成提示词模板
    style: dict = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> 'DocumentTemplate':
        """从 YAML 文件加载模板"""
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        sections = [
            TemplateSection(**s) for s in data.get('sections', [])
        ]
        return cls(
            template_id=data.get('template_id', ''),
            name=data.get('name', ''),
            description=data.get('description', ''),
            document_type=data.get('document_type', 'operations_manual'),
            target_audience=data.get('target_audience', '运维工程师'),
            sections=sections,
            prompt_template=data.get('prompt_template', ''),
            style=data.get('style', {}),
        )


# ── 内置模板库 ──

def get_builtin_templates() -> list[DocumentTemplate]:
    """获取内置模板列表"""
    return [
        _OPERATIONS_MANUAL_TEMPLATE,
        _TROUBLESHOOTING_GUIDE_TEMPLATE,
        _DEPLOYMENT_GUIDE_TEMPLATE,
        _ARCHITECTURE_DOC_TEMPLATE,
        _RUNBOOK_COLLECTION_TEMPLATE,
    ]


# 前台操作手册
_OPERATIONS_MANUAL_TEMPLATE = DocumentTemplate(
    template_id='operations_manual',
    name='前台操作手册',
    description='面向前台操作人员的系统操作手册，按功能模块组织',
    document_type='operations_manual',
    target_audience='前台操作人员',
    sections=[
        TemplateSection('文档概述', 'text', True, '文档目的、适用范围、阅读对象'),
        TemplateSection('系统概述', 'text', True, '系统简介、核心功能说明'),
        TemplateSection('登录与权限', 'text', True, '登录方式、权限说明'),
        TemplateSection('功能操作指南', 'list', True, '按功能模块组织操作步骤', max_items=20),
        TemplateSection('常见问题', 'list', True, 'FAQ 列表', max_items=15),
        TemplateSection('注意事项', 'checklist', False, '操作注意事项和限制'),
        TemplateSection('附录', 'text', False, '术语表、参考链接'),
    ],
    prompt_template="""你是一个技术文档编写专家。请根据以下 Wiki 知识库内容，生成一份面向{target_audience}的{name}。

## 文档要求
- 语言简洁易懂，避免过多技术术语
- 每个操作步骤配截图位置标记（📷 标记）
- 重要提示使用 > 引用块突出
- 表格用于参数说明和对照

## 章节结构
{section_structure}

## 相关 Wiki 内容
{wiki_content}

## 输出格式
直接输出 Markdown 格式的完整文档，从 `# {name}` 开始。""",
    style={'toc': True, 'page_numbers': True, 'header_footer': True},
)

# 故障排查指南
_TROUBLESHOOTING_GUIDE_TEMPLATE = DocumentTemplate(
    template_id='troubleshooting_guide',
    name='故障排查指南',
    description='系统故障排查的标准化指南，按故障类型组织',
    document_type='troubleshooting_guide',
    target_audience='运维工程师',
    sections=[
        TemplateSection('文档概述', 'text', True, '指南目的、适用范围'),
        TemplateSection('故障分类总览', 'table', True, '故障类型、严重程度、影响范围'),
        TemplateSection('通用排查流程', 'text', True, '标准排查步骤'),
        TemplateSection('故障排查手册', 'list', True, '按故障类型组织排查步骤', max_items=30, sort_by='priority'),
        TemplateSection('处置方案', 'list', True, '对应每种故障的处置方案', max_items=30),
        TemplateSection('预防措施', 'checklist', True, '预防性维护检查清单'),
        TemplateSection('附录', 'text', False, '工具清单、联系人、参考文档'),
    ],
    prompt_template="""你是一个运维故障排查专家。请根据以下 Wiki 知识库内容，生成一份面向{target_audience}的{name}。

## 文档要求
- 按故障严重程度排序（critical → high → medium → low）
- 每个故障包含：现象、成因、排查步骤、处置方案
- 排查步骤包含具体命令（```bash 代码块）
- 配置参数用表格展示

## 章节结构
{section_structure}

## 相关 Wiki 内容
{wiki_content}

## 输出格式
直接输出 Markdown 格式的完整文档，从 `# {name}` 开始。""",
    style={'toc': True, 'code_highlight': True},
)

# 部署指南
_DEPLOYMENT_GUIDE_TEMPLATE = DocumentTemplate(
    template_id='deployment_guide',
    name='部署指南',
    description='系统部署的标准化指南，包含环境要求、部署步骤、验证方法',
    document_type='deployment_guide',
    target_audience='运维工程师',
    sections=[
        TemplateSection('文档概述', 'text', True, '部署目标、适用范围'),
        TemplateSection('环境要求', 'table', True, '硬件/软件/网络要求'),
        TemplateSection('前置准备', 'checklist', True, '部署前检查清单'),
        TemplateSection('部署步骤', 'list', True, '按顺序的部署操作步骤', max_items=20),
        TemplateSection('配置说明', 'text', True, '关键配置参数说明'),
        TemplateSection('验证方法', 'checklist', True, '部署后验证清单'),
        TemplateSection('回滚方案', 'text', False, '部署失败的回滚步骤'),
        TemplateSection('附录', 'text', False, '参考文档、常见问题'),
    ],
    prompt_template="""你是一个系统部署专家。请根据以下 Wiki 知识库内容，生成一份面向{target_audience}的{name}。

## 文档要求
- 部署步骤按顺序编号，每步包含：操作内容 + 预期结果
- 命令使用 ```bash 代码块
- 配置参数使用表格展示
- 重要警告使用 > 引用块

## 章节结构
{section_structure}

## 相关 Wiki 内容
{wiki_content}

## 输出格式
直接输出 Markdown 格式的完整文档，从 `# {name}` 开始。""",
    style={'toc': True, 'code_highlight': True},
)

# 架构文档
_ARCHITECTURE_DOC_TEMPLATE = DocumentTemplate(
    template_id='architecture_doc',
    name='系统架构文档',
    description='系统整体架构说明，包含组件、依赖、数据流',
    document_type='architecture_doc',
    target_audience='技术管理/架构师',
    sections=[
        TemplateSection('文档概述', 'text', True, '文档目的、系统定位'),
        TemplateSection('总体架构', 'text', True, '架构概述、设计原则'),
        TemplateSection('组件说明', 'list', True, '核心组件功能说明', max_items=20),
        TemplateSection('依赖关系', 'table', True, '组件间依赖关系矩阵'),
        TemplateSection('数据流', 'text', True, '核心数据流说明'),
        TemplateSection('部署拓扑', 'text', True, '部署架构、网络拓扑'),
        TemplateSection('容量规划', 'table', False, '各组件容量建议'),
        TemplateSection('附录', 'text', False, '术语表、参考文档'),
    ],
    prompt_template="""你是一个系统架构师。请根据以下 Wiki 知识库内容，生成一份面向{target_audience}的{name}。

## 文档要求
- 架构描述清晰，组件关系明确
- 使用 Mermaid 图表描述架构（如适用）
- 组件间依赖关系用表格展示
- 技术术语附带简要说明

## 章节结构
{section_structure}

## 相关 Wiki 内容
{wiki_content}

## 输出格式
直接输出 Markdown 格式的完整文档，从 `# {name}` 开始。""",
    style={'toc': True, 'diagrams': True},
)

# Runbook 合集
_RUNBOOK_COLLECTION_TEMPLATE = DocumentTemplate(
    template_id='runbook_collection',
    name='运维操作手册合集',
    description='运维操作手册的标准化合集，按场景组织',
    document_type='runbook_collection',
    target_audience='运维工程师',
    sections=[
        TemplateSection('文档概述', 'text', True, '合集目的、覆盖场景'),
        TemplateSection('操作场景总览', 'table', True, '场景分类、影响范围、操作等级'),
        TemplateSection('日常操作', 'list', True, '日常运维操作手册', max_items=15),
        TemplateSection('变更操作', 'list', True, '变更操作手册', max_items=15),
        TemplateSection('应急操作', 'list', True, '应急操作手册', max_items=15, sort_by='priority'),
        TemplateSection('附录', 'text', False, '工具清单、审批流程、联系人'),
    ],
    prompt_template="""你是一个运维操作手册编写专家。请根据以下 Wiki 知识库内容，生成一份面向{target_audience}的{name}。

## 文档要求
- 按场景分类组织（日常/变更/应急）
- 每个操作包含：目的、影响分析、前置条件、操作步骤、验证方法、回滚方案
- 应急操作按优先级排序
- 重要操作标注风险等级

## 章节结构
{section_structure}

## 相关 Wiki 内容
{wiki_content}

## 输出格式
直接输出 Markdown 格式的完整文档，从 `# {name}` 开始。""",
    style={'toc': True, 'risk_labels': True},
)


def load_template(template_id: str) -> DocumentTemplate | None:
    """加载模板（优先从模板目录加载 YAML，其次从内置模板）"""
    # 尝试从文件加载
    template_dir = Path(__file__).parent
    yaml_path = template_dir / f'{template_id}.yaml'
    if yaml_path.exists():
        return DocumentTemplate.from_yaml(yaml_path)

    # 回退到内置模板
    for t in get_builtin_templates():
        if t.template_id == template_id:
            return t
    return None


def list_templates() -> list[dict]:
    """列出所有可用模板"""
    templates = get_builtin_templates()
    return [
        {
            'template_id': t.template_id,
            'name': t.name,
            'description': t.description,
            'document_type': t.document_type,
            'target_audience': t.target_audience,
            'section_count': len(t.sections),
        }
        for t in templates
    ]