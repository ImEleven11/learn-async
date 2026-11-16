from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Job:
    id: UUID
    name: str
    urls: tuple[str, ...]
    status: JobStatus
    total_count: int
    completed_count: int
    failed_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InspectionResult:
    id: UUID
    job_id: UUID
    url: str
    status_code: int | None
    elapsed_ms: int | None
    error: str | None
    created_at: datetime
