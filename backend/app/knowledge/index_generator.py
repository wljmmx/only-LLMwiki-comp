"""Wiki 自组织目录树生成器

从所有 Wiki 页面生成层级目录树，作为 wiki:index 页面持久化。

流程:
1. 收集所有 Wiki 页面的摘要信息（slug, title, type, tags, wikilinks）
2. 构建页面关系图（wikilink 出链 + tag 共享 + 类型相似）
3. LLM 生成层级目录树
4. 保存为 wiki:index 页面
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IndexNode:
    """目录树节点"""
    slug: str
    title: str
    node_type: str          # "category" | "page"
    level: int
    children: list['IndexNode'] = field(default_factory=list)
    description: str = ""
    page_count: int = 0     # 仅 category 节点
    tags: list[str] = field(default_factory=list)


@dataclass
class IndexTree:
    """Wiki 目录树"""
    generated_at: str
    version: int
    total_pages: int
    roots: list[IndexNode]
    orphan_pages: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class IndexGenerator:
    """Wiki 目录树生成器

    支持两种模式:
    - 模板生成: 按 type + tag 自动分组（无 LLM）
    - LLM 生成: 语义聚类 + 层级组织（需 LLM）
    """

    INDEX_GENERATE_PROMPT = """你是一个知识库目录结构设计专家。请根据以下 Wiki 页面信息,
生成一个层级目录树。

## 页面信息
{page_summaries}

## 任务
1. 按主题聚类页面（运维域/故障类型/技术栈/服务类型）
2. 生成 2-3 层级的目录树
3. 为每个分类节点生成描述
4. 识别"入口页面"（最常被引用的页面，放入顶层）

## 输出格式
JSON:
{{
  "roots": [
    {{
      "slug": "category-slug",
      "title": "分类名称",
      "description": "分类描述",
      "entry_pages": ["slug1", "slug2"],
      "children": [
        {{"slug": "page-slug", "title": "页面标题", "type": "incident"}}
      ]
    }}
  ],
  "orphan_pages": ["unclassified-slug"]
}}"""

    def __init__(self, llm_call: Any | None = None):
        self._llm_call = llm_call

    async def generate(
        self,
        page_summaries: list[dict],
        existing_index: dict | None = None,
        use_llm: bool = True,
    ) -> IndexTree:
        """生成目录树

        Args:
            page_summaries: [{slug, title, type, tags, outlinks_count, backlinks_count}]
            existing_index: 已有的目录树（用于增量更新）
            use_llm: 是否使用 LLM

        Returns:
            IndexTree
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        if use_llm and self._llm_call:
            tree = await self._generate_with_llm(page_summaries)
        else:
            tree = self._generate_by_template(page_summaries)

        tree.generated_at = now
        tree.total_pages = len(page_summaries)
        tree.stats = {
            'by_type': self._count_by_type(page_summaries),
            'tree_depth': self._max_depth(tree.roots),
            'category_count': len(tree.roots),
            'orphan_count': len(tree.orphan_pages),
        }

        return tree

    async def _generate_with_llm(
        self, summaries: list[dict],
    ) -> IndexTree:
        """LLM 生成目录树"""
        prompt = self.INDEX_GENERATE_PROMPT.format(
            page_summaries=json.dumps(summaries, ensure_ascii=False, indent=2),
        )
        try:
            response = await self._llm_call(prompt)
            data = self._parse_json_response(response)
            return self._build_tree_from_json(data)
        except Exception:
            return self._generate_by_template(summaries)

    def _generate_by_template(self, summaries: list[dict]) -> IndexTree:
        """按 type + tag 模板生成目录树"""
        # 按 type 分组
        type_groups: dict[str, list[dict]] = {}
        for s in summaries:
            t = s.get('type', 'concept')
            if t not in type_groups:
                type_groups[t] = []
            type_groups[t].append(s)

        # 类型名称映射
        type_names = {
            'incident': '故障排查',
            'runbook': '操作手册',
            'concept': '运维概念',
            'service': '服务管理',
            'host': '主机管理',
            'entity': '实体参考',
        }

        roots: list[IndexNode] = []
        for t, pages in sorted(type_groups.items()):
            category = IndexNode(
                slug=f'category-{t}',
                title=type_names.get(t, t),
                node_type='category',
                level=1,
                description=f'{type_names.get(t, t)}相关页面',
                page_count=len(pages),
            )
            # 按标签再分子类
            tag_groups = self._group_by_tags(pages)
            for tag, tag_pages in sorted(tag_groups.items()):
                if len(tag_pages) > 1 and len(tag_groups) > 1:
                    sub = IndexNode(
                        slug=f'category-{t}-{tag}',
                        title=tag,
                        node_type='category',
                        level=2,
                        page_count=len(tag_pages),
                    )
                    for p in tag_pages:
                        sub.children.append(IndexNode(
                            slug=p['slug'],
                            title=p.get('title', p['slug']),
                            node_type='page',
                            level=3,
                        ))
                    category.children.append(sub)
                else:
                    for p in tag_pages:
                        category.children.append(IndexNode(
                            slug=p['slug'],
                            title=p.get('title', p['slug']),
                            node_type='page',
                            level=2,
                        ))
            roots.append(category)

        return IndexTree(
            generated_at='',
            version=1,
            total_pages=len(summaries),
            roots=roots,
            orphan_pages=[],
        )

    def _group_by_tags(self, pages: list[dict]) -> dict[str, list[dict]]:
        """按标签分组"""
        groups: dict[str, list[dict]] = {}
        for p in pages:
            tags = p.get('tags', [])
            if tags:
                for tag in tags[:2]:  # 只用前 2 个标签
                    if tag not in groups:
                        groups[tag] = []
                    groups[tag].append(p)
            else:
                if 'other' not in groups:
                    groups['other'] = []
                groups['other'].append(p)
        return groups

    def _build_tree_from_json(self, data: dict) -> IndexTree:
        """从 JSON 构建目录树"""
        def _parse_node(d: dict, level: int) -> IndexNode:
            children = [
                _parse_node(c, level + 1)
                for c in d.get('children', [])
            ]
            return IndexNode(
                slug=d.get('slug', ''),
                title=d.get('title', ''),
                node_type='category' if children else 'page',
                level=level,
                children=children,
                description=d.get('description', ''),
                page_count=d.get('page_count', len(children)),
            )

        roots = [_parse_node(r, 1) for r in data.get('roots', [])]
        return IndexTree(
            generated_at='',
            version=1,
            total_pages=0,
            roots=roots,
            orphan_pages=data.get('orphan_pages', []),
        )

    def _parse_json_response(self, response: str) -> dict:
        import re
        response = response.strip()
        if '```' in response:
            response = re.sub(r'```\w*\n?', '', response)
        return json.loads(response)

    def to_markdown(self, tree: IndexTree) -> str:
        """将目录树渲染为 Markdown"""
        lines: list[str] = []
        lines.append(f'# Wiki 知识库目录\n')
        lines.append(f'> 生成时间: {tree.generated_at}')
        lines.append(f'> 页面总数: {tree.total_pages}')
        lines.append('')

        def _render(node: IndexNode, depth: int):
            indent = '  ' * (depth - 1)
            if node.node_type == 'category':
                lines.append(f'{indent}- **{node.title}** ({node.page_count} 页)')
                if node.description:
                    lines.append(f'{indent}  {node.description}')
            else:
                lines.append(f'{indent}- [[{node.slug}|{node.title}]]')
            for child in node.children:
                _render(child, depth + 1)

        for root in tree.roots:
            _render(root, 1)

        if tree.orphan_pages:
            lines.append('\n## 未分类页面')
            for slug in tree.orphan_pages:
                lines.append(f'- [[{slug}]]')

        return '\n'.join(lines)

    @staticmethod
    def _count_by_type(summaries: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in summaries:
            t = s.get('type', 'concept')
            counts[t] = counts.get(t, 0) + 1
        return counts

    @staticmethod
    def _max_depth(nodes: list[IndexNode]) -> int:
        if not nodes:
            return 0
        return 1 + max((IndexGenerator._max_depth(n.children) for n in nodes), default=0)


_index_generator: IndexGenerator | None = None


def get_index_generator(llm_call: Any | None = None) -> IndexGenerator:
    """获取 IndexGenerator 单例"""
    global _index_generator
    if _index_generator is None:
        _index_generator = IndexGenerator(llm_call=llm_call)
    return _index_generator