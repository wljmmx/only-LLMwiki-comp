"""LLM 段落语义分类器

替换纯关键词匹配，使用 LLM 对段落进行高精度语义分类。
支持批量处理（每批 BATCH_SIZE 个段落），结构化 JSON 输出，
并在 LLM 不可用时降级到关键词匹配。

分类类别（与原关键词分类器一致）：
- overview:   概述/简介/背景
- cause:     原因/成因
- analysis:  分析/排查/诊断
- solution:  解决/方案/处置
- config:    配置/参数/设置
- steps:     步骤/流程/操作
- example:   示例/例如
- warning:   注意/警告
- reference: 参考/参见
- general:   通用/未分类
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from app.core.llm.base import LLMClient

logger = structlog.get_logger()

# LLM 分类的系统提示
SYSTEM_PROMPT = """你是一名专业的技术文档分析师。请为每个段落判断其语义类别。

可选类别：
- overview: 概述、简介、背景、总览
- cause: 原因、成因、根源
- analysis: 分析、排查、诊断、定位
- solution: 解决、方案、处置、修复
- config: 配置、参数、设置、选项
- steps: 步骤、流程、操作过程
- example: 示例、例如、比如
- warning: 注意、警告、重要提示
- reference: 参考、参见、引用来源
- general: 其他通用内容

严格输出 JSON 格式，不要输出任何其他文本。"""

# 每批处理的段落数
BATCH_SIZE = 20

# LLM 调用超时（秒）
LLM_TIMEOUT = 120

# 分类提示模板
USER_PROMPT_TEMPLATE = """请为以下 {count} 个段落分别判断语义类别。

段落列表：
{paragraphs_text}

请返回 JSON 数组，每个元素格式为：
{{"index": 0, "class": "overview", "confidence": 0.95}}

要求：
1. class 必须是上述 10 个类别之一
2. confidence 为 0-1 的置信度
3. 按 index 顺序返回所有段落的分类结果
4. 严格 JSON 格式，不要加注释"""


@dataclass
class ClassifiedParagraph:
    """分类结果"""
    para_idx: int
    text_preview: str
    class_name: str
    confidence: float


# 关键词分类器的关键词表（用于降级）
KEYWORD_CLASSIFIERS: dict[str, list[str]] = {
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


def keyword_classify(paragraphs: list[str]) -> list[ClassifiedParagraph]:
    """关键词分类（降级方案）

    与原 _classify_paragraphs 逻辑一致，作为 LLM 不可用时的 fallback。
    """
    results: list[ClassifiedParagraph] = []
    for idx, para in enumerate(paragraphs):
        stripped = para.strip()
        if not stripped:
            continue

        best_class = 'general'
        best_score = 0

        for cls_name, keywords in KEYWORD_CLASSIFIERS.items():
            matches = sum(1 for kw in keywords if kw in stripped.lower())
            if matches > best_score:
                best_score = matches
                best_class = cls_name

        max_kw = len(KEYWORD_CLASSIFIERS.get(best_class, []))
        confidence = min(best_score / max(max_kw, 1), 1.0) if best_score > 0 else 0.3

        results.append(ClassifiedParagraph(
            para_idx=idx,
            text_preview=stripped[:120],
            class_name=best_class,
            confidence=round(confidence, 2),
        ))

    return results


async def classify_paragraphs_with_llm(
    paragraphs: list[str],
    llm_client: "LLMClient",
    batch_size: int = BATCH_SIZE,
) -> list[ClassifiedParagraph]:
    """使用 LLM 对段落进行语义分类

    Args:
        paragraphs: 段落列表
        llm_client: LLM 客户端实例
        batch_size: 每批处理的段落数

    Returns:
        ClassifiedParagraph 列表
    """
    if not paragraphs:
        return []

    # 过滤空段落
    valid_indices = [(i, p) for i, p in enumerate(paragraphs) if p.strip()]
    if not valid_indices:
        return []

    results: list[ClassifiedParagraph] = []

    # 分批处理
    for batch_start in range(0, len(valid_indices), batch_size):
        batch = valid_indices[batch_start:batch_start + batch_size]
        batch_indices = [idx for idx, _ in batch]
        batch_paragraphs = [p for _, p in batch]

        try:
            batch_results = await _classify_batch(
                batch_paragraphs, batch_indices, llm_client,
            )
            results.extend(batch_results)
        except Exception as e:
            logger.warning(
                "llm_classify_batch_failed",
                batch_start=batch_start,
                batch_size=len(batch),
                error=str(e),
            )
            # 降级：对该批次使用关键词分类
            for orig_idx, para in batch:
                kw_results = keyword_classify([para])
                if kw_results:
                    kw = kw_results[0]
                    results.append(ClassifiedParagraph(
                        para_idx=orig_idx,
                        text_preview=kw.text_preview,
                        class_name=kw.class_name,
                        confidence=kw.confidence,
                    ))

    # 按 para_idx 排序
    results.sort(key=lambda r: r.para_idx)
    return results


async def _classify_batch(
    paragraphs: list[str],
    original_indices: list[int],
    llm_client: "LLMClient",
) -> list[ClassifiedParagraph]:
    """对单批段落进行 LLM 分类"""
    from app.core.llm.base import ChatMessage

    # 构建段落文本
    paragraphs_text = "\n".join(
        f"[{i}] {p[:300]}" for i, p in enumerate(paragraphs)
    )

    prompt = USER_PROMPT_TEMPLATE.format(
        count=len(paragraphs),
        paragraphs_text=paragraphs_text,
    )

    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=prompt),
    ]

    # LLM 调用
    resp = await asyncio.wait_for(
        llm_client.chat(
            messages=messages,
            temperature=0.1,  # 低温度确保分类稳定
            max_tokens=2000,
        ),
        timeout=LLM_TIMEOUT,
    )

    raw_text = resp.text or ""

    # 解析 JSON 结果
    parsed = _parse_llm_response(raw_text)

    results: list[ClassifiedParagraph] = []
    seen_indices: set[int] = set()

    for item in parsed:
        try:
            batch_idx = int(item.get("index", -1))
            class_name = str(item.get("class", "general")).strip().lower()
            confidence = float(item.get("confidence", 0.5))

            if 0 <= batch_idx < len(original_indices):
                orig_idx = original_indices[batch_idx]
                # 验证类别合法性
                if class_name not in KEYWORD_CLASSIFIERS and class_name != 'general':
                    class_name = 'general'

                confidence = max(0.0, min(1.0, confidence))
                stripped = paragraphs[batch_idx].strip()

                results.append(ClassifiedParagraph(
                    para_idx=orig_idx,
                    text_preview=stripped[:120],
                    class_name=class_name,
                    confidence=round(confidence, 2),
                ))
                seen_indices.add(batch_idx)
        except (ValueError, TypeError):
            continue

    # 未被 LLM 返回的段落，用关键词分类补充
    for i, orig_idx in enumerate(original_indices):
        if i not in seen_indices:
            kw_results = keyword_classify([paragraphs[i]])
            if kw_results:
                kw = kw_results[0]
                results.append(ClassifiedParagraph(
                    para_idx=orig_idx,
                    text_preview=kw.text_preview,
                    class_name=kw.class_name,
                    confidence=kw.confidence,
                ))

    return results


def _parse_llm_response(text: str) -> list[dict]:
    """解析 LLM 返回的 JSON 文本

    处理常见的 LLM 输出格式问题：
    - 包裹在 ```json ... ``` 中
    - 包裹在 ``` ... ``` 中
    - 直接 JSON
    - 多个 JSON 对象拼接
    """
    if not text:
        return []

    # 尝试直接解析
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "results" in data:
            return data["results"] if isinstance(data["results"], list) else []
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块中提取
    json_match = None
    for pattern in [
        r'```json\s*\n?(.*?)\n?```',
        r'```\s*\n?(.*?)\n?```',
        r'`([\s\S]*?)`',
    ]:
        import re
        m = re.search(pattern, text, re.DOTALL)
        if m:
            json_str = m.group(1).strip()
            try:
                data = json.loads(json_str)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "results" in data:
                    return data["results"] if isinstance(data["results"], list) else []
            except json.JSONDecodeError:
                continue

    # 尝试提取第一个 [ ... ] 数组
    import re
    bracket_match = re.search(r'\[[\s\S]*\]', text)
    if bracket_match:
        try:
            data = json.loads(bracket_match.group())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    logger.warning(
        "llm_classify_parse_failed",
        preview=text[:200],
    )
    return []


def to_cleaned_dict(results: list[ClassifiedParagraph]) -> list[dict]:
    """将 ClassifiedParagraph 转换为 CleanedDocument 兼容的 dict 格式

    输出格式与原 _classify_paragraphs 一致：
    [{para_idx, text_preview, class, confidence}, ...]
    """
    return [
        {
            "para_idx": r.para_idx,
            "text_preview": r.text_preview,
            "class": r.class_name,
            "confidence": r.confidence,
        }
        for r in sorted(results, key=lambda x: x.para_idx)
    ]


def _get_cache_key(paragraphs: list[str]) -> str:
    """计算段落列表的缓存键"""
    combined = "||".join(p[:100] for p in paragraphs)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
