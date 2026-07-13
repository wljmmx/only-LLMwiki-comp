"""搜索分词器（P2-1.5）

支持两种分词模式：
- `jieba`：中文优先分词，使用 jieba.cut_for_search（搜索引擎模式，更细粒度）
- `whitespace`：纯空格切分（向后兼容，无 jieba 依赖）

设计原则：
1. 双端一致：index 写入与 query 切分必须用同一模式，否则 FTS5 MATCH 召回失效
2. 懒加载：jieba 仅在首次调用时导入（首次加载 ~600ms 字典构建）
3. 降级：jieba 不可用时自动回退到 whitespace，并记日志
4. 配置驱动：通过 Settings.search_tokenizer 切换模式

用法：
    from app.search.tokenizer import tokenize
    tokens = tokenize("Nginx 502 故障排查")  # → ['nginx', '502', '故障', '排查']
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

import structlog

logger = structlog.get_logger()

TokenizerMode = Literal["whitespace", "jieba"]

# 简单停用词表（中英混合，避免 FTS5 索引膨胀）
# 注：保留 "502"/"500" 等数字错误码（高区分度）
_STOPWORDS: frozenset[str] = frozenset(
    {
        # 中文常见停用词
        "的",
        "了",
        "在",
        "是",
        "我",
        "有",
        "和",
        "就",
        "不",
        "人",
        "都",
        "一",
        "一个",
        "上",
        "也",
        "很",
        "到",
        "说",
        "要",
        "去",
        "你",
        "会",
        "着",
        "没有",
        "看",
        "好",
        "自己",
        "这",
        "那",
        # 英文常见停用词
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
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "by",
        "from",
        "as",
        "into",
        "through",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "when",
        "up",
        "down",
        "out",
        # 标点与空白
        " ",
        "",
    }
)

# 匹配纯标点/符号 token（中英文标点 + 运算符）
_PUNCT_RE = re.compile(r"^[\s\W_]+$", re.UNICODE)


def _is_meaningful(token: str) -> bool:
    """判断 token 是否有检索意义（非停用词、非纯标点、非空）"""
    if not token:
        return False
    if token in _STOPWORDS:
        return False
    if _PUNCT_RE.match(token):
        return False
    return True


@lru_cache(maxsize=1)
def _get_jieba():
    """懒加载 jieba（首次调用构建字典，后续命中缓存）

    Returns:
        jieba 模块；不可用时返回 None
    """
    try:
        import jieba  # type: ignore[import-untyped]

        # 触发字典加载（首次约 600ms）
        # cut_for_search 内部会自动初始化，但显式调用确保缓存就绪
        list(jieba.cut_for_search("预热"))
        logger.info("jieba_loaded", version=getattr(jieba, "__version__", "unknown"))
        return jieba
    except ImportError:
        logger.warning("jieba_not_available", msg="jieba 未安装，回退到 whitespace 分词")
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("jieba_init_failed", error=str(e), msg="回退到 whitespace 分词")
        return None


def _tokenize_jieba(text: str) -> list[str]:
    """jieba 搜索引擎模式分词

    cut_for_search 比 cut 更细粒度（适合搜索引擎召回）：
    - "Nginx 故障排查" → ["Nginx", " ", "故障", "排查"]
    - "反向代理服务器" → ["反向", "代理", "服务器", "反向代理"]

    输出：小写化 + 过滤停用词/标点/空白
    """
    jieba = _get_jieba()
    if jieba is None:
        return _tokenize_whitespace(text)

    tokens = jieba.cut_for_search(text)
    return [t.lower() for t in tokens if _is_meaningful(t)]


def _tokenize_whitespace(text: str) -> list[str]:
    """纯空格切分（向后兼容，无 jieba 依赖）

    输出：小写化 + 过滤停用词
    """
    return [t.lower() for t in text.split() if _is_meaningful(t)]


def tokenize(text: str, mode: TokenizerMode = "jieba") -> list[str]:
    """文本分词（统一入口）

    Args:
        text: 待分词文本
        mode: 分词模式
            - "jieba"：jieba.cut_for_search（默认，中文优先）
            - "whitespace"：纯空格切分（向后兼容）

    Returns:
        小写化 token 列表（已过滤停用词、标点、空白）

    Examples:
        >>> tokenize("Nginx 502 故障排查")
        ['nginx', '502', '故障', '排查']
        >>> tokenize("Nginx 502 故障排查", mode="whitespace")
        ['nginx', '502', '故障排查']  # 中文整段未切
        >>> tokenize("")
        []
    """
    if not text:
        return []
    if mode == "jieba":
        return _tokenize_jieba(text)
    return _tokenize_whitespace(text)


def tokenize_to_string(text: str, mode: TokenizerMode = "jieba") -> str:
    """分词后用空格拼接成字符串（供 FTS5 索引写入与 MATCH 查询使用）

    FTS5 unicode61 tokenizer 看到空格分隔的 token 后会按空格切分，
    因此双端预分词后用空格拼接，FTS5 即可正确匹配。

    Returns:
        空格分隔的 token 字符串

    Examples:
        >>> tokenize_to_string("Nginx 502 故障排查")
        'nginx 502 故障 排查'
    """
    return " ".join(tokenize(text, mode=mode))
