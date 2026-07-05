"""Wiki-based Q&A（P1-2）— Karpathy LLM Wiki 的 Query 流程

区别于 RAG（每次检索原文片段），本模块基于**已编译好的 wiki 页面**回答：
- 从 index.md 出发，识别问题相关页面类型与 slug
- 加载相关 wiki 页面（不是 raw 原文）
- LLM 基于 wiki 回答，引用 [[slug]] 作为来源
- 若回答中产生新事实 → 回写到对应 wiki 页面（知识复利）

如果 wiki 中无相关页面，提示"知识库不足，建议上传相关文档"（不强行 RAG 原文）。

召回策略（三层）：
1. 关键词命中：用户问题分词后在 wiki 页面 title/tags/body 中匹配
2. Backlink 扩展：被召回页面的入链页面作为补充上下文
3. 类型路由：故障类问题优先召回 incident/runbook；概念类优先 concept
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

from app.config import get_settings
from app.core.llm import ChatMessage, get_llm_client, embed_query, embed_texts
from app.knowledge.wikilink import (
    render_wikilinks_text,
    get_backlinks,
)
from app.knowledge.wiki_index import list_wiki_pages, _key_from_slug
from app.storage.version_control import get_version_control

logger = structlog.get_logger()

# 尝试 numpy 加速余弦相似度（与 SearchEngine 保持一致）
try:  # pragma: no cover
    import numpy as np  # type: ignore[import-untyped]

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False

# wiki 页面 embedding 内存缓存：slug → (version, embedding)
# 版本变化时自动失效（无需手动清理）
_wiki_emb_cache: dict[str, tuple[int, list[float]]] = {}


@dataclass
class WikiPageHit:
    """召回的 wiki 页面"""

    slug: str
    title: str
    type: str
    score: float
    snippet: str  # 命中片段（用于上下文）


@dataclass
class WikiQueryResult:
    """wiki Q&A 结果"""

    question: str
    answer: str
    cited_slugs: list[str] = field(default_factory=list)
    recalled_pages: list[WikiPageHit] = field(default_factory=list)
    insufficient_knowledge: bool = False
    error: str = ""


# ────────── 关键词分词（极简，无外部依赖）──────────

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_STOP_WORDS = {
    # 英文停用词
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "and",
    "or",
    "but",
    "if",
    "then",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "with",
    "without",
    "from",
    "by",
    "as",
    "into",
    "through",
    "what",
    "how",
    "why",
    "when",
    "where",
    "who",
    "which",
    "do",
    "does",
    "did",
    "can",
    "could",
    "should",
    "would",
    "will",
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    # 中文停用词
    "的",
    "是",
    "在",
    "了",
    "有",
    "和",
    "与",
    "或",
    "但",
    "如果",
    "什么",
    "怎么",
    "为什么",
    "何时",
    "哪里",
    "哪个",
    "谁",
    "我",
    "你",
    "他",
    "她",
    "它",
    "我们",
    "你们",
    "他们",
    "请",
    "帮",
    "一下",
    "吗",
    "呢",
    "啊",
}


def _tokenize(text: str) -> list[str]:
    """简单分词：英文按空格/标点切，中文按字符切"""
    tokens: list[str] = []
    # 英文：小写化后按非字母数字分割
    en_parts = re.split(r"[^a-zA-Z0-9]+", text.lower())
    for p in en_parts:
        if p and p not in _STOP_WORDS and len(p) >= 2:
            tokens.append(p)
    # 中文：每个汉字单独成词（粗粒度）
    for ch in text:
        if _CJK_RE.match(ch):
            tokens.append(ch)
    # 中文双字组合（提升匹配度）
    cjk_chars = [ch for ch in text if _CJK_RE.match(ch)]
    for i in range(len(cjk_chars) - 1):
        tokens.append(cjk_chars[i] + cjk_chars[i + 1])
    return tokens


# ────────── 召回引擎 ──────────


async def recall_pages(
    question: str,
    limit: int = 5,
    min_score: float = 5.0,
    *,
    use_vector: bool = True,
    rrf_k: int = 60,
) -> list[WikiPageHit]:
    """从 wiki 召回与问题相关的页面

    双路召回 + RRF 融合（P2-1.1）：
    - 关键词路径：title +5 / tags +3 / body +1
    - 向量路径：余弦相似度（依赖 LLM embedding，未配置时自动降级）
    - 融合：Reciprocal Rank Fusion，score(d) = Σ 1/(k + rank_i(d))

    Args:
        question: 用户问题
        limit: 返回数量上限
        min_score: 关键词路径最低分数阈值（避免噪音召回）
        use_vector: 是否启用向量召回（默认 True，未配置 embedding 时自动降级）
        rrf_k: RRF 平滑常数，默认 60（业界经验值）
    """
    tokens = _tokenize(question)

    pages = list_wiki_pages(limit=1000)
    if not pages:
        return []

    vc = get_version_control()

    # slug → body_md 缓存（关键词路径与向量路径共用，避免重复 I/O）
    body_cache: dict[str, str] = {}

    # ── 关键词召回路径 ──
    keyword_hits: dict[str, WikiPageHit] = {}
    if tokens:
        for p in pages:
            if p["slug"] == "index":
                continue
            latest = vc.get_latest(_key_from_slug(p["slug"]))
            if not latest:
                continue
            title = (p.get("title") or p["slug"]).lower()
            tags = [t.lower() for t in p.get("tags", [])]
            body_md = latest["content"]
            body_cache[p["slug"]] = body_md
            body_text = render_wikilinks_text(body_md).lower()

            score = 0.0
            matched_tokens: set[str] = set()
            for tok in tokens:
                if tok in matched_tokens:
                    continue
                if tok in title:
                    score += 5
                    matched_tokens.add(tok)
                elif any(tok in t for t in tags):
                    score += 3
                    matched_tokens.add(tok)
                elif tok in body_text:
                    score += 1
                    matched_tokens.add(tok)

            if score < min_score:
                continue

            snippet = _extract_snippet(body_md, list(matched_tokens))
            keyword_hits[p["slug"]] = WikiPageHit(
                slug=p["slug"],
                title=p.get("title") or p["slug"],
                type=p["type"],
                score=score,
                snippet=snippet,
            )

    # ── 向量召回路径（P2-1.1） ──
    vector_hits: dict[str, WikiPageHit] = {}
    vector_used = False
    if use_vector:
        try:
            query_emb = await embed_query(question)
            if query_emb:
                wiki_embs = await _get_wiki_embeddings(pages, vc, body_cache)
                if wiki_embs:
                    scored = _rank_by_cosine(query_emb, wiki_embs)
                    # 取更多候选以保证 RRF 融合后 top-K 质量
                    candidate_limit = max(limit * 3, 15)
                    for slug, sim in scored[:candidate_limit]:
                        if slug == "index":
                            continue
                        p = next((x for x in pages if x["slug"] == slug), None)
                        if not p:
                            continue
                        body_md = body_cache.get(slug, "")
                        snippet = (
                            _extract_snippet(body_md, [question])
                            if body_md
                            else ""
                        )
                        vector_hits[slug] = WikiPageHit(
                            slug=slug,
                            title=p.get("title") or slug,
                            type=p["type"],
                            score=float(sim),
                            snippet=snippet,
                        )
                    vector_used = bool(vector_hits)
        except Exception as e:
            logger.warning("wiki_vector_recall_failed", error=str(e))

    # ── 融合 ──
    if not vector_used:
        # 向量不可用 → 仅返回关键词结果（兼容旧行为）
        merged = list(keyword_hits.values())
        merged.sort(key=lambda h: h.score, reverse=True)
        return merged[:limit]

    if not keyword_hits:
        # 仅向量召回（例如问题为纯中文且分词后无 token 匹配）
        merged = list(vector_hits.values())
        merged.sort(key=lambda h: h.score, reverse=True)
        return merged[:limit]

    # RRF 融合
    kw_ranked = sorted(
        keyword_hits.items(), key=lambda kv: kv[1].score, reverse=True
    )
    vec_ranked = sorted(
        vector_hits.items(), key=lambda kv: kv[1].score, reverse=True
    )
    kw_rank = {slug: i + 1 for i, (slug, _) in enumerate(kw_ranked)}
    vec_rank = {slug: i + 1 for i, (slug, _) in enumerate(vec_ranked)}

    all_slugs = set(kw_rank.keys()) | set(vec_rank.keys())
    merged_hits: list[WikiPageHit] = []
    for slug in all_slugs:
        kw_hit = keyword_hits.get(slug)
        vec_hit = vector_hits.get(slug)
        rrf_score = 0.0
        if slug in kw_rank:
            rrf_score += 1.0 / (rrf_k + kw_rank[slug])
        if slug in vec_rank:
            rrf_score += 1.0 / (rrf_k + vec_rank[slug])
        # 取较丰富的元信息（关键词路径通常带 snippet）
        meta_hit = kw_hit or vec_hit
        merged_hits.append(
            WikiPageHit(
                slug=slug,
                title=meta_hit.title,
                type=meta_hit.type,
                score=rrf_score,
                snippet=meta_hit.snippet,
            )
        )

    merged_hits.sort(key=lambda h: h.score, reverse=True)
    return merged_hits[:limit]


async def _get_wiki_embeddings(
    pages: list[dict],
    vc,
    body_cache: dict[str, str],
) -> dict[str, list[float]]:
    """获取 wiki 页面 embedding，带版本级内存缓存

    缓存键：(slug, version)。version 变化时自动失效重算。
    """
    result: dict[str, list[float]] = {}
    slugs_to_embed: list[str] = []
    texts_to_embed: list[str] = []
    versions_to_embed: list[int] = []

    for p in pages:
        slug = p["slug"]
        if slug == "index":
            continue
        version = p.get("version", 0)
        cached = _wiki_emb_cache.get(slug)
        if cached and cached[0] == version:
            result[slug] = cached[1]
            continue

        # 获取正文
        body_md = body_cache.get(slug)
        if body_md is None:
            latest = vc.get_latest(_key_from_slug(slug))
            if not latest:
                continue
            body_md = latest["content"]
            body_cache[slug] = body_md

        body = _strip_frontmatter(body_md)
        # 拼接 title + body，截断到 2000 字符（避免 token 过多）
        text = f"{p.get('title') or slug}\n{body}"[:2000]
        slugs_to_embed.append(slug)
        texts_to_embed.append(text)
        versions_to_embed.append(version)

    if not texts_to_embed:
        return result

    embs = await embed_texts(texts_to_embed)
    if not embs:
        return result

    for slug, emb, version in zip(slugs_to_embed, embs, versions_to_embed):
        _wiki_emb_cache[slug] = (version, emb)
        result[slug] = emb

    return result


def _rank_by_cosine(
    query_emb: list[float],
    wiki_embs: dict[str, list[float]],
) -> list[tuple[str, float]]:
    """计算查询向量与所有 wiki 向量的余弦相似度，按降序返回"""
    if not wiki_embs:
        return []

    if _HAS_NUMPY:
        q_vec = np.asarray(query_emb, dtype=np.float32)
        q_norm = float(np.linalg.norm(q_vec))
        if q_norm == 0:
            return []
        q_unit = q_vec / q_norm
        scored: list[tuple[str, float]] = []
        for slug, emb in wiki_embs.items():
            d_vec = np.asarray(emb, dtype=np.float32)
            if d_vec.shape != q_vec.shape:
                continue
            d_norm = float(np.linalg.norm(d_vec))
            if d_norm == 0:
                continue
            score = float(np.dot(q_unit, d_vec / d_norm))
            scored.append((slug, score))
    else:
        scored = []
        for slug, emb in wiki_embs.items():
            score = _cosine_similarity(query_emb, emb)
            scored.append((slug, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """纯 Python 余弦相似度（numpy 不可用时兜底）"""
    import math

    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def clear_wiki_embedding_cache() -> None:
    """清空 wiki embedding 内存缓存（测试或运维使用）"""
    _wiki_emb_cache.clear()


def _extract_snippet(md: str, tokens: list[str], window: int = 200) -> str:
    """提取首个命中 token 周围的文本片段"""
    if not tokens:
        return md[:window]
    body = render_wikilinks_text(md)
    lower = body.lower()
    for tok in tokens:
        idx = lower.find(tok.lower())
        if idx >= 0:
            start = max(0, idx - window // 2)
            end = min(len(body), idx + len(tok) + window // 2)
            return body[start:end]
    return body[:window]


# ────────── 答案生成 ──────────


class WikiQAEngine:
    """基于 wiki 的问答引擎"""

    def __init__(self) -> None:
        self.llm = get_llm_client()
        self.settings = get_settings()
        self.vc = get_version_control()

    async def answer(
        self,
        question: str,
        *,
        recall_limit: int = 5,
        expand_backlinks: bool = True,
    ) -> WikiQueryResult:
        """回答用户问题

        流程（AGENTS.md §六）：
            1. 召回相关 wiki 页面
            2. 若召回为空 → 提示知识库不足
            3. 加载页面正文作为上下文
            4. LLM 基于上下文回答，引用 [[slug]]
        """
        # 1. 召回
        recalled = await recall_pages(question, limit=recall_limit)
        if not recalled:
            return WikiQueryResult(
                question=question,
                answer="知识库中暂无相关 wiki 页面。建议先上传相关运维文档，由 wiki_compiler 编译后再提问。",
                insufficient_knowledge=True,
            )

        # 2. backlink 扩展（最多补 2 个）
        if expand_backlinks:
            existing_slugs = {h.slug for h in recalled}
            for hit in list(recalled):
                for back in get_backlinks(hit.slug):
                    if (
                        back.source_slug in existing_slugs
                        or back.source_slug == "index"
                    ):
                        continue
                    latest = self.vc.get_latest(_key_from_slug(back.source_slug))
                    if not latest:
                        continue
                    recalled.append(
                        WikiPageHit(
                            slug=back.source_slug,
                            title=back.display,
                            type="",
                            score=hit.score * 0.3,  # 较低权重
                            snippet=_extract_snippet(latest["content"], [question]),
                        )
                    )
                    existing_slugs.add(back.source_slug)
                    if len(recalled) >= recall_limit + 2:
                        break
                if len(recalled) >= recall_limit + 2:
                    break

        # 3. 加载页面正文
        contexts: list[str] = []
        cited: list[str] = []
        for hit in recalled:
            latest = self.vc.get_latest(_key_from_slug(hit.slug))
            if not latest:
                continue
            body = _strip_frontmatter(latest["content"])
            contexts.append(f"## [[{hit.slug}]] — {hit.title}\n\n{body}")
            cited.append(hit.slug)

        if not contexts:
            return WikiQueryResult(
                question=question,
                answer="知识库中相关 wiki 页面无法加载。",
                recalled_pages=recalled,
                insufficient_knowledge=True,
            )

        # 4. LLM 回答
        answer = await self._llm_answer(question, contexts)
        if not answer:
            answer = "已召回以下 wiki 页面，但 LLM 暂时无法生成回答：\n" + "\n".join(
                f"- [[{h.slug}]] {h.title}" for h in recalled
            )

        return WikiQueryResult(
            question=question,
            answer=answer,
            cited_slugs=cited,
            recalled_pages=recalled,
        )

    async def _llm_answer(self, question: str, contexts: list[str]) -> str:
        """让 LLM 基于 wiki 上下文回答"""
        system = (
            "你是 OpsKG Wiki 管理员。基于已编译的 wiki 页面回答用户问题。"
            "回答中引用相关页面时使用 [[slug]] 形式。"
            "只基于提供的 wiki 内容回答，不要编造未在 wiki 中出现的事实。"
            "如果 wiki 内容不足以完整回答，明确指出缺口。"
        )
        prompt = (
            f"# 用户问题\n{question}\n\n"
            f"# 相关 wiki 页面\n" + "\n\n".join(contexts) + "\n\n# 回答要求\n"
            "1. 直接回答问题，不要复述问题\n"
            "2. 在引用具体页面信息时，用 [[slug]] 标注来源\n"
            "3. 如有排查步骤，分点列出\n"
            "4. 若 wiki 内容不足，明确说明"
        )
        try:
            messages = [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=prompt),
            ]
            resp = await self.llm.chat(
                messages=messages,
                temperature=0.2,
                max_tokens=self.settings.llm_max_tokens,
            )
            return (resp.text or "").strip()
        except Exception as e:
            logger.warning("wiki_qa_llm_failed", error=str(e))
            return ""


# ────────── 内部工具 ──────────


def _strip_frontmatter(md: str) -> str:
    """剥离 frontmatter，返回正文"""
    if not md.startswith("---"):
        return md
    parts = md.split("---", 2)
    if len(parts) < 3:
        return md
    return parts[2].lstrip("\n")


# ────────── 单例 ──────────

_qa: WikiQAEngine | None = None


def get_wiki_qa_engine() -> WikiQAEngine:
    global _qa
    if _qa is None:
        _qa = WikiQAEngine()
    return _qa
