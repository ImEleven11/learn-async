from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.domain.models import InspectionResult, Job, JobStatus

# 创建请求模型，用于创建任务
class CreateJobRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    urls: Annotated[list[HttpUrl], Field(min_length=1, max_length=50)]

    # 验证器，用于验证任务名称和 URL 列表
    @field_validator("name")
    @classmethod
    def strip_and_validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    # 验证器，用于验证 URL 列表
    @field_validator("urls")
    @classmethod
    def reject_duplicate_urls(cls, value: list[HttpUrl]) -> list[HttpUrl]:
        normalized = [str(url) for url in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("urls must not contain duplicates")
        return value


class JobResponse(BaseModel):
    id: UUID
    name: str
    urls: list[str]
    status: JobStatus
    total_count: int
    completed_count: int
    failed_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, job: Job) -> "JobResponse":
        return cls(
            id=job.id,
            name=job.name,
            urls=list(job.urls),
            status=job.status,
            total_count=job.total_count,
            completed_count=job.completed_count,
            failed_count=job.failed_count,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


# 创建响应模型，用于创建检查结果
class InspectionResultResponse(BaseModel):
    id: UUID
    url: str
    status_code: int | None
    elapsed_ms: int | None
    error: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, result: InspectionResult) -> "InspectionResultResponse":
        return cls(
            id=result.id,
            url=result.url,
            status_code=result.status_code,
            elapsed_ms=result.elapsed_ms,
            error=result.error,
            created_at=result.created_at,
        )


# 创建响应模型，用于创建任务结果
class JobResultsResponse(BaseModel):
    job: JobResponse
    results: list[InspectionResultResponse]
