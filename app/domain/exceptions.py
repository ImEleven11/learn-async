from uuid import UUID


class JobNotFoundError(Exception):
    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__(f"Job {job_id} was not found")
