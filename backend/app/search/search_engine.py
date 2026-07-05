"""全文 + 向量搜索（P1-1 + P2-1.1）

混合检索引擎：
- SQLite FTS5 全文索引（关键字检索，零依赖）
- 内存向量索引（embedding 相似度检索，使用 LLM 生成 embedding）
- 融合策略：
  - `rrf`（默认，P2-1.1）：Reciprocal Rank Fusion，1/(k+rank)
  - `weighted`（旧版兼容）：keyword_score * w_kw + vector_score * w_vec

支持对已解析文档的内容进行检索。
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import structlog

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "search_index.db"

# 尝试导入 numpy 加速余弦相似度
try:
    import numpy as np  # type: ignore[import-untyped]

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        -- FTS5 全文索引表
        CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
            doc_id UNINDEXED,
            title,
            content,
            format UNINDEXED,
            tokenize='unicode61'
        );

        -- 向量索引表（内存计算余弦相似度）
        CREATE TABLE IF NOT EXISTS doc_embeddings (
            doc_id TEXT PRIMARY KEY,
            title TEXT,
            embedding TEXT,          -- JSON 序列化的 float list
            dim INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_emb_doc ON doc_embeddings(doc_id);
    """)


FusionStrategy = Literal["rrf", "weighted"]


class SearchEngine:
    """混合检索引擎（关键字 + 向量，RRF 融合）"""

    # ────────── 索引管理 ──────────

    def index_document(
        self,
        doc_id: str,
        title: str,
        content: str,
        fmt: str,
        embedding: list[float] | None = None,
    ) -> None:
        """添加/更新文档索引"""
        conn = _get_db()
        # 先删除旧索引
        conn.execute("DELETE FROM docs_fts WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM doc_embeddings WHERE doc_id = ?", (doc_id,))

        # FTS5 全文索引
        conn.execute(
            "INSERT INTO docs_fts (doc_id, title, content, format) VALUES (?, ?, ?, ?)",
            (doc_id, title or "", content[:50000], fmt),
        )

        # 向量索引
        if embedding:
            conn.execute(
                """INSERT INTO doc_embeddings (doc_id, title, embedding, dim, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    doc_id,
                    title or "",
                    json.dumps(embedding),
                    len(embedding),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        conn.commit()
        logger.info(
            "search_indexed",
            doc_id=doc_id,
            has_embedding=bool(embedding),
            dim=len(embedding) if embedding else 0,
        )

    def remove_index(self, doc_id: str) -> None:
        """删除文档索引"""
        conn = _get_db()
        conn.execute("DELETE FROM docs_fts WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM doc_embeddings WHERE doc_id = ?", (doc_id,))
        conn.commit()

    # ────────── 检索 ──────────

    def search(
        self,
        query: str,
        limit: int = 20,
        query_embedding: list[float] | None = None,
        weight_keyword: float = 0.4,
        weight_vector: float = 0.6,
        *,
        fusion: FusionStrategy = "rrf",
        rrf_k: int = 60,
    ) -> list[dict]:
        """混合检索

        Args:
            query: 搜索关键词
            query_embedding: 查询向量（可选，提供时启用向量检索）
            weight_keyword: 关键词权重（仅 fusion="weighted" 时生效）
            weight_vector: 向量权重（仅 fusion="weighted" 时生效）
            fusion: 融合策略
                - "rrf"（默认）：Reciprocal Rank Fusion，对两路结果按 rank 倒数求和
                - "weighted"：加权线性（旧版兼容）
            rrf_k: RRF 平滑常数，默认 60（业界经验值）
        """
        # 拉取更多候选以保证 RRF 融合后的 top-K 质量
        candidate_limit = max(limit * 3, 30)
        keyword_results = self._keyword_search(query, candidate_limit)
        vector_results = (
            self._vector_search(query_embedding, candidate_limit) if query_embedding else {}
        )

        if fusion == "rrf" and query_embedding:
            merged = self._rrf_fusion(
                keyword_results, vector_results, k=rrf_k
            )
        else:
            merged = self._weighted_fusion(
                keyword_results,
                vector_results,
                weight_keyword=weight_keyword,
                weight_vector=weight_vector,
            )

        merged.sort(key=lambda x: x["combined_score"], reverse=True)
        return merged[:limit]

    def _rrf_fusion(
        self,
        keyword_results: dict[str, dict],
        vector_results: dict[str, dict],
        *,
        k: int = 60,
    ) -> list[dict]:
        """Reciprocal Rank Fusion

        score(d) = Σ_i  1 / (k + rank_i(d))

        其中 rank_i(d) 是文档 d 在第 i 路结果中的排名（从 1 开始）。
        未出现在某一路的文档，该路贡献为 0。
        """
        # 按分数降序得到排名（vector_score 越大越相关，keyword_score 已归一化到 [0,1] 也是越大越相关）
        kw_ranked = sorted(
            keyword_results.items(),
            key=lambda kv: kv[1]["score"],
            reverse=True,
        )
        vec_ranked = sorted(
            vector_results.items(),
            key=lambda kv: kv[1]["score"],
            reverse=True,
        )

        kw_rank_map: dict[str, int] = {doc_id: i + 1 for i, (doc_id, _) in enumerate(kw_ranked)}
        vec_rank_map: dict[str, int] = {doc_id: i + 1 for i, (doc_id, _) in enumerate(vec_ranked)}

        all_doc_ids = set(kw_rank_map.keys()) | set(vec_rank_map.keys())
        merged: list[dict] = []
        for doc_id in all_doc_ids:
            kw_score = keyword_results.get(doc_id, {}).get("score", 0.0)
            vec_score = vector_results.get(doc_id, {}).get("score", 0.0)
            kw_rank = kw_rank_map.get(doc_id)
            vec_rank = vec_rank_map.get(doc_id)
            rrf_score = 0.0
            if kw_rank is not None:
                rrf_score += 1.0 / (k + kw_rank)
            if vec_rank is not None:
                rrf_score += 1.0 / (k + vec_rank)

            meta = keyword_results.get(doc_id) or vector_results.get(doc_id) or {}
            merged.append(
                {
                    "doc_id": doc_id,
                    "title": meta.get("title", ""),
                    "format": meta.get("format", ""),
                    "snippet": meta.get("snippet", ""),
                    "keyword_score": round(kw_score, 4),
                    "vector_score": round(vec_score, 4),
                    "combined_score": round(rrf_score, 6),
                    "fusion": "rrf",
                }
            )
        return merged

    @staticmethod
    def _weighted_fusion(
        keyword_results: dict[str, dict],
        vector_results: dict[str, dict],
        *,
        weight_keyword: float,
        weight_vector: float,
    ) -> list[dict]:
        """加权线性融合（旧版兼容）"""
        all_doc_ids = set(keyword_results.keys()) | set(vector_results.keys())
        merged: list[dict] = []
        for doc_id in all_doc_ids:
            kw_score = keyword_results.get(doc_id, {}).get("score", 0.0)
            vec_score = vector_results.get(doc_id, {}).get("score", 0.0)
            combined = weight_keyword * kw_score + weight_vector * vec_score
            meta = keyword_results.get(doc_id) or vector_results.get(doc_id) or {}
            merged.append(
                {
                    "doc_id": doc_id,
                    "title": meta.get("title", ""),
                    "format": meta.get("format", ""),
                    "snippet": meta.get("snippet", ""),
                    "keyword_score": round(kw_score, 4),
                    "vector_score": round(vec_score, 4),
                    "combined_score": round(combined, 4),
                    "fusion": "weighted",
                }
            )
        return merged

    def _keyword_search(self, query: str, limit: int) -> dict[str, dict]:
        """FTS5 关键词检索"""
        if not query.strip():
            return {}
        conn = _get_db()
        words = [w for w in query.split() if w]
        if not words:
            return {}
        safe_query = " ".join(words)
        try:
            # 分两步：先查 doc_id 和 bm25，再单独取 snippet
            rows = conn.execute(
                """SELECT doc_id, title, format, bm25(docs_fts) as score
                   FROM docs_fts WHERE docs_fts MATCH ?
                   ORDER BY score LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}

        results = {}
        for r in rows:
            raw_score = r["score"]
            score = 1.0 / (1.0 + math.exp(raw_score)) if raw_score > -50 else 1.0
            # 从 content 截取片段
            content_row = conn.execute(
                "SELECT substr(content, 1, 200) as snippet FROM docs_fts WHERE doc_id = ?",
                (r["doc_id"],),
            ).fetchone()
            snippet = content_row["snippet"] if content_row else ""
            results[r["doc_id"]] = {
                "title": r["title"],
                "format": r["format"],
                "snippet": snippet,
                "score": score,
            }
        return results

    def _vector_search(
        self, query_embedding: list[float] | None, limit: int
    ) -> dict[str, dict]:
        """向量相似度检索（余弦相似度）

        优先使用 numpy 加速；不可用时降级到纯 Python。
        """
        if not query_embedding:
            return {}
        conn = _get_db()
        rows = conn.execute(
            "SELECT doc_id, title, embedding FROM doc_embeddings"
        ).fetchall()

        # 收集所有向量化结果，再取 top-limit
        scored: list[tuple[str, dict, float]] = []
        if _HAS_NUMPY:
            q_vec = np.asarray(query_embedding, dtype=np.float32)
            q_norm = float(np.linalg.norm(q_vec))
            if q_norm == 0:
                return {}
            q_unit = q_vec / q_norm
            for r in rows:
                try:
                    doc_emb = json.loads(r["embedding"])
                except (json.JSONDecodeError, TypeError):
                    continue
                d_vec = np.asarray(doc_emb, dtype=np.float32)
                if d_vec.shape != q_vec.shape:
                    continue
                d_norm = float(np.linalg.norm(d_vec))
                if d_norm == 0:
                    continue
                # cosine = dot(q_unit, d_unit)
                score = float(np.dot(q_unit, d_vec / d_norm))
                scored.append(
                    (
                        r["doc_id"],
                        {"title": r["title"], "format": "", "snippet": ""},
                        score,
                    )
                )
        else:
            for r in rows:
                try:
                    doc_emb = json.loads(r["embedding"])
                except (json.JSONDecodeError, TypeError):
                    continue
                score = self._cosine_similarity(query_embedding, doc_emb)
                scored.append(
                    (
                        r["doc_id"],
                        {"title": r["title"], "format": "", "snippet": ""},
                        score,
                    )
                )

        scored.sort(key=lambda x: x[2], reverse=True)
        return {
            doc_id: {**meta, "score": score}
            for doc_id, meta, score in scored[:limit]
        }

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """计算余弦相似度（纯 Python 兜底版）"""
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def get_stats(self) -> dict:
        """索引统计"""
        conn = _get_db()
        fts_count = conn.execute("SELECT COUNT(*) as cnt FROM docs_fts").fetchone()
        emb_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM doc_embeddings"
        ).fetchone()
        return {
            "indexed_docs": fts_count["cnt"] if fts_count else 0,
            "vectorized_docs": emb_count["cnt"] if emb_count else 0,
            "numpy_enabled": _HAS_NUMPY,
        }


# 全局单例
_engine: SearchEngine | None = None


def get_search_engine() -> SearchEngine:
    global _engine
    if _engine is None:
        _engine = SearchEngine()
    return _engine
