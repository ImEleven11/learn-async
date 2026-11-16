import asyncio
from uuid import UUID, uuid4

from app.workers.jobs import JobWorker


class FakeJobRepository:
    def __init__(self) -> None:
        self.claimed_job_ids: list[UUID] = []

    async def claim_pending(self, job_id: UUID) -> bool:
        self.claimed_job_ids.append(job_id)
        return True


async def test_worker_consumes_queue_and_marks_items_done() -> None:
    repository = FakeJobRepository()
    worker = JobWorker(repository)
    job_ids = [uuid4(), uuid4(), uuid4()]
    worker.start()

    try:
        for job_id in job_ids:
            await worker.enqueue(job_id)

        async with asyncio.timeout(1):
            await worker.join()
    finally:
        await worker.stop()

    assert repository.claimed_job_ids == job_ids
