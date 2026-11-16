from datetime import UTC, datetime
from uuid import UUID, uuid4

import aiosqlite

from app.domain.models import InspectionResult, Job, JobStatus
from app.repositories.database import Database


class JobRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(self, name: str, urls: tuple[str, ...]) -> Job:
        job_id = uuid4()
        now = datetime.now(UTC)

        async with self._database.transaction() as connection:
            # 写任务摘要
            await connection.execute(
                """
                INSERT INTO jobs (
                    id, name, status, total_count, completed_count,
                    failed_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    str(job_id),
                    name,
                    JobStatus.PENDING.value,
                    len(urls),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            # 批量写 url
            await connection.executemany(
                """
                INSERT INTO job_targets (job_id, position, url)
                VALUES (?, ?, ?)
                """,
                ((str(job_id), position, url) for position, url in enumerate(urls)),
            )

        # 返回 job 领域对象
        return Job(
            id=job_id,
            name=name,
            urls=urls,
            status=JobStatus.PENDING,
            total_count=len(urls),
            completed_count=0,
            failed_count=0,
            created_at=now,
            updated_at=now,
        )
    
    # 根据 ID 查询任务
    async def get(self, job_id: UUID) -> Job | None:
        # 获取查询会话
        async with self._database.session() as connection:
            cursor = await connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (str(job_id),),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return None

            # 查询目标 url（只有任务存在时才继续）
            target_cursor = await connection.execute(
                """
                SELECT url FROM job_targets
                WHERE job_id = ?
                ORDER BY position
                """,
                (str(job_id),),
            )
            # 读取全部 url
            target_rows = await target_cursor.fetchall()
            await target_cursor.close()

        # 转换为 job
        return self._row_to_job(row, tuple(item["url"] for item in target_rows))

    # 查询任务结果
    async def list_results(self, job_id: UUID) -> tuple[InspectionResult, ...]:
        async with self._database.session() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM inspection_results
                WHERE job_id = ?
                ORDER BY created_at
                """,
                (str(job_id),),
            )
            rows = await cursor.fetchall()
            await cursor.close()

        return tuple(self._row_to_result(row) for row in rows)

    # 数据库行转换成 job
    # @staticmethod 表示转换函数不依赖当前 Repository 实例
    @staticmethod
    def _row_to_job(row: aiosqlite.Row, urls: tuple[str, ...]) -> Job:
        return Job(
            id=UUID(row["id"]),
            name=row["name"],
            urls=urls,
            status=JobStatus(row["status"]),
            total_count=row["total_count"],
            completed_count=row["completed_count"],
            failed_count=row["failed_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_result(row: aiosqlite.Row) -> InspectionResult:
        return InspectionResult(
            id=UUID(row["id"]),
            job_id=UUID(row["job_id"]),
            url=row["url"],
            status_code=row["status_code"],
            elapsed_ms=row["elapsed_ms"],
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
