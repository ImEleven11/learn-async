import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

# 定义数据库结构
# jobs：保存任务主体
# job_targets：保存每个任务需要巡检的 URL
# inspection_results：保存未来 Worker 对每个 URL 的巡检结果，如HTTP状态码、请求耗时等
# 以及 索引
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    total_count INTEGER NOT NULL CHECK (total_count >= 0),
    completed_count INTEGER NOT NULL DEFAULT 0 CHECK (completed_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    url TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    UNIQUE (job_id, position),
    UNIQUE (job_id, url)
);

CREATE TABLE IF NOT EXISTS inspection_results (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    url TEXT NOT NULL,
    status_code INTEGER,
    elapsed_ms INTEGER CHECK (elapsed_ms IS NULL OR elapsed_ms >= 0),
    error TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    UNIQUE (job_id, url)
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_results_job_id ON inspection_results(job_id);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: aiosqlite.Connection | None = None 
        self._lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not connected")
        return self._connection

    # 建立数据库连接
    async def connect(self) -> None:
        # 避免重复连接：若已经连接，就不再创建第二个连接
        if self._connection is not None:
            return

        # 创建数据库目录
        if self.path != Path(":memory:"):
            self.path.parent.mkdir(parents=True, exist_ok=True)

        # 真正连接
        self._connection = await aiosqlite.connect(
            self.path,
            isolation_level=None,
        )
        self._connection.row_factory = aiosqlite.Row # 设置行格式
        await self._connection.execute("PRAGMA foreign_keys = ON") # 启用外键

    # 初始化表结构：执行前面定义的全部建表 SQL
    async def initialize(self) -> None:
        async with self.session() as connection:
            await connection.executescript(SCHEMA)

    # 关闭连接
    async def close(self) -> None:
        if self._connection is None:
            return # 没有连接时，直接返回
        await self._connection.close() # 关闭真实连接
        self._connection = None # 恢复未连接状态

    # 独占使用连接
    @asynccontextmanager
    async def session(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            yield self.connection

    # 事务：提交和回滚
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            connection = self.connection
            await connection.execute("BEGIN")
            try:
                yield connection
            except BaseException:
                await connection.rollback()
                raise # 把原来的异常继续向上传播
            else:
                await connection.commit() # 成功时提交
