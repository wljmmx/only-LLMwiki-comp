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
from typing import Any

import structlog

from app.config import get_settings
from app.core.llm import ChatMessage, embed_query, embed_texts, get_llm_client
from app.knowledge.wiki_index import _key_from_slug, list_wiki_pages
from app.knowledge.wikilink import (
    get_backlinks,
    render_wikilinks_text,
)
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
    # S12-1 知识复利：回写的新事实记录
    writebacks: list[dict] = field(default_factory=list)


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


def _graph_recall(
    tokens: list[str],
    pages: list[dict],
) -> dict[str, WikiPageHit]:
    """P3-1: 图谱召回路径 — 通过知识图谱邻居扩展召回

    策略：
    1. 用问题关键词在图谱中 search_entities 找到匹配实体
    2. 对每个匹配实体，query_related 获取一跳邻居
    3. 将实体名和邻居名映射为 wiki slug（复用 wiki_compiler.make_slug）
    4. 只保留 wiki 中实际存在的 slug

    GraphStore 不可用时（Neo4j 未配置）返回空 dict，不影响召回流程。

    Returns:
        slug → WikiPageHit（score 为图谱派生分数：直接匹配 2.0，邻居 1.0）
    """
    try:
        from app.knowledge.graph_store import get_graph_store
        from app.knowledge.wiki_compiler import make_slug

        store = get_graph_store()
    except Exception:  # noqa: BLE001
        return {}

    wiki_slugs = {p["slug"] for p in pages if p["slug"] != "index"}
    slug_to_page = {p["slug"]: p for p in pages if p["slug"] != "index"}

    hits: dict[str, WikiPageHit] = {}

    # 限制 token 数量避免过多图谱查询（最多 10 个）
    for tok in tokens[:10]:
        try:
            entities = store.search_entities(tok, limit=5)
        except Exception:  # noqa: BLE001
            continue

        for ent in entities:
            ent_name = ent.get("name", "")
            ent_type = ent.get("type", "")
            if not ent_name:
                continue

            # 匹配实体本身 → 映射为 slug（直接匹配，分数较高）
            slug = make_slug(ent_type, ent_name)
            if slug in wiki_slugs and slug not in hits:
                p = slug_to_page[slug]
                hits[slug] = WikiPageHit(
                    slug=slug,
                    title=p.get("title") or slug,
                    type=p["type"],
                    score=2.0,
                    snippet="",
                )

            # 一跳邻居 → 映射为 slug（邻居降权）
            try:
                relations = store.query_related(ent_name, depth=1)
            except Exception:  # noqa: BLE001
                continue

            for rel in relations[:10]:  # 每个实体最多取 10 个邻居
                target_name = rel.get("target", "")
                target_type = rel.get("target_type", "")
                if not target_name:
                    continue
                neighbor_slug = make_slug(target_type, target_name)
                if neighbor_slug in wiki_slugs and neighbor_slug not in hits:
                    p = slug_to_page[neighbor_slug]
                    hits[neighbor_slug] = WikiPageHit(
                        slug=neighbor_slug,
                        title=p.get("title") or neighbor_slug,
                        type=p["type"],
                        score=1.0,
                        snippet="",
                    )

    return hits


async def recall_pages(
    question: str,
    limit: int = 5,
    min_score: float = 5.0,
    *,
    use_vector: bool = True,
    rrf_k: int = 60,
) -> list[WikiPageHit]:
    """从 wiki 召回与问题相关的页面

    三路召回 + RRF 融合（P2-1.1 + P3-1）：
    - 关键词路径：title +5 / tags +3 / body +1
    - 向量路径：余弦相似度（依赖 LLM embedding，未配置时自动降级）
    - 图谱路径（P3-1）：GraphStore 邻居扩展（Neo4j 未配置时自动降级）
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

    # ── 图谱召回路径（P3-1） ──
    graph_hits: dict[str, WikiPageHit] = {}
    graph_used = False
    try:
        graph_hits = _graph_recall(tokens, pages)
        graph_used = bool(graph_hits)
    except Exception as e:  # noqa: BLE001
        logger.warning("wiki_graph_recall_failed", error=str(e))

    # ── 融合 ──
    # 收集所有可用路径（P3-1: 新增图谱路径作为第三路）
    paths: list[tuple[str, dict[str, WikiPageHit]]] = []
    if keyword_hits:
        paths.append(("kw", keyword_hits))
    if vector_used:
        paths.append(("vec", vector_hits))
    if graph_used:
        paths.append(("graph", graph_hits))

    if not paths:
        return []

    # 单路 → 直接按分数排序返回
    if len(paths) == 1:
        merged = list(paths[0][1].values())
        merged.sort(key=lambda h: h.score, reverse=True)
        return merged[:limit]

    # 多路 → RRF 融合
    ranks: dict[str, dict[str, int]] = {}
    for path_name, hits in paths:
        ranked = sorted(hits.items(), key=lambda kv: kv[1].score, reverse=True)
        ranks[path_name] = {slug: i + 1 for i, (slug, _) in enumerate(ranked)}

    all_slugs: set[str] = set()
    for _, hits in paths:
        all_slugs.update(hits.keys())

    merged_hits: list[WikiPageHit] = []
    for slug in all_slugs:
        rrf_score = 0.0
        meta_hit: WikiPageHit | None = None
        for path_name, hits in paths:
            rank_map = ranks[path_name]
            if slug in rank_map:
                rrf_score += 1.0 / (rrf_k + rank_map[slug])
                if meta_hit is None:
                    meta_hit = hits[slug]
        if meta_hit is None:
            continue
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


# P2-13b：多轮会话历史消息数上限（控制 token，保留最近上下文）
MAX_HISTORY_MESSAGES = 10


def _sanitize_history(history: list[dict] | None) -> list[dict]:
    """清洗多轮会话历史：只保留 user/assistant 的 role+content，截断到最近 N 条

    对齐 AGENTS.md §六 Query Workflow：多轮会话时把前几轮问答作为上下文传给 LLM，
    让其理解指代与追问。后端无状态（不存 session），历史由前端维护。
    """
    if not history:
        return []
    cleaned: list[dict] = []
    for h in history:
        if not isinstance(h, dict):
            continue
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            cleaned.append({"role": role, "content": content})
    # 保留最近 N 条（避免 token 爆炸）
    if len(cleaned) > MAX_HISTORY_MESSAGES:
        cleaned = cleaned[-MAX_HISTORY_MESSAGES:]
    return cleaned


def _build_llm_messages(
    system: str,
    prompt: str,
    history: list[dict] | None,
) -> list[ChatMessage]:
    """构建 LLM messages：system + 多轮历史 + 当前问题

    多轮历史插入在 system 与当前 user prompt 之间，让 LLM 理解追问/指代。
    """
    messages: list[ChatMessage] = [ChatMessage(role="system", content=system)]
    if history:
        for h in history:
            messages.append(
                ChatMessage(role=h["role"], content=h["content"])
            )
    messages.append(ChatMessage(role="user", content=prompt))
    return messages


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
        writeback: bool = True,
        permissive: bool = True,
        history: list[dict] | None = None,
    ) -> WikiQueryResult:
        """回答用户问题

        流程（AGENTS.md §六）：
            1. 召回相关 wiki 页面
            2. 若召回为空 → permissive 降级（raw 文档检索 / OKF 导入提示）
            3. 加载页面正文作为上下文
            4. LLM 基于 wiki 上下文回答，引用 [[slug]]
            5. S12-1 知识复利：从回答中提取新事实回写 wiki（writeback=True 时）

        Args:
            permissive: P2-2 容错消费模式。True 时召回为空会尝试降级到 raw 文档
                       检索，而非直接拒绝。对齐 OKF permissive consumption 哲学。
            history: P2-13b 多轮会话历史（[{role, content}]），注入 LLM messages
                     让其理解追问/指代。后端无状态，历史由前端维护。
        """
        clean_history = _sanitize_history(history)
        # P1-4: 召回 + 上下文准备（与 stream_answer 共用）
        early, recalled, contexts, cited = await self._prepare_context(
            question,
            recall_limit=recall_limit,
            expand_backlinks=expand_backlinks,
            permissive=permissive,
        )
        if early is not None:
            return early

        # 4. LLM 回答
        answer = await self._llm_answer(question, contexts, history=clean_history)
        if not answer:
            answer = "已召回以下 wiki 页面，但 LLM 暂时无法生成回答：\n" + "\n".join(
                f"- [[{h.slug}]] {h.title}" for h in recalled
            )

        result = WikiQueryResult(
            question=question,
            answer=answer,
            cited_slugs=cited,
            recalled_pages=recalled,
        )

        # 5. S12-1 知识复利：回写新事实
        if writeback and answer and cited:
            try:
                writebacks = await self.writeback_new_facts(
                    question=question,
                    answer=answer,
                    cited_slugs=cited,
                    contexts=contexts,
                )
                result.writebacks = writebacks
            except Exception as e:
                logger.warning("wiki_writeback_failed", error=str(e))

        return result

    # ────────── P1-4: 流式问答 ──────────

    async def _prepare_context(
        self,
        question: str,
        *,
        recall_limit: int = 5,
        expand_backlinks: bool = True,
        permissive: bool = True,
    ) -> tuple[WikiQueryResult | None, list[WikiPageHit], list[str], list[str]]:
        """召回 + backlink 扩展 + 加载页面正文（answer 与 stream_answer 共用）

        Returns:
            (early_result, recalled, contexts, cited)
            - early_result 非 None 时表示知识库不足，调用方应直接返回
            - 否则使用 recalled/contexts/cited 进行 LLM 生成
        """
        # 1. 召回
        recalled = await recall_pages(question, limit=recall_limit)
        if not recalled:
            if permissive:
                degraded = await self._try_degraded_recall(question, recall_limit)
                if degraded:
                    recalled = degraded
            if not recalled:
                return (
                    WikiQueryResult(
                        question=question,
                        answer=(
                            "知识库中暂无相关 wiki 页面。建议先上传相关运维文档，"
                            "由 wiki_compiler 编译后再提问；或通过 OKF bundle 导入"
                            "（POST /api/okf/import）外部知识。"
                        ),
                        insufficient_knowledge=True,
                    ),
                    [],
                    [],
                    [],
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
                            score=hit.score * 0.3,
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
            if hit.type == "raw-fallback":
                if hit.snippet:
                    contexts.append(
                        f"## [raw] {hit.title}\n\n{hit.snippet}\n\n"
                        f"> 注：此内容来自 raw 文档降级召回，建议编译为 wiki 页面。"
                    )
                    cited.append(hit.slug)
                continue
            latest = self.vc.get_latest(_key_from_slug(hit.slug))
            if not latest:
                continue
            body = _strip_frontmatter(latest["content"])
            contexts.append(f"## [[{hit.slug}]] — {hit.title}\n\n{body}")
            cited.append(hit.slug)

        if not contexts:
            return (
                WikiQueryResult(
                    question=question,
                    answer="知识库中相关 wiki 页面无法加载。",
                    recalled_pages=recalled,
                    insufficient_knowledge=True,
                ),
                recalled,
                [],
                [],
            )

        return None, recalled, contexts, cited

    async def stream_answer(
        self,
        question: str,
        *,
        recall_limit: int = 5,
        expand_backlinks: bool = True,
        writeback: bool = True,
        permissive: bool = True,
        history: list[dict] | None = None,
        cancel_token: Any = None,
    ):
        """流式问答：先返回 meta（recalled/cited），再流式 yield 回答片段

        yield 顺序：
            {"type": "meta", "recalled_pages": [...], "cited_slugs": [...],
             "insufficient_knowledge": bool}  # 知识不足时附 answer
            {"type": "delta", "text": "..."}  # 多次
            {"type": "done", "writebacks": [...]}  # 结束

        与 answer() 共用 _prepare_context，保证召回逻辑一致。

        Args:
            history: P2-13b 多轮会话历史，注入 LLM messages。
        """
        clean_history = _sanitize_history(history)
        early, recalled, contexts, cited = await self._prepare_context(
            question,
            recall_limit=recall_limit,
            expand_backlinks=expand_backlinks,
            permissive=permissive,
        )
        if early is not None:
            yield {
                "type": "meta",
                "recalled_pages": [],
                "cited_slugs": [],
                "insufficient_knowledge": True,
                "answer": early.answer,
            }
            yield {"type": "done", "writebacks": []}
            return

        # 发送 meta（让前端立即展示召回页面）
        yield {
            "type": "meta",
            "recalled_pages": [
                {"slug": h.slug, "title": h.title, "type": h.type, "score": h.score}
                for h in recalled
            ],
            "cited_slugs": cited,
            "insufficient_knowledge": False,
        }

        # 流式生成回答：迭代 LLM stream，逐 chunk yield delta 并收集全文
        collected: list[str] = []
        async for chunk in self._stream_llm(
            question, contexts, history=clean_history, cancel_token=cancel_token
        ):
            collected.append(chunk)
            yield {"type": "delta", "text": chunk}

        full_answer = "".join(collected)
        if not full_answer:
            full_answer = "已召回以下 wiki 页面，但 LLM 暂时无法生成回答：\n" + "\n".join(
                f"- [[{h.slug}]] {h.title}" for h in recalled
            )
            yield {"type": "delta", "text": full_answer}

        # 知识复利回写（需要完整回答）
        writebacks: list[dict] = []
        if writeback and full_answer and cited:
            try:
                writebacks = await self.writeback_new_facts(
                    question=question,
                    answer=full_answer,
                    cited_slugs=cited,
                    contexts=contexts,
                )
            except Exception as e:
                logger.warning("wiki_stream_writeback_failed", error=str(e))

        yield {"type": "done", "writebacks": writebacks}

    async def _stream_llm(
        self,
        question: str,
        contexts: list[str],
        *,
        history: list[dict] | None = None,
        cancel_token: Any = None,
    ):
        """底层：调用 LLM stream，逐 chunk yield delta 文本

        与 _llm_answer 使用相同的 system/prompt，仅切换为流式接口。
        LLM 不可用或出错时静默结束（调用方按空 collected 处理回退）。

        Args:
            history: P2-13b 多轮会话历史，注入 system 与当前问题之间。
        """
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
        messages = _build_llm_messages(system, prompt, history)
        try:
            async for chunk in self.llm.stream(
                messages=messages,
                temperature=0.2,
                max_tokens=self.settings.llm_max_tokens,
                cancel_token=cancel_token,
            ):
                if chunk:
                    yield chunk
        except Exception as e:
            logger.warning("wiki_qa_stream_failed", error=str(e))

    # ────────── P2-2: permissive 降级召回 ──────────

    async def _try_degraded_recall(
        self, question: str, limit: int
    ) -> list[WikiPageHit]:
        """P2-2 容错消费：wiki 召回为空时，降级到 raw 文档检索兜底

        对齐 OKF permissive consumption 哲学：消费者不应因主路径无结果
        就直接拒绝，而应尝试降级路径。

        降级策略：
        1. 尝试用 SearchEngine 对 raw 文档做向量/关键词检索
        2. 把命中的 raw 文档包装为 WikiPageHit（type="raw-fallback"，
           score 较低），让 LLM 仍能基于 raw 内容回答
        3. 在回答中标注"来自 raw 文档，建议编译为 wiki"

        Returns:
            WikiPageHit 列表（可能为空）
        """
        try:
            from app.search import get_search_engine

            engine = get_search_engine()
            # search 是同步方法
            hits = engine.search(question, limit=limit)
            if not hits:
                return []

            degraded: list[WikiPageHit] = []
            for h in hits[:limit]:
                # 用 raw 文档构造一个降级 hit
                # score 降权（×0.5），表明这是兜底结果
                score = float(
                    h.get("combined_score", h.get("score", 1.0))
                )
                degraded.append(
                    WikiPageHit(
                        slug=h.get("doc_id", ""),
                        title=h.get("title", h.get("doc_id", "raw-doc")),
                        type="raw-fallback",  # 标记降级来源
                        score=score * 0.5,
                        snippet=h.get("snippet", ""),
                    )
                )
            logger.info(
                "wiki_query_degraded_recall",
                question=question,
                hits=len(degraded),
            )
            return degraded
        except Exception as e:
            logger.debug("wiki_query_degraded_failed", error=str(e))
            return []

    # ────────── S12-1 知识复利：新事实回写 ──────────

    async def writeback_new_facts(
        self,
        question: str,
        answer: str,
        cited_slugs: list[str],
        contexts: list[str],
    ) -> list[dict]:
        """从 LLM 回答中提取新事实，回写到对应 wiki 页面（知识复利）

        流程（AGENTS.md §六 "知识复利"）：
            1. LLM 对比 answer 与 contexts，提取"回答中包含但 wiki 中没有的新事实"
            2. 每个新事实归属到一个 cited_slug
            3. 将新事实追加到对应 wiki 页面的「## 知识复利补充」章节
            4. 更新页面 frontmatter review_status: review_needed
            5. 返回回写记录列表

        容错：LLM 不可用 / 无新事实 / 写入失败 → 静默跳过，不抛异常

        Returns:
            [{slug, fact, version, status, reason}]
        """
        # 1. LLM 提取新事实
        new_facts = await self._extract_new_facts(question, answer, contexts)
        if not new_facts:
            logger.info("wiki_writeback_no_new_facts", question=question[:50])
            return []

        # P3-2: 校验新事实，过滤幻觉
        validated = await self._validate_facts(new_facts, answer, cited_slugs)
        if not validated:
            logger.info("wiki_writeback_all_facts_rejected", question=question[:50])
            return []

        # 2. 逐条回写到归属页面
        writebacks: list[dict] = []
        for fact in validated:
            slug = fact["slug"]
            text = fact["fact"]
            try:
                record = self._append_fact_to_page(slug, text, question)
                if record:
                    record["validation"] = fact.get("validation", "")
                    writebacks.append(record)
            except Exception as e:
                logger.warning(
                    "wiki_writeback_page_failed",
                    slug=slug,
                    error=str(e),
                )
                writebacks.append(
                    {"slug": slug, "fact": text, "status": "failed", "reason": str(e)}
                )

        if writebacks:
            logger.info(
                "wiki_writeback_done",
                question=question[:50],
                count=len(writebacks),
            )
        return writebacks

    async def _extract_new_facts(
        self,
        question: str,
        answer: str,
        contexts: list[str],
    ) -> list[dict]:
        """让 LLM 对比 answer 与 wiki 上下文，提取新事实

        Returns:
            [{slug, fact}] — slug 为新事实应归属的 wiki 页面 slug
        """
        system = (
            "你是 OpsKG Wiki 管理员。对比用户的回答与已有 wiki 上下文，"
            "提取回答中包含但 wiki 上下文中尚未记录的「新事实」。"
            "只提取有价值的、可复用的事实性信息（如参数值、处置步骤、配置项）。"
            "忽略主观判断、复述 wiki 的内容、以及无法验证的断言。"
            "每个新事实需指明应归属的 wiki slug（来自上下文中出现的 [[slug]]）。"
            "若无新事实，返回空数组。"
        )
        prompt = (
            f"# 用户问题\n{question}\n\n"
            f"# LLM 回答\n{answer}\n\n"
            f"# 已有 wiki 上下文\n"
            + "\n\n".join(contexts)
            + "\n\n# 输出要求\n"
            "返回 JSON 数组，每个元素：{\"slug\": \"页面slug\", \"fact\": \"新事实（1-2 句）\"}\n"
            "只返回 JSON，不要其他文字。若无新事实返回 []"
        )
        try:
            messages = [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=prompt),
            ]
            resp = await self.llm.chat(
                messages=messages,
                temperature=0.1,
                max_tokens=1024,
            )
            raw = (resp.text or "").strip()
            # 容忍 LLM 返回非纯 JSON（包了 markdown code fence）
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            import json

            facts = json.loads(raw)
            if not isinstance(facts, list):
                return []
            # 校验结构
            valid = []
            for f in facts:
                if isinstance(f, dict) and f.get("slug") and f.get("fact"):
                    valid.append({"slug": str(f["slug"]), "fact": str(f["fact"])})
            return valid
        except Exception as e:
            logger.warning("wiki_extract_facts_failed", error=str(e))
            return []

    # ────────── P3-2: 知识复利回写校验 ──────────

    async def _validate_facts(
        self,
        facts: list[dict],
        answer: str,
        cited_slugs: list[str],
    ) -> list[dict]:
        """P3-2: 校验新事实，过滤幻觉，避免污染知识库

        两层校验：
        1. 规则校验（必跑，无 LLM 开销）：
           - 非空且非琐碎（len >= 10）
           - slug 在 cited_slugs 中
           - 事实关键 token 须在 answer 中有支撑（重叠率 >= 30%）
           - 目标 wiki 页面中无近似重复（token Jaccard >= 0.8 视为重复）
        2. LLM 自校验（可选，settings.wiki_writeback_llm_validate=True 时）：
           - 让 LLM 判断事实是否由 answer 直接支持
           - 批量校验，减少 LLM 调用次数

        Returns:
            通过校验的事实列表 [{slug, fact, validation}]
        """
        # ── 规则校验 ──
        rule_passed: list[dict] = []
        answer_tokens = set(_tokenize(answer))

        for fact in facts:
            slug = fact.get("slug", "")
            text = fact.get("fact", "").strip()
            if not slug or slug not in cited_slugs:
                continue
            if len(text) < 10:
                continue

            # 事实 token 与 answer token 重叠率（避免 LLM 编造 answer 中没有的内容）
            fact_tokens = set(_tokenize(text))
            if not fact_tokens:
                continue
            overlap = len(fact_tokens & answer_tokens) / len(fact_tokens)
            if overlap < 0.3:
                continue

            # 去重：检查目标页面是否已有近似事实
            if self._is_duplicate_fact(slug, text):
                continue

            rule_passed.append({"slug": slug, "fact": text, "validation": "rule_passed"})

        if not rule_passed:
            return []

        # ── LLM 自校验（可选） ──
        # settings 可能为 None（verify 脚本绕过 __init__）→ 跳过 LLM 校验
        if not self.settings or not self.settings.wiki_writeback_llm_validate:
            return rule_passed

        llm_passed = await self._llm_verify_facts(rule_passed, answer)
        if not llm_passed:
            logger.info(
                "wiki_writeback_llm_rejected_all",
                rule_passed=len(rule_passed),
            )
        return llm_passed if llm_passed else []

    def _is_duplicate_fact(self, slug: str, fact: str, threshold: float = 0.8) -> bool:
        """检查目标 wiki 页面是否已包含近似事实（基于 token Jaccard 相似度）

        Args:
            slug: wiki 页面 slug
            fact: 待检查的事实文本
            threshold: Jaccard 相似度阈值，>= threshold 视为重复
        """
        try:
            latest = self.vc.get_latest(_key_from_slug(slug))
            if not latest:
                return False
            existing = _strip_frontmatter(latest["content"]).lower()
            fact_tokens = set(_tokenize(fact))
            if not fact_tokens:
                return False
            # 滑动窗口检查：对 existing 的每个段落计算与 fact 的 Jaccard
            for para in existing.split("\n\n"):
                para_tokens = set(_tokenize(para))
                if not para_tokens:
                    continue
                jaccard = len(fact_tokens & para_tokens) / len(
                    fact_tokens | para_tokens
                )
                if jaccard >= threshold:
                    return True
            return False
        except Exception:  # noqa: BLE001
            return False

    async def _llm_verify_facts(
        self,
        facts: list[dict],
        answer: str,
    ) -> list[dict]:
        """P3-2: LLM 批量校验事实是否由 answer 直接支持

        Returns:
            通过校验的事实列表（LLM 判定 supported=True 的）
        """
        system = (
            "你是 OpsKG Wiki 审查员。判断每条「事实」是否由「回答」直接支持"
            "（即回答中有明确文字依据，而非推断或编造）。"
            "只返回 JSON 数组，每个元素包含 index 和 supported 字段。"
        )
        facts_desc = "\n".join(
            f"{i}. {f['fact']}" for i, f in enumerate(facts)
        )
        prompt = (
            f"# 回答\n{answer}\n\n"
            f"# 待校验事实\n{facts_desc}\n\n"
            f"# 输出要求\n"
            f'返回 JSON 数组：[{{"index": 0, "supported": true/false}}]\n'
            "只返回 JSON，不要其他文字。"
        )
        try:
            messages = [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=prompt),
            ]
            resp = await self.llm.chat(
                messages=messages,
                temperature=0.0,
                max_tokens=512,
            )
            raw = (resp.text or "").strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            import json

            results = json.loads(raw)
            if not isinstance(results, list):
                return facts  # 解析失败 → 放行（不因 LLM 异常阻断回写）

            supported_indices = {
                r["index"]
                for r in results
                if isinstance(r, dict) and r.get("supported") is True and "index" in r
            }
            passed = [
                {**f, "validation": "rule+llm_passed"}
                for i, f in enumerate(facts)
                if i in supported_indices
            ]
            return passed
        except Exception as e:
            logger.warning("wiki_fact_llm_verify_failed", error=str(e))
            # LLM 校验失败 → 降级为仅规则校验通过的结果（不阻断回写）
            return [{**f, "validation": "rule_passed(llm_failed)"} for f in facts]

    def _append_fact_to_page(
        self,
        slug: str,
        fact: str,
        question: str,
    ) -> dict | None:
        """将新事实追加到 wiki 页面的「知识复利补充」章节

        - 在页面正文末尾追加 `## 知识复利补充` 章节（若不存在）
        - 每条事实格式：`- [问题] 事实内容（待审查）`
        - 更新 frontmatter review_status: review_needed
        - 通过 VersionControl 保存新版本

        Returns:
            {slug, fact, version, status} 或 None（页面不存在）
        """
        from datetime import datetime, timezone

        from app.knowledge.wiki_index import _parse_frontmatter

        doc_key = _key_from_slug(slug)
        latest = self.vc.get_latest(doc_key)
        if not latest:
            return None

        content = latest["content"]
        meta, body = _parse_frontmatter(content)

        # 追加知识复利补充章节
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fact_line = f"- [{now} 来自提问「{question[:40]}」] {fact}（待审查）"

        section_header = "## 知识复利补充"
        if section_header in body:
            # 章节已存在，追加到章节末尾（下一个 ## 之前）
            idx = body.index(section_header)
            section_start = idx + len(section_header)
            # 找下一个二级标题
            next_section = body.find("\n## ", section_start)
            insert_pos = next_section if next_section > 0 else len(body)
            body = body[:insert_pos].rstrip() + "\n" + fact_line + "\n" + body[insert_pos:]
        else:
            # 章节不存在，追加到正文末尾
            body = body.rstrip() + f"\n\n{section_header}\n\n{fact_line}\n"

        # 更新 frontmatter
        meta["review_status"] = "review_needed"
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 重建完整内容
        new_content = _rebuild_frontmatter(meta, body)

        # 保存新版本
        title = meta.get("title") or slug
        result = self.vc.save_version(
            doc_key=doc_key,
            title=title,
            content=new_content,
            author="wiki-qa-writeback",
            change_summary=f"知识复利：回写新事实（来自提问「{question[:30]}」）",
        )

        version = result.get("version", latest.get("version", 0))
        skipped = result.get("skipped", False)
        return {
            "slug": slug,
            "fact": fact,
            "version": version,
            "status": "skipped" if skipped else "written",
            "review_status": "review_needed",
        }

    async def _llm_answer(
        self,
        question: str,
        contexts: list[str],
        *,
        history: list[dict] | None = None,
    ) -> str:
        """让 LLM 基于 wiki 上下文回答

        Args:
            history: P2-13b 多轮会话历史，注入 system 与当前问题之间。
        """
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
            messages = _build_llm_messages(system, prompt, history)
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


def _rebuild_frontmatter(meta: dict, body: str) -> str:
    """根据 meta dict 与 body 重建带 frontmatter 的完整内容

    用于 S12-1 知识复利回写时重建页面。
    """
    import yaml

    # 确保 body 末尾有换行
    body = body.rstrip() + "\n"
    if not meta:
        return body
    # yaml.safe_dump 保留顺序（sort_keys=False），allow_unicode 中文不转义
    front = yaml.safe_dump(
        meta, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).strip()
    return f"---\n{front}\n---\n\n{body}"


# ────────── 单例 ──────────

_qa: WikiQAEngine | None = None


def get_wiki_qa_engine() -> WikiQAEngine:
    global _qa
    if _qa is None:
        _qa = WikiQAEngine()
    return _qa
