"""经验蒸馏器（P4-2）

批量分析 Wiki 页面，蒸馏出跨文档的运维经验模式。

流程:
1. 收集所有 Wiki 页面摘要（slug, type, tags, 关键实体）
2. 按主题聚类（故障模式 / 处置策略 / 最佳实践）
3. LLM 批量蒸馏，生成经验页面
4. 经验页面存储为 wiki:experience:{slug}

经验类型:
- pattern: 重复出现的故障模式
- best_practice: 最佳实践
- anti_pattern: 反模式 / 常见错误
- lesson: 经验教训
- checklist: 操作清单
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class ExperienceInsight:
    """蒸馏出的经验洞见"""
    insight_type: str          # pattern | best_practice | anti_pattern | lesson | checklist
    title: str
    slug: str
    description: str
    severity: str              # critical | high | medium | low
    source_slugs: list[str]    # 来源 Wiki 页面
    supporting_evidence: str   # 支撑证据
    related_tags: list[str] = field(default_factory=list)


@dataclass
class DistillationResult:
    """经验蒸馏结果"""
    generated_at: str
    source_page_count: int
    insights: list[ExperienceInsight]
    experience_pages: list[dict]  # 生成的经验页面
    stats: dict = field(default_factory=dict)


class ExperienceDistiller:
    """经验蒸馏器

    从多个 Wiki 页面中蒸馏跨文档的运维经验模式。
    使用 LLM 进行语义聚类和模式识别。
    """

    DISTILL_PROMPT = """你是一个资深运维专家，负责从运维知识库中提炼跨文档的经验模式。

## 任务
分析以下 Wiki 页面摘要，识别跨文档的运维经验模式。请关注:
1. **重复故障模式** (pattern): 在不同文档中反复出现的故障类型
2. **最佳实践** (best_practice): 经过验证的有效做法
3. **反模式/常见错误** (anti_pattern): 容易犯的错误操作
4. **经验教训** (lesson): 从事故中总结的教训
5. **操作清单** (checklist): 可操作的检查清单

## Wiki 页面摘要
{page_summaries}

## 输出格式
JSON:
{{
  "insights": [
    {{
      "insight_type": "pattern|best_practice|anti_pattern|lesson|checklist",
      "title": "经验标题",
      "slug": "experience-kebab-case-slug",
      "description": "详细描述（2-3句话）",
      "severity": "critical|high|medium|low",
      "source_slugs": ["source-page-1", "source-page-2"],
      "supporting_evidence": "支撑该经验的原文证据摘要",
      "related_tags": ["tag1", "tag2"]
    }}
  ]
}}

## 规则
- 每个 insight 必须有至少 2 个来源页面支撑
- 识别至少 3 个 insight，最多 15 个
- 如果素材不足，返回空数组
- 优先识别 critical/high 严重性的经验"""

    def __init__(self, llm_call: Any | None = None):
        self._llm_call = llm_call

    async def distill(
        self,
        page_summaries: list[dict],
        *,
        existing_insights: list[dict] | None = None,
        use_llm: bool = True,
    ) -> DistillationResult:
        """蒸馏经验

        Args:
            page_summaries: [{slug, title, type, tags, summary, entities, incident_count}]
            existing_insights: 已有的经验洞见（用于增量更新）
            use_llm: 是否使用 LLM

        Returns:
            DistillationResult
        """
        now = datetime.now(timezone.utc).isoformat()

        if use_llm and self._llm_call and len(page_summaries) >= 3:
            insights = await self._distill_with_llm(page_summaries)
        else:
            insights = self._distill_by_template(page_summaries)

        # 合并已有洞见
        if existing_insights:
            insights = self._merge_insights(insights, existing_insights)

        # 生成经验 Wiki 页面
        experience_pages = self._build_experience_pages(insights, now)

        # 统计
        stats = {
            'total_insights': len(insights),
            'by_type': self._count_by_type(insights),
            'by_severity': self._count_by_severity(insights),
            'avg_source_pages': round(
                sum(len(i.source_slugs) for i in insights) / max(len(insights), 1), 1
            ),
        }

        return DistillationResult(
            generated_at=now,
            source_page_count=len(page_summaries),
            insights=insights,
            experience_pages=experience_pages,
            stats=stats,
        )

    async def _distill_with_llm(
        self, summaries: list[dict],
    ) -> list[ExperienceInsight]:
        """LLM 蒸馏"""
        prompt = self.DISTILL_PROMPT.format(
            page_summaries=json.dumps(summaries, ensure_ascii=False, indent=2),
        )
        try:
            response = await self._llm_call(prompt)
            data = self._parse_json_response(response)
            insights_data = data.get('insights', [])
            return [
                ExperienceInsight(
                    insight_type=i.get('insight_type', 'pattern'),
                    title=i.get('title', ''),
                    slug=i.get('slug', ''),
                    description=i.get('description', ''),
                    severity=i.get('severity', 'medium'),
                    source_slugs=i.get('source_slugs', []),
                    supporting_evidence=i.get('supporting_evidence', ''),
                    related_tags=i.get('related_tags', []),
                )
                for i in insights_data
                if i.get('title') and len(i.get('source_slugs', [])) >= 2
            ]
        except Exception as e:
            logger.warning("experience_distill_llm_failed", error=str(e))
            return self._distill_by_template(summaries)

    def _distill_by_template(
        self, summaries: list[dict],
    ) -> list[ExperienceInsight]:
        """模板归因蒸馏（无 LLM 兜底）

        按 tag 共现和类型聚类生成经验。
        """
        insights: list[ExperienceInsight] = []

        # 按类型分组
        by_type: dict[str, list[dict]] = {}
        for s in summaries:
            t = s.get('type', 'concept')
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(s)

        # 故障页面 → 提取故障模式
        incidents = by_type.get('incident', [])
        if len(incidents) >= 2:
            # 按标签聚类
            tag_clusters = self._cluster_by_tags(incidents)
            for tag, cluster in tag_clusters.items():
                if len(cluster) >= 2:
                    insights.append(ExperienceInsight(
                        insight_type='pattern',
                        title=f'{tag} 相关故障模式',
                        slug=f'experience-pattern-{self._slugify(tag)}',
                        description=f'在 {len(cluster)} 个故障页面中发现了与 {tag} 相关的重复模式',
                        severity='high',
                        source_slugs=[c['slug'] for c in cluster],
                        supporting_evidence=f'涉及 {len(cluster)} 个相关故障',
                        related_tags=[tag],
                    ))

        # Runbook 页面 → 提取最佳实践
        runbooks = by_type.get('runbook', [])
        if len(runbooks) >= 2:
            tag_clusters = self._cluster_by_tags(runbooks)
            for tag, cluster in tag_clusters.items():
                if len(cluster) >= 2:
                    insights.append(ExperienceInsight(
                        insight_type='best_practice',
                        title=f'{tag} 处置最佳实践',
                        slug=f'experience-best-practice-{self._slugify(tag)}',
                        description=f'从 {len(cluster)} 个操作手册中提炼的 {tag} 相关最佳实践',
                        severity='medium',
                        source_slugs=[c['slug'] for c in cluster],
                        supporting_evidence=f'覆盖 {len(cluster)} 个操作场景',
                        related_tags=[tag],
                    ))

        return insights

    def _merge_insights(
        self,
        new_insights: list[ExperienceInsight],
        existing: list[dict],
    ) -> list[ExperienceInsight]:
        """合并新旧洞见"""
        merged = list(new_insights)
        for e in existing:
            if e.get('slug') not in {i.slug for i in new_insights}:
                merged.append(ExperienceInsight(
                    insight_type=e.get('insight_type', 'pattern'),
                    title=e.get('title', ''),
                    slug=e.get('slug', ''),
                    description=e.get('description', ''),
                    severity=e.get('severity', 'medium'),
                    source_slugs=e.get('source_slugs', []),
                    supporting_evidence=e.get('supporting_evidence', ''),
                    related_tags=e.get('related_tags', []),
                ))
        return merged

    def _build_experience_pages(
        self, insights: list[ExperienceInsight], now: str,
    ) -> list[dict]:
        """为每个经验洞见生成 Wiki 页面"""
        pages: list[dict] = []
        for i, insight in enumerate(insights):
            body = self._build_experience_body(insight, now)
            pages.append({
                'slug': insight.slug,
                'title': insight.title,
                'type': 'concept',
                'tags': [insight.insight_type, insight.severity] + insight.related_tags,
                'body_md': body,
                'review_status': 'auto',
                'sources': [
                    {'doc_id': s, 'title': s, 'type': 'wiki_page'}
                    for s in insight.source_slugs
                ],
            })
        return pages

    def _build_experience_body(
        self, insight: ExperienceInsight, now: str,
    ) -> str:
        """构建经验页面正文"""
        type_labels = {
            'pattern': '故障模式',
            'best_practice': '最佳实践',
            'anti_pattern': '反模式/常见错误',
            'lesson': '经验教训',
            'checklist': '操作清单',
        }
        type_label = type_labels.get(insight.insight_type, '经验')

        lines: list[str] = []
        lines.append(f'# {insight.title}\n')
        lines.append(f'> 类型: {type_label} | 严重程度: {insight.severity}')
        lines.append(f'> 蒸馏时间: {now}')
        lines.append('')

        # 概述
        lines.append('## 概述')
        lines.append('')
        lines.append(insight.description)
        lines.append('')

        # 支撑证据
        lines.append('## 支撑证据')
        lines.append('')
        lines.append(insight.supporting_evidence)
        lines.append('')

        # 来源页面
        lines.append('## 来源页面')
        lines.append('')
        for slug in insight.source_slugs:
            lines.append(f'- [[{slug}]]')
        lines.append('')

        # 相关标签
        if insight.related_tags:
            lines.append('## 相关标签')
            lines.append('')
            lines.append(', '.join(f'`{t}`' for t in insight.related_tags))
            lines.append('')

        return '\n'.join(lines)

    @staticmethod
    def _cluster_by_tags(
        pages: list[dict],
    ) -> dict[str, list[dict]]:
        """按标签聚类"""
        clusters: dict[str, list[dict]] = {}
        for p in pages:
            tags = p.get('tags', [])
            for tag in tags:
                if tag not in clusters:
                    clusters[tag] = []
                clusters[tag].append(p)
        # 只保留 >= 2 的聚类
        return {k: v for k, v in clusters.items() if len(v) >= 2}

    @staticmethod
    def _slugify(text: str) -> str:
        """生成 slug"""
        slug = re.sub(r'[^\w\s-]', '', text.lower())
        slug = re.sub(r'[-\s]+', '-', slug)
        return slug.strip('-')[:60]

    @staticmethod
    def _parse_json_response(response: str) -> dict:
        """解析 JSON 响应"""
        response = response.strip()
        if '```' in response:
            response = re.sub(r'```\w*\n?', '', response)
        return json.loads(response)

    @staticmethod
    def _count_by_type(insights: list[ExperienceInsight]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for i in insights:
            counts[i.insight_type] = counts.get(i.insight_type, 0) + 1
        return counts

    @staticmethod
    def _count_by_severity(insights: list[ExperienceInsight]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for i in insights:
            counts[i.severity] = counts.get(i.severity, 0) + 1
        return counts


_experience_distiller: ExperienceDistiller | None = None


def get_experience_distiller(llm_call: Any | None = None) -> ExperienceDistiller:
    """获取 ExperienceDistiller 单例"""
    global _experience_distiller
    if _experience_distiller is None:
        _experience_distiller = ExperienceDistiller(llm_call=llm_call)
    return _experience_distiller
