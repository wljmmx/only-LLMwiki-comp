"""全局 LLM 并发控制器

基于信号量 + 优先级队列的并发控制，支持:
- 全局最大并发限制
- 各阶段独立并发限制
- 任务优先级调度
- 本地部署性能适配（Ollama 推荐 4-8 并发）
- 运行时状态查询

使用方式:
    controller = LLMConcurrencyController(max_global=4)
    result = await controller.submit(LLMTask(
        task_id="compile_sec_001",
        priority=TaskPriority.HIGH,
        stage="section_compile",
        coroutine=compile_section(section),
    ))
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Coroutine


class TaskPriority(Enum):
    """LLM 任务优先级"""
    CRITICAL = 0     # 实时用户请求
    HIGH = 1         # 章节编译
    MEDIUM = 2       # Wiki 生成 / 图谱抽取
    LOW = 3          # 经验蒸馏 / 批量任务


@dataclass
class LLMTask:
    """LLM 任务"""
    task_id: str
    priority: TaskPriority
    stage: str           # "section_compile" | "wiki_generate" | "entity_resolve" | "experience_distill"
    coroutine: Coroutine[Any, Any, Any]
    section_id: str | None = None
    doc_id: str | None = None
    timeout: float = 120.0
    metadata: dict = field(default_factory=dict)


@dataclass
class LLMTaskResult:
    """LLM 任务执行结果"""
    task_id: str
    success: bool
    result: Any = None
    error: str | None = None
    elapsed: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0


class LLMConcurrencyController:
    """全局 LLM 并发控制器

    设计要点:
    - 基于 asyncio.Semaphore 的并发限制
    - 优先级队列（高优先级任务先执行）
    - 每个阶段独立的最大并发数
    - 支持动态调整并发数
    - 本地部署感知（Ollama 通常支持 4-8 并发）
    """

    def __init__(
        self,
        max_global: int = 4,
        max_per_stage: dict[str, int] | None = None,
    ):
        """
        Args:
            max_global: 全局最大并发 LLM 调用数
            max_per_stage: 每个阶段的最大并发数，默认:
                {
                    "section_compile": 3,
                    "wiki_generate": 1,
                    "entity_resolve": 1,
                    "experience_distill": 1,
                    "index_generate": 1,
                    "document_generate": 1,
                }
        """
        self._global_semaphore = asyncio.Semaphore(max_global)
        self._max_global = max_global

        self._stage_semaphores: dict[str, asyncio.Semaphore] = {}
        self._stage_limits = max_per_stage or {
            'section_compile': 3,
            'wiki_generate': 1,
            'entity_resolve': 1,
            'experience_distill': 1,
            'index_generate': 1,
            'document_generate': 1,
        }
        for stage, limit in self._stage_limits.items():
            self._stage_semaphores[stage] = asyncio.Semaphore(limit)

        # 优先级队列
        self._queues: dict[TaskPriority, asyncio.Queue[LLMTask]] = {
            p: asyncio.Queue() for p in TaskPriority
        }

        # 运行时状态
        self._running: dict[str, LLMTask] = {}
        self._stats = {
            'queued': 0, 'running': 0, 'completed': 0, 'failed': 0,
            'total_elapsed': 0.0, 'max_elapsed': 0.0,
        }
        self._worker_running = False
        self._worker_task: asyncio.Task | None = None
        self._results: dict[str, LLMTaskResult] = {}

    async def acquire(self, stage: str = "default", priority: TaskPriority = TaskPriority.MEDIUM) -> '_AcquireContext':
        """获取 LLM 并发槽位（async context manager）

        同时获取全局 + 阶段级 semaphore，确保不超过配置限制。

        Usage:
            async with controller.acquire(stage="section_compile"):
                result = await llm.chat(...)

        Args:
            stage: 阶段名称（用于阶段级限流）
            priority: 优先级（影响资源分配顺序）

        Returns:
            _AcquireContext 上下文管理器
        """
        return _AcquireContext(self, stage)

    async def _acquire_slots(self, stage: str) -> None:
        """获取全局和阶段级槽位"""
        await self._global_semaphore.acquire()
        stage_sem = self._stage_semaphores.get(stage, self._global_semaphore)
        if stage_sem is not self._global_semaphore:
            await stage_sem.acquire()

    def _release_slots(self, stage: str) -> None:
        """释放全局和阶段级槽位"""
        stage_sem = self._stage_semaphores.get(stage, self._global_semaphore)
        if stage_sem is not self._global_semaphore:
            stage_sem.release()
        self._global_semaphore.release()

    async def start(self) -> None:
        """启动后台 worker"""
        if not self._worker_running:
            self._worker_running = True
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        """停止后台 worker"""
        self._worker_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def submit(self, task: LLMTask) -> LLMTaskResult:
        """提交 LLM 任务并等待结果

        Args:
            task: LLM 任务

        Returns:
            LLMTaskResult 执行结果
        """
        # 创建等待事件
        event = asyncio.Event()
        self._completion_events[task.task_id] = event

        # 加入优先级队列
        await self._queues[task.priority].put(task)
        self._stats['queued'] += 1

        # 确保 worker 在运行
        if not self._worker_running:
            await self.start()

        # 等待完成
        await event.wait()

        # 清理并返回结果
        self._completion_events.pop(task.task_id, None)
        return self._results.pop(task.task_id, LLMTaskResult(
            task_id=task.task_id,
            success=False,
            error='Task result not found',
        ))

    async def _worker_loop(self) -> None:
        """后台 worker 循环"""
        while self._worker_running:
            task = await self._get_next_task()
            if task is None:
                await asyncio.sleep(0.05)
                continue

            # 获取阶段信号量
            stage_sem = self._stage_semaphores.get(task.stage)
            if stage_sem is None:
                stage_sem = asyncio.Semaphore(1)  # 未知阶段默认 1 并发

            async with self._global_semaphore:
                async with stage_sem:
                    await self._execute_task(task)

    async def _get_next_task(self) -> LLMTask | None:
        """从优先级队列取下一个任务"""
        for priority in TaskPriority:
            queue = self._queues[priority]
            try:
                return queue.get_nowait()
            except asyncio.QueueEmpty:
                continue
        return None

    async def _execute_task(self, task: LLMTask) -> None:
        """执行单个 LLM 任务"""
        self._running[task.task_id] = task
        self._stats['running'] += 1
        started = time.monotonic()

        try:
            result = await asyncio.wait_for(
                task.coroutine,
                timeout=task.timeout,
            )
            elapsed = time.monotonic() - started
            self._stats['completed'] += 1
            self._stats['total_elapsed'] += elapsed
            self._stats['max_elapsed'] = max(self._stats['max_elapsed'], elapsed)

            self._results[task.task_id] = LLMTaskResult(
                task_id=task.task_id,
                success=True,
                result=result,
                elapsed=elapsed,
                started_at=started,
                finished_at=time.monotonic(),
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - started
            self._stats['failed'] += 1
            self._results[task.task_id] = LLMTaskResult(
                task_id=task.task_id,
                success=False,
                error=f'Timeout after {task.timeout}s',
                elapsed=elapsed,
                started_at=started,
                finished_at=time.monotonic(),
            )
        except Exception as e:
            elapsed = time.monotonic() - started
            self._stats['failed'] += 1
            self._results[task.task_id] = LLMTaskResult(
                task_id=task.task_id,
                success=False,
                error=str(e),
                elapsed=elapsed,
                started_at=started,
                finished_at=time.monotonic(),
            )

        self._running.pop(task.task_id, None)
        self._stats['running'] -= 1

        # 通知等待者
        event = self._completion_events.get(task.task_id)
        if event:
            event.set()

    async def submit_batch(
        self, tasks: list[LLMTask],
    ) -> list[LLMTaskResult]:
        """批量提交任务并等待全部完成

        Args:
            tasks: 任务列表

        Returns:
            结果列表（顺序与输入一致）
        """
        results = await asyncio.gather(
            *[self.submit(t) for t in tasks],
            return_exceptions=True,
        )
        return [
            r if not isinstance(r, Exception) else LLMTaskResult(
                task_id='unknown',
                success=False,
                error=str(r),
            )
            for r in results
        ]

    def get_stats(self) -> dict:
        """获取当前并发状态"""
        return {
            **self._stats,
            'global_limit': self._max_global,
            'global_available': self._max_global - len(self._running),
            'stage_limits': self._stage_limits,
            'running_tasks': [
                {
                    'task_id': t.task_id,
                    'stage': t.stage,
                    'priority': t.priority.name,
                    'section_id': t.section_id,
                }
                for t in self._running.values()
            ],
            'queue_depth': {
                p.name: q.qsize()
                for p, q in self._queues.items()
            },
        }

    def update_limits(self, max_global: int | None = None, **stage_limits: int) -> None:
        """动态调整并发限制

        Args:
            max_global: 新的全局并发限制（None 表示不变）
            **stage_limits: 阶段级别的并发限制
        """
        if max_global is not None and max_global != self._max_global:
            # 重建全局信号量（简单方式：差异调整）
            diff = max_global - self._max_global
            self._max_global = max_global
            if diff > 0:
                for _ in range(diff):
                    self._global_semaphore.release()
            elif diff < 0:
                for _ in range(-diff):
                    # 通过 acquire 减少可用许可
                    asyncio.create_task(self._global_semaphore.acquire())

        for stage, limit in stage_limits.items():
            self._stage_limits[stage] = limit
            if stage in self._stage_semaphores:
                self._stage_semaphores[stage] = asyncio.Semaphore(limit)


class _AcquireContext:
    """LLM 并发槽位获取的 async context manager"""

    def __init__(self, controller: LLMConcurrencyController, stage: str):
        self._controller = controller
        self._stage = stage

    async def __aenter__(self):
        await self._controller._acquire_slots(self._stage)
        return self

    async def __aexit__(self, *args):
        self._controller._release_slots(self._stage)


# 全局单例
_controller: LLMConcurrencyController | None = None


def get_llm_concurrency_controller(
    max_global: int | None = None,
    max_per_stage: dict[str, int] | None = None,
) -> LLMConcurrencyController:
    """获取全局 LLM 并发控制器单例"""
    global _controller
    if _controller is None:
        _controller = LLMConcurrencyController(
            max_global=max_global or 4,
            max_per_stage=max_per_stage,
        )
    return _controller
