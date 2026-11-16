import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.domain.exceptions import JobNotFoundError
from app.repositories.database import Database
from app.repositories.jobs import JobRepository
from app.services.jobs import JobService
from app.workers.jobs import JobWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    database = Database(settings.database_path)
    worker: JobWorker | None = None
    await database.connect() # 连接 sqlite
    try:
        await database.initialize() # 初始化数据库
        repository = JobRepository(database)
        recoverable_job_ids = await repository.prepare_incomplete_for_recovery()
        worker = JobWorker(repository)
        for job_id in recoverable_job_ids:
            await worker.enqueue(job_id)
        worker.start()
        app.state.database = database
        app.state.job_worker = worker
        app.state.job_service = JobService(repository, worker.enqueue)
        logger.info(
            "application_started service=%s environment=%s database=%s recovered_jobs=%d",
            settings.app_name,
            settings.environment,
            settings.database_path,
            len(recoverable_job_ids),
        )
        yield
    finally:
        if worker is not None:
            await worker.stop()
        await database.close()
        logger.info("application_stopped service=%s", settings.app_name)


async def job_not_found_handler(
    request: Request,
    exc: JobNotFoundError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env() # 从环境变量中获取配置
    configure_logging(resolved_settings.log_level) # 配置日志

    application = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    ) # 创建 FastAPI 应用
    application.state.settings = resolved_settings # 设置配置
    application.add_exception_handler(JobNotFoundError, job_not_found_handler) # 添加异常处理
    application.include_router(health_router) # 添加健康检查路由
    application.include_router(jobs_router) # 添加任务路由
    return application


app = create_app()
