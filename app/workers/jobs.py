import asyncio
import logging
from typing import Protocol
from uuid import UUID

logger = logging.getLogger(__name__)

# 结构化类型
class JobClaimer(Protocol):
    async def claim_pending(self, job_id: UUID) -> bool: ...


class JobWorker:
    """Own one in-process queue and consume job IDs in the background."""

    def __init__(self, repository: JobClaimer) -> None:
        self._repository = repository
        self._queue: asyncio.Queue[UUID | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    # 启动后台 Task
    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Job worker is already started")
        self._task = asyncio.create_task(self._run(), name="job-worker")

    # 生产者入口
    async def enqueue(self, job_id: UUID) -> None:
        await self._queue.put(job_id)

    # 等待队列处理完成
    async def join(self) -> None:
        await self._queue.join()

    # 让 Worker 有序退出
    async def stop(self) -> None:
        task = self._task
        if task is None:
            return # 从未启动
        if task.done():
            self._task = None
            await task
            return

        await self._queue.put(None)
        try:
            await task
        finally:
            self._task = None

    # 长期消费循环
    async def _run(self) -> None:
        logger.info("job_worker_started")
        try:
            while True:
                job_id = await self._queue.get()
                try:
                    if job_id is None:
                        return # 取到 `None`：通过 `return` 退出
                    await self._claim_job(job_id) # 取到 UUID：调用 `_claim_job()`
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("job_worker_failed job_id=%s", job_id)
                finally:
                    self._queue.task_done()
        finally:
            logger.info("job_worker_stopped")

    # 处理单个队列项
    async def _claim_job(self, job_id: UUID) -> None:
        claimed = await self._repository.claim_pending(job_id) # 返回一个bool值
        if claimed:
            logger.info("job_claimed job_id=%s", job_id)
        else:
            logger.warning("job_not_claimed job_id=%s", job_id)
                