"""结构化日志初始化（P0 修复）

必须在应用启动最早阶段调用 `setup_logging()`，确保：
1. structlog 配置正确的 processor 链（ConsoleRenderer / JSONRenderer）
2. Python stdlib logging 级别与 LOG_LEVEL 环境变量对齐
3. uvicorn 日志桥接到 structlog（统一格式）

修复前问题：
- structlog.configure() 从未被调用（仅 tracing 启用时才有）
- LOG_LEVEL 环境变量被读取但从未应用到 logging 系统
- structlog 默认 WARNING 级别，DEBUG/INFO 日志被静默丢弃
"""

from __future__ import annotations

import logging
import os
import sys
import threading

import structlog


def setup_logging() -> None:
    """初始化 structlog + stdlib logging 桥接。

    调用时机：FastAPI app 创建前（main.py 模块顶层或 lifespan 启动阶段）。
    幂等：重复调用安全（structlog 支持 hot-reload 配置）。
    """
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # 判断是否为生产环境（JSON 输出便于日志采集）
    env = os.getenv("ENV", "dev")
    use_json = env == "production"

    # ── 1. 配置 stdlib logging 根级别 ──
    # structlog 内部委托给 stdlib logging，所以必须设置根 logger 级别
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,  # 覆盖已有配置（如 uvicorn 预设）
    )

    # 控制第三方库日志噪音
    for _noisy in ("urllib3", "httpx", "httpcore", "openai", "neo4j", "asyncio"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    # ── 2. 构建 processor 链 ──
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.set_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if use_json:
        # 生产环境：JSON 输出（便于 logstash / fluentd / loki 采集）
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # 开发环境：彩色 Console 输出
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(
                colors=True,
                pad_event=30,
            ),
        ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    # 日志初始化完成（使用 print 而非 logger，避免循环依赖）
    if not use_json:
        print(f"[logging] structlog 已初始化 | level={log_level_name} | renderer={'JSON' if use_json else 'Console'}")

    # ── 3. 桥接 uvicorn 日志到 structlog ──
    _bridge_uvicorn_logging()


def _bridge_uvicorn_logging() -> None:
    """将 uvicorn 的 stdlib logger 桥接到 structlog。

    uvicorn 使用 `uvicorn.error` 和 `uvicorn.access` 两个 logger。
    通过添加 structlog 兼容的 handler，使 uvicorn 日志也使用统一格式。
    """
    # 配置 uvicorn.access 使用 structlog 格式
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers = []
    uvicorn_access.addHandler(_StructlogHandler())

    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.handlers = []
    uvicorn_error.addHandler(_StructlogHandler())


class _StructlogHandler(logging.Handler):
    """将 stdlib logging 记录桥接到 structlog 的自定义 Handler

    防递归说明：
    structlog 配置使用 stdlib.LoggerFactory，structlog 日志会委托给
    stdlib logging，而 stdlib 根 logger 的 handler 又是本 Handler，
    形成 structlog → stdlib → _StructlogHandler → structlog 的循环。
    使用 threading.local 标志打断递归。
    """

    _local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        # 防止递归：如果当前线程已经在桥接中，直接返回
        if getattr(self._local, "handling", False):
            return
        # 跳过 structlog 自身的日志
        if record.name.startswith("structlog"):
            return
        # 设置处理标志，防止 structlog → stdlib → 本 handler 的递归
        self._local.handling = True
        try:
            logger = structlog.get_logger(record.name)
            log_method = getattr(logger, record.levelname.lower(), logger.info)
            log_method(
                record.getMessage(),
                logger_name=record.name,
                filename=record.filename,
                lineno=record.lineno,
            )
        finally:
            self._local.handling = False
