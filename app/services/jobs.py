from collections.abc import Awaitable, Callable
from uuid import UUID

from app.domain.exceptions import JobNotFoundError
from app.domain.models import InspectionResult, Job
from app.repositories.jobs import JobRepository


class JobService:
    def __init__(
        self,
        repository: JobRepository,
        enqueue_job: Callable[[UUID], Awaitable[None]],
    ) -> None:
        self._repository = repository
        self._enqueue_job = enqueue_job # enqueue_job只是一个参数名

    async def create_job(self, name: str, urls: tuple[str, ...]) -> Job:
        job = await self._repository.create(name=name, urls=urls)
        await self._enqueue_job(job.id) # 阶段2新增之处
        return job

    async def get_job(self, job_id: UUID) -> Job:
        job = await self._repository.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    async def get_results(
        self,
        job_id: UUID,
    ) -> tuple[Job, tuple[InspectionResult, ...]]:
        job = await self.get_job(job_id)
        results = await self._repository.list_results(job_id)
        return job, results
