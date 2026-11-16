from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_job_service
from app.api.schemas import (
    CreateJobRequest,
    InspectionResultResponse,
    JobResponse,
    JobResultsResponse,
)
from app.services.jobs import JobService

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

# 创建任务
@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: CreateJobRequest,
    service: Annotated[JobService, Depends(get_job_service)],
) -> JobResponse:
    job = await service.create_job(
        name=payload.name,
        urls=tuple(str(url) for url in payload.urls),
    )
    return JobResponse.from_domain(job)

# 查询任务
@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    service: Annotated[JobService, Depends(get_job_service)],
) -> JobResponse:
    job = await service.get_job(job_id)
    return JobResponse.from_domain(job)

# 查询任务结果
@router.get("/{job_id}/results", response_model=JobResultsResponse)
async def get_job_results(
    job_id: UUID,
    service: Annotated[JobService, Depends(get_job_service)],
) -> JobResultsResponse:
    job, results = await service.get_results(job_id)
    return JobResultsResponse(
        job=JobResponse.from_domain(job),
        results=[InspectionResultResponse.from_domain(result) for result in results],
    )
