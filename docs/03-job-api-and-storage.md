# 阶段 1 学习指南：任务 API 与异步持久化

> 当前项目进度：阶段 1。应用已经能接收任务、把任务保存到 SQLite，并在重启后查询；还没有队列、后台 Worker 和真正的 URL 巡检。

## 1. 先建立全局认识

这一阶段不是要学习“所有 asyncio API”，而是先打通一条最小但完整的生产调用链：

```text
HTTP 请求
  -> Pydantic 校验输入
  -> FastAPI 路由
  -> Service 处理业务语义
  -> Repository 组织 SQL
  -> Database 管理连接、锁和事务
  -> SQLite 持久化
  -> 领域对象
  -> Pydantic 响应模型
  -> JSON 响应
```

创建一个任务后，数据库中会有一条 `jobs` 记录和若干条 `job_targets` 记录。任务状态始终是 `pending`，`inspection_results` 表暂时为空。这是刻意保留的阶段边界，不是功能缺失：只有先看懂“请求如何可靠入库”，下一阶段加入 Queue 和 Worker 时才不会把 HTTP、数据库和后台任务混在一起。

### 1.1 本阶段已经实现什么

- `POST /api/v1/jobs`：校验名称和 URL，创建任务，返回 HTTP 201；
- `GET /api/v1/jobs/{job_id}`：查询任务及其 URL；
- `GET /api/v1/jobs/{job_id}/results`：查询任务和结果，当前结果固定为空；
- 使用 `aiosqlite` 异步等待数据库操作；
- 使用事务保证任务和目标 URL 要么全部写入，要么全部不写；
- 使用应用生命周期统一连接和关闭数据库；
- 使用全局异常处理器把领域异常转换为 HTTP 404；
- 使用临时数据库完成接口、校验和重启持久化测试。

### 1.2 本阶段明确没有什么

- 没有 `asyncio.Queue`；
- 没有 `asyncio.create_task()`；
- 没有后台 Worker；
- 没有并发访问 URL；
- 没有 HTTPX 外部请求、超时、重试和取消；
- 不会把 `pending` 更新成 `running` 或 `completed`。

看到任务一直是 `pending`、结果一直是 `[]`，说明当前实现符合阶段目标。

## 2. 本节课需要掌握的知识

### 2.1 必须掌握：异步调用链与 `await`

本项目的创建任务调用链是：

```python
# API 层
job = await service.create_job(...)

# Service 层
return await self._repository.create(...)

# Repository 层
await connection.execute(...)
```

==`async def` 定义的是协程函数。调用它不会立刻跑完整个函数，而是产生一个协程对象；只有交给事件循环并 `await`，才能获得最终结果。==

在这条链上漏掉任何一层 `await`，上层拿到的就可能是协程对象，而不是 `Job` 或数据库执行结果。常见表现包括：

- 返回值类型不对；
- JSON 序列化失败；
- SQL 没有按预期执行；
- 程序结束时出现 `coroutine was never awaited` 警告。

还要注意：`await` 的含义是“等待时允许事件循环调度其他工作”，不是“自动创建一个并发后台任务”。当前阶段没有 `create_task()`，一次请求内部仍然按顺序完成校验、写库和返回。

### 2.2 必须掌握：事件循环负责什么

启动命令是：

```powershell
python -m uvicorn app.main:app --reload
```

Uvicorn 负责创建和运行事件循环，FastAPI 在这个循环中执行异步路由和生命周期函数。因此 Web 路由里不能再调用 `asyncio.run()`。`asyncio.run()` 适合普通脚本的同步入口，不适合已经处于事件循环中的代码。

本阶段的数据库调用通过 `aiosqlite` 完成。SQLite 原生接口是同步的，`aiosqlite` 把实际操作排到后台线程，再让当前协程异步等待结果。等待数据库时，事件循环可以处理其他就绪任务。

这不等于数据库会无限并行。本项目只有一个共享连接，并用一把锁串行保护它，所以数据库会话仍是逐个执行的。

### 2.3 必须掌握：`async with` 和异步上下文管理器

仓储层使用：

```python
async with self._database.transaction() as connection:
    await connection.execute(...)
```

它可以理解为异步版本的“进入资源作用域 -> 使用资源 -> 离开时清理”。`Database.transaction()` 由 `@asynccontextmanager` 定义：

- 进入作用域时异步获取锁并执行 `BEGIN`；
- `yield connection` 把连接交给 `with` 代码块；
- 正常退出时 `commit()`；
- 出现异常或取消时 `rollback()`；
- 最终释放锁。

`async with` 适合连接、事务、锁、HTTP Client 等需要可靠进入和退出的异步资源。

### 2.4 必须掌握：事务边界

创建任务需要写两张表：

1. 向 `jobs` 插入任务主体；
2. 向 `job_targets` 插入这批 URL。

这两步必须位于同一事务。假如写入第 2 个 URL 时失败：

- 有事务：回滚整个操作，不留下半成品；
- 无事务：可能留下只有任务、没有完整 URL 的错误数据。

事务边界应当尽量短。本阶段事务只包住本地 SQL，未来绝不能在事务内部请求外部 URL，否则慢速网络会一直占用数据库连接和锁，所有其他数据库操作都会排队。

### 2.5 必须掌握：`asyncio.Lock` 与共享连接

应用生命周期只创建一个 `aiosqlite.Connection`。如果两个请求在同一个连接上交错执行：

```text
请求 A: BEGIN
请求 B: BEGIN / SELECT / COMMIT
请求 A: INSERT / COMMIT
```

事务边界就可能互相干扰。因此 `Database.session()` 和 `Database.transaction()` 都先获取同一把 `asyncio.Lock`，保证一次只有一个协程使用共享连接。

这说明一个重要事实：即使 asyncio 通常运行在单线程里，只要代码在共享状态操作之间发生 `await`，仍然可能出现竞态条件。单线程不等于无需并发控制。

当前方案适合教学和小型 SQLite 应用，代价是数据库吞吐量有限。生产数据库通常使用连接池，每个事务独占一个连接，而不是让所有请求共用一个连接和一把全局锁。

### 2.6 必须掌握：应用生命周期

`lifespan()` 把数据库资源的生命周期绑定到应用：

```text
应用启动
  -> 创建 Database
  -> connect()
  -> initialize() 建表
  -> 创建 Repository 和 Service
  -> 保存到 app.state
  -> yield：开始处理请求

应用停止或启动后发生异常
  -> finally
  -> close() 数据库
```

共享资源必须在请求开始前准备好，在应用结束时可靠关闭。不要在每个路由里临时创建数据库对象，也不要只创建不关闭。

### ==2.7 必须掌握：分层和依赖方向==

各层职责如下：

| 层 | 负责什么 | 不应该负责什么 |
| --- | --- | --- |
| API / Schema | HTTP 路径、状态码、输入输出校验 | SQL、事务、核心业务规则 |
| Service | 业务流程、业务异常、跨仓储编排 | HTTP 响应格式、SQL 细节 |
| Repository | SQL、行与领域对象的转换 | HTTP 状态码、请求校验 |
| Database | 连接、建表、锁、事务 | “任务不存在”等业务语义 |
| Domain | 稳定的业务数据和状态 | FastAPI、Pydantic、SQLite 细节 |

依赖方向大致是：

```text
API -> Service -> Repository -> Database
             \-> Domain <-/
```

分层看起来比“一个路由写完所有代码”多，但它让后续 Worker 可以直接复用 Service/Repository，而不需要伪造 HTTP 请求。

==这里重点对Job领域对象（models.py）和响应模型JobResponse（schema.py）作出区分；以及`from_domain() `转成响应模型有什么作用？（2.8 讲得比较粗略）==

首先，在写入数据库后，有这几个步骤：

```
13. Repository 构造 Job 领域对象
14. JobResponse.from_domain() 转成响应模型
15. FastAPI 把响应模型序列化成 JSON
```

它们分别对应三个不同世界：

```
业务内部使用       HTTP 接口使用          网络上传输
Job              JobResponse            JSON
领域对象           响应模型                响应正文
```

1. Job 领域对象

   领域就是当前系统所处理的业务范围。

   这个项目的业务是“异步站点巡检”，其中包含这些业务概念：

   - 任务 `Job`；
   - 任务状态 `JobStatus`；
   - 巡检结果 `InspectionResult`；
   - URL；
   - 完成数量；
   - 失败数量。

   所以 `Job` 领域对象可以理解为：

   > Python 程序内部对于“一个巡检任务”的正式表达。

   只是一个普通 Python 对象

   不关心 HTTP，只关心：在业务系统内部，一个任务应该是什么

2. 什么是`JobResponse`响应模型

   `JobResponse` 是 Pydantic 模型。

   它表示：

   > 创建任务或查询任务时，HTTP API 准备返回给客户端的数据结构。

3. 对比

   ```
   Job：系统内部怎样理解一个任务
   JobResponse：系统对外怎样展示一个任务
   ```

4. `from_domain`做了什么

   它的作用是：

   > 把 `Job` 领域对象明确转换成 `JobResponse` API 响应对象。

5. 核心理解

   这个任务的最后几步：

   ```
   Repository：
   数据库写入成功后，构造一个内部业务对象 Job
   
   Service：
   把 Job 交给 API
   
   API：
   使用 from_domain() 把 Job 转成对外响应对象 JobResponse
   
   FastAPI：
   把 JobResponse 序列化成 JSON
   ```

   三者边界：

   ```
   Job
     = 内部怎样表示任务
   
   JobResponse
     = HTTP API 准备怎样返回任务
   
   from_domain()
     = 从内部表示到外部表示的转换规则
   ```

   





### ==2.8 应该理解：领域模型与 API 模型为什么分开==

- `app/domain/models.py` 使用 dataclass，表示业务内部数据；
- `app/api/schemas.py` 使用 Pydantic，负责外部 JSON 的校验和序列化。

同一个任务会经历这些类型变化：

```text
请求 JSON
  -> CreateJobRequest
  -> name: str + urls: tuple[str, ...]
  -> Job 领域对象
  -> JobResponse
  -> 响应 JSON
```

分开后，API 可以改变字段格式而不污染领域对象；未来 Worker 也可直接操作 `Job`，不用依赖 HTTP 模型。

## 3. 应该如何看代码

### 3.1 不要按文件名从头到尾扫

初学者常从 `app/__init__.py` 开始逐文件阅读，很快会迷失。更有效的方法是选一个业务动作，沿调用链纵向追踪。

推荐先追踪“创建任务”，因为它同时覆盖校验、依赖注入、Service、事务和两张表：

1. 从 `tests/test_jobs.py::test_create_and_query_job` 看期望行为；
2. 到 `app/api/schemas.py::CreateJobRequest` 看输入如何校验；
3. 到 `app/api/jobs.py::create_job` 看 HTTP 如何进入业务层；
4. 到 `app/api/dependencies.py::get_job_service` 看 Service 从哪里来；
5. 到 `app/services/jobs.py::JobService.create_job` 看业务入口；
6. 到 `app/repositories/jobs.py::JobRepository.create` 看 SQL 和领域对象；
7. 到 `app/repositories/database.py::transaction` 看提交和回滚；
8. 回到 `app/main.py::lifespan` 看 Database 和 Service 何时创建。

第二遍追踪“查询不存在的任务”：

```text
GET /jobs/{id}
  -> Repository 返回 None
  -> Service 抛 JobNotFoundError
  -> main.py 全局异常处理器
  -> HTTP 404
```

第三遍追踪“应用重启后仍能查询”：

```text
第一次 lifespan: connect -> 写入 -> close
第二次 lifespan: connect 同一路径 -> 查询到原记录 -> close
```

### 3.2 阅读每个函数时固定问六个问题

1. 谁调用它？
2. 参数是什么类型？
3. 返回什么类型？
4. 哪一行会发生 `await`，等待的是什么？
5. 可能抛出什么异常，谁负责处理？
6. 它是否持有连接、锁或事务，作用域到哪里结束？

如果能回答这六个问题，就不是“看过代码”，而是真正理解了代码。

### 3.3 本阶段重点看什么

第一优先级：

- `app/main.py`：对象装配和资源生命周期；
- `app/repositories/database.py`：异步资源、锁和事务；
- `app/repositories/jobs.py`：真正的数据读写；
- `app/api/jobs.py`：HTTP 到业务层的入口。

第二优先级：

- `app/api/schemas.py`：请求校验和响应转换；
- `app/services/jobs.py`：层之间如何隔离；
- `tests/test_jobs.py`：行为契约。

快速浏览即可：

- 各目录的 `__init__.py`：当前只是包标记；
- `app/core/logging.py`：当前只有基础日志格式；
- `app/api/health.py`：已在阶段 0 解释，本阶段没有核心变化。

### 3.4 看异步代码时特别标记三类位置

- 所有 `await`：这是协程可能让出执行权的位置；
- 所有 `async with`：这里通常有资源获取与清理；
- 所有 `try/except/finally`：这里定义异常、取消和清理语义。

然后检查：`await` 前后是否存在共享状态？发生异常后资源是否释放？事务是否可能持有过久？这是阅读生产异步代码最重要的习惯。

## 4. 项目文件地图

```text
learn-async/
├── app/
│   ├── main.py                  # 应用工厂、生命周期、异常处理、路由装配
│   ├── api/
│   │   ├── dependencies.py      # 从 app.state 取得 JobService
│   │   ├── health.py            # 健康检查
│   │   ├── jobs.py              # 三个任务 HTTP 接口
│   │   └── schemas.py           # 请求/响应 Pydantic 模型
│   ├── core/
│   │   ├── config.py            # 配置与数据库路径
│   │   └── logging.py           # 日志初始化
│   ├── domain/
│   │   ├── exceptions.py        # 业务异常
│   │   └── models.py            # JobStatus、Job、InspectionResult
│   ├── repositories/
│   │   ├── database.py          # Schema、连接、锁、事务
│   │   └── jobs.py              # Job SQL 与对象映射
│   └── services/
│       └── jobs.py              # 任务业务入口
├── tests/
│   ├── conftest.py              # 异步客户端和临时数据库夹具
│   ├── test_jobs.py             # 任务行为与持久化测试
│   ├── test_health.py           # 健康检查测试
│   └── test_settings.py         # 配置测试
├── data/                        # 默认 SQLite 文件目录
├── pyproject.toml               # 依赖、pytest 异步模式
└── README.md                    # 安装、启动和基础使用
```

## ==5. 三条完整调用链==

### 5.1 创建任务

请求：

```http
POST /api/v1/jobs
Content-Type: application/json

{
  "name": "  Local services  ",
  "urls": [
    "https://example.com/health",
    "https://example.org/status"
  ]
}
```

执行顺序：

```text
1. FastAPI 匹配 POST /api/v1/jobs
2. Pydantic 构造 CreateJobRequest
3. name 去除首尾空格，urls 检查格式、数量和重复
4. Depends(get_job_service) 从 app.state 取得共享 Service
5. create_job() 把 HttpUrl 转成 str，再转成 tuple
6. await JobService.create_job()
7. await JobRepository.create()
8. 生成 UUID 和 UTC 时间
9. transaction() 获取锁并 BEGIN
10. INSERT jobs
11. executemany INSERT job_targets
12. 正常退出 transaction()，COMMIT 并释放锁
13. Repository 构造 Job 领域对象
14. JobResponse.from_domain() 转成响应模型
15. FastAPI 序列化 UUID、枚举和 datetime，返回 201
```

关键观察：事务期间没有外部网络 I/O；创建后直接用已知值构造 `Job`，不再额外查询一次数据库。

### 5.2 查询任务和 404

```text
GET /api/v1/jobs/{job_id}
  -> FastAPI 把路径字符串解析为 UUID
  -> JobService.get_job(job_id)
  -> JobRepository.get(job_id)
  -> session() 获取锁
  -> SELECT jobs
  -> SELECT job_targets ORDER BY position
  -> 释放锁
  -> 数据库行转换为 Job
```

如果 `jobs` 查不到：

```text
Repository: return None
  -> Service: raise JobNotFoundError(job_id)
  -> FastAPI: job_not_found_handler()
  -> 404 {"detail": "Job ... was not found"}
```

Repository 只表达“查不到”，Service 决定“业务上这是异常”，API 决定“这个异常对应 HTTP 404”。三层各自只处理自己的语义。

### 5.3 查询结果

```text
GET /api/v1/jobs/{job_id}/results
  -> Service 先调用 get_job()，保证任务存在
  -> Repository.list_results(job_id)
  -> SELECT inspection_results
  -> 返回 (Job, tuple[InspectionResult, ...])
  -> API 转成 JobResultsResponse
```

当前没有写结果的代码，因此 `results` 为 `[]`。先查询任务再查询结果还有一个作用：不存在的任务返回 404，而不是含有空数组的 200，避免把“不存在”和“存在但暂无结果”混为一谈。

## 6. 重要文件详细解释

### 6.1 `app/main.py`：应用的装配中心

#### `lifespan()`

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    database = Database(settings.database_path)
    await database.connect()
    try:
        await database.initialize()
        app.state.database = database
        app.state.job_service = JobService(JobRepository(database))
        yield
    finally:
        await database.close()
```

逐句理解：

- `@asynccontextmanager`：把含有一个 `yield` 的异步生成器变成异步上下文管理器；
- `settings = app.state.settings`：读取 `create_app()` 之前放入应用状态的配置；
- `Database(...)`：这里只构造对象，还没有打开连接；
- `await database.connect()`：真正连接 SQLite；
- `await database.initialize()`：执行建表脚本，`IF NOT EXISTS` 使其可重复运行；
- `JobRepository(database)`：Repository 依赖 Database；
- `JobService(...)`：Service 依赖 Repository；
- `app.state.job_service = ...`：保存应用级共享对象，供每个请求取用；
- `yield`：启动阶段到此结束，应用开始接收请求；
- `finally`：无论正常停止还是 `yield` 之后发生异常，都会尝试关闭数据库。

这几行也叫“组合根”：对象之间如何连接，只集中在入口处决定。Service 本身不负责创建 Repository，Repository 也不负责读取环境变量，因此更容易测试和替换。

#### 异常转换

```python
async def job_not_found_handler(
    request: Request,
    exc: JobNotFoundError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )
```

`JobNotFoundError` 是领域异常，不应知道 HTTP。`main.py` 把它注册成全局处理器，所有路由只要抛出该异常都会得到一致的 404。`request` 是 FastAPI 异常处理器签名要求的一部分，本实现暂时没有使用它。

#### `create_app()`

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    configure_logging(resolved_settings.log_level)
    application = FastAPI(..., lifespan=lifespan)
    application.state.settings = resolved_settings
    application.add_exception_handler(...)
    application.include_router(health_router)
    application.include_router(jobs_router)
    return application
```

重点是“应用工厂”模式：生产启动时不传参数，从环境变量读取配置；测试可以直接传入临时数据库配置。这避免测试修改全局变量，也让每个测试拥有独立应用。

文件最后的 `app = create_app()` 是 Uvicorn 在 `app.main:app` 中寻找的模块级对象。导入模块时会创建 FastAPI 对象，但数据库直到进入 lifespan 才真正连接。

### 6.2 `app/api/schemas.py`：守住 HTTP 边界

#### 创建请求模型

```python
class CreateJobRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    urls: Annotated[list[HttpUrl], Field(min_length=1, max_length=50)]
```

这里同时限制：

- `name` 长度为 1–100；
- `urls` 至少 1 个、最多 50 个；
- 每项必须是合法 HTTP/HTTPS URL。

`Annotated` 把 Python 类型和 Pydantic 约束放在一起；静态工具仍能看到字段基础类型。

#### 名称校验

```python
@field_validator("name")
@classmethod
def strip_and_validate_name(cls, value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("name must not be blank")
    return stripped
```

只用 `min_length=1` 无法拦住 `"   "`。因此先去除首尾空格，再检查是否为空，并返回清洗后的值。测试中传入 `"  Local services  "`，最终响应是 `"Local services"`。

#### URL 去重

```python
normalized = [str(url) for url in value]
if len(normalized) != len(set(normalized)):
    raise ValueError("urls must not contain duplicates")
```

此时每个值已经被解析成 `HttpUrl`。先规范化再比较，使 `https://example.com` 与 `https://example.com/` 被视为同一个 URL。重复时 Pydantic 统一返回 422，请求不会进入 Service 和数据库。

#### 响应模型转换

`JobResponse.from_domain()` 是显式的边界转换：

```python
@classmethod
def from_domain(cls, job: Job) -> "JobResponse":
    return cls(
        id=job.id,
        name=job.name,
        urls=list(job.urls),
        ...
    )
```

领域层用不可变 `tuple` 保存 URL，API JSON 更自然地表现为数组，因此这里转成 `list`。FastAPI/Pydantic 会继续把 UUID、`JobStatus` 和 datetime 转成 JSON 可表示的字符串。

### 6.3 `app/api/jobs.py` 精读笔记

本文件是任务 API 的入口层，只负责“接收并转换输入 -> 调用 Service -> 转换输出”，不负责 SQL 和事务。

```python
router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
```

三个路由共享 `/api/v1/jobs` 前缀；`tags` 用于 OpenAPI 文档分组。函数执行前，FastAPI 会先校验请求参数，并通过 `Depends(get_job_service)` 注入应用启动时创建的 `JobService`。

#### `create_job()`：创建任务

```text
POST /api/v1/jobs
  -> CreateJobRequest 校验名称和 URL
  -> HttpUrl 列表转换为 tuple[str, ...]
  -> await service.create_job()
  -> Repository 在事务中写入 jobs 和 job_targets
  -> 得到 Job 领域对象
  -> JobResponse.from_domain(job)
  -> 返回 201
```

`payload` 进入函数时已经通过 Pydantic 校验。路由把 Pydantic 的 `HttpUrl` 转成普通字符串，再把可变 `list` 转成领域层使用的不可变 `tuple`。创建成功返回 HTTP 201。

#### `get_job()`：查询任务

```text
GET /api/v1/jobs/{job_id}
  -> FastAPI 将路径参数转换为 UUID
  -> await service.get_job(job_id)
  -> Repository 查询任务和目标 URL
  -> 找到：Job -> JobResponse
  -> 未找到：JobNotFoundError -> 全局处理器返回 404
```

路径不是合法 UUID 时，FastAPI 直接返回 422，不会进入函数或访问数据库。

#### `get_job_results()`：查询任务及巡检结果

```text
GET /api/v1/jobs/{job_id}/results
  -> await service.get_results(job_id)
  -> 先确认任务存在，再查询 inspection_results
  -> Job 转成 JobResponse
  -> 每个 InspectionResult 转成 InspectionResultResponse
  -> 组合为 JobResultsResponse
```

`job, results = ...` 是元组解包。结果通过列表推导逐项转换。当前阶段还没有 Worker 写结果，因此列表通常为空。

#### `await` 在这里的作用

调用 `async def` 函数只会得到协程对象；`await` 才会执行并等待该协程的最终结果：

```python
job = await service.get_job(job_id)
```

整个等待链：

```
API Task
  await Service
    await Repository
      await aiosqlite.execute()
      await cursor.fetchone()
```

当底层数据库操作尚未完成时：

1. 当前 HTTP 请求所在的 Task 暂停；
2. 控制权交还事件循环，事件循环可以处理其他就绪任务；
3. 数据库操作完成后，该 Task 重新获得调度；
4. 代码从 `await` 后继续，结果赋给 `job`。

如果等待对象已经完成，代码可能立即继续。`await` 不会自动创建后台任务，也不代表一定创建线程；本文件中的每一步仍按顺序执行。被等待函数抛出的异常会从 `await` 处继续向上传播。

#### `from_domain()`、领域模型与 `classmethod`

`from_domain()` 的核心作用是：

> 把内部使用的领域模型，转换成对外使用的 API 响应模型。

领域模型是对业务概念的内部表达，例如 `Job` 和 `InspectionResult`。它们定义在 `domain` 层，只描述任务、状态和结果，不关心 HTTP、JSON、FastAPI 或 Pydantic。

`from_domain()` 负责把领域模型转换成 API 响应模型：

```text
Job                         -> JobResponse
InspectionResult            -> InspectionResultResponse
内部业务对象                 -> 可校验、可序列化的 HTTP 响应对象
```

转换过程中可以调整类型或隐藏字段，例如把 `Job.urls` 的 `tuple` 转为响应中的 `list`。这样领域结构和对外 API 可以分别演进。

```python
@classmethod
def from_domain(cls, job: Job) -> "JobResponse":
    return cls(id=job.id, name=job.name, ...)
```

`@classmethod` 表示方法接收类本身 `cls`，而不是实例 `self`。调用 `JobResponse.from_domain(job)` 时，`cls` 就是 `JobResponse`；`cls(...)` 创建并返回一个新响应对象。因此它是一个有明确语义的“替代构造方法”。

****

### 6.4 `app/api/dependencies.py`：取得共享 Service

```python
def get_job_service(request: Request) -> JobService:
    return request.app.state.job_service
```

该函数是同步函数，因为它只做一次内存属性读取，没有可等待 I/O。不要为了“项目是异步的”而把所有函数都写成 `async def`。

Service 是 lifespan 启动时创建的应用级对象。依赖函数隐藏了 `app.state` 的读取细节，路由只依赖 `JobService`。以后测试或扩展依赖时，也可以通过 FastAPI dependency override 替换它。

### 6.5 `app/domain/models.py`：稳定的业务语言

#### 状态枚举

```python
class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

枚举避免代码到处散落容易拼错的字符串。继承 `StrEnum` 后，它既有枚举约束，也方便存为数据库文本并序列化为 JSON。

当前只会产生 `PENDING`，其他状态是后续 Worker、部分失败和取消功能的领域准备。

#### 不可变 dataclass

```python
@dataclass(frozen=True, slots=True)
class Job:
    ...
```

- `frozen=True`：构造后不能直接改字段，状态更新应显式写入数据库并产生新对象；
- `slots=True`：限制实例属性集合，并减少一些内存开销；
- `urls: tuple[str, ...]`：使用不可变序列，避免任务对象被意外原地修改。

`InspectionResult` 当前只有模型和查询映射，还没有创建结果的方法。这正好反映阶段边界。

### 6.6 `app/services/jobs.py`：业务语义层

`app/services/jobs.py` 是任务业务层（Service Layer）。

它位于 API 层和 Repository 层之间：

```
api/jobs.py
    ↓
JobService
    ↓
JobRepository
    ↓
SQLite
```

代码结构：

```py
class JobService:
    def __init__(self, repository: JobRepository) -> None:
        self._repository = repository
```

它主要负责：

- 向 API 提供任务相关的业务操作；
- 调用 Repository 读写数据；
- 处理业务规则和业务异常；
- 编排多个数据库操作；
- 避免 API 层直接接触 SQL。

Repository 负责“怎样访问数据库”，Service 负责“业务上应该怎样处理结果”。

先看构造函数：

```py
def __init__(self, repository: JobRepository) -> None:
    self._repository = repository
```

`repository` 是创建 `JobService` 时传进来的 `JobRepository` 对象。

在 `main.py` 中：

```py
app.state.job_service = JobService(
    JobRepository(database)
)
```

执行过程是：

```
1. JobRepository(database)
   创建一个 Repository 对象

2. JobService(repository)
   把 Repository 对象传给 JobService.__init__()

3. self._repository = repository
   将 Repository 保存到当前 Service 对象中
```

等价于：

```py
repository = JobRepository(database)
service = JobService(repository)
```

==**`self` 表示当前创建出来的 `JobService` 实例。**==

例如：

```py
service = JobService(repository)
```

执行 `__init__()` 时：

```py
self
```

就是 `service` 这个对象。

所以：

```py
self._repository = repository
```

可以理解为：

> 把外部传进来的 Repository 保存到当前 Service 对象的 `_repository` 属性中。

**为什么有下划线？**

前面的单下划线 `_` 是 Python 的命名约定，表示：

> 这是类的内部实现属性，外部代码通常不应直接使用。

这种写法叫**依赖注入**

好处是：

- Service 不需要知道数据库如何创建；
- 可以在多个地方复用 Service；
- 测试时可以传入假的 Repository；
- 更换数据库实现时，Service 的业务逻辑不一定需要修改；
- 所有对象的创建集中在 `main.py` 中，更容易管理生命周期。



### 6.7 `app/repositories/database.py`：本阶段最重要的异步文件

#### 数据库结构

三张表的关系是：

```text
jobs (1)
  ├── (N) job_targets
  └── (N) inspection_results
```

`jobs` 保存任务摘要和进度；`job_targets` 保存输入 URL 及原始顺序；`inspection_results` 保存未来每个 URL 的巡检结果。

重要约束：

- 主键和外键保证关联关系；
- `UNIQUE (job_id, position)` 保证同一任务的位置不重复；
- `UNIQUE (job_id, url)` 保证同一任务 URL 不重复；
- `CHECK (... >= 0)` 防止计数和耗时为负；
- 索引加速按创建时间或任务 ID 查询；
- `ON DELETE CASCADE` 表示删除任务时关联目标和结果一起删除。

Pydantic 校验提供友好错误，数据库约束提供最后一道数据完整性防线，两者不是重复浪费。

#### `connect()`

```python
async def connect(self) -> None:
    if self._connection is not None:
        return
    if self.path != Path(":memory:"):
        self.path.parent.mkdir(parents=True, exist_ok=True)
    self._connection = await aiosqlite.connect(
        self.path,
        isolation_level=None,
    )
    self._connection.row_factory = aiosqlite.Row
    await self._connection.execute("PRAGMA foreign_keys = ON")
```

- 重复调用时直接返回，避免覆盖已有连接；
- 文件数据库先创建父目录；
- `isolation_level=None` 开启自动提交模式，因此普通 session 查询无需手动提交；
- 需要原子性的写操作由 `transaction()` 显式执行 `BEGIN`；
- `row_factory = aiosqlite.Row` 允许用 `row["name"]` 读取列，避免依赖列顺序；
- SQLite 默认不会自动启用外键约束，所以每个连接都要执行 `PRAGMA foreign_keys = ON`。

#### `connection` 属性

```python
@property
def connection(self) -> aiosqlite.Connection:
    if self._connection is None:
        raise RuntimeError("Database is not connected")
    return self._connection
```

它把“未连接却尝试使用”的生命周期错误尽早暴露出来，而不是在更深的 SQL 代码中出现难懂的 `NoneType` 错误。

#### ==` async with`与`@asynccontextmanager`==

先来复习`yield`：

用 `yield`：

1. **返回给调用方**一个值
2. 函数**暂停**在原地，保留所有局部变量和执行位置
3. 下次被唤醒时，**从暂停处继续往下跑**。

关键点：

- **`yield 值`** = "产出一个值给调用方，然后暂停"。
- **暂停期间函数状态不丢**（`x`、执行到哪一行都保留）。
- 调用方用 `next()` / `send()` / `throw()` 来恢复或干扰它。

示例：

```py
def g():
    print("a")
    x = yield 1      # 暂停，把 1 交出去
    print("b", x)    # 恢复后继续
    yield 2          # 再暂停，交出 2

gen = g()
print(next(gen))     # 打印 a，返回 1   → 函数停在 yield 1
print(next(gen))     # 打印 b None，返回 2 → 停在 yield 2
```

输出：

```
a
1
b None
2
```

这里有一个很容易搞错的内容：为什么是`b None`而不是`b 1`？

一句话总结：

> `x = yield 1` 要理解成 **"先把 1 送出去并暂停；等被唤醒时，把唤醒时带进来的值赋给 x"**。`next()` 唤醒时什么都没带（即 `None`），所以是 `b None`。

`x = yield 1` 这一行，看起来像"把 1 赋给 x"，**其实完全不是**。它是两个独立的事件：

- `yield 1`（往外出）：把 `1` 交给**调用方**，即 `next()` 的返回值。
- `x =`（往里进）：等生成器被恢复时，把恢复时**传入的值**赋给 `x`。

我们此时用`next()`恢复，等于塞进去`None`；想要得到`b 1`，需用`send`主动传值

---

再看要学的内容，用一个简单例子：

```py
from contextlib import asynccontextmanager

@asynccontextmanager
async def conn():
    print("A. 进入（yield 之前）")
    try:
        yield "资源"
    finally:
        print("D. 退出（yield 之后）")

async def main():
    print("B. 调用 conn()")
    async with conn() as r:
        print("C. with 体内:", r)
    print("E. 结束")
```

注意：

==**`yield`的值 = `as`拿到的值**==

调用顺序：

```
B. 调用 conn()
A. 进入（yield 之前）
C. with 体内: 资源
D. 退出（yield 之后）
E. 结束
```

关键理解：

1. `conn()` 只是**创建一个上下文管理器对象**，函数体还没跑。
2. `async with` 触发 `__aenter__`，生成器开始执行，**运行到 `yield` 处暂停**。
3. with 体执行完后触发 `__aexit__`，生成器**从 `yield` 之后继续执行**（这里是 `finally` 里的清理）。
4. 若 with 体内抛异常，会在 `yield` 处被重新抛出，所以清理代码务必放 `try/finally` 里。

#### `session()`

```python
@asynccontextmanager
async def session(self) -> AsyncIterator[aiosqlite.Connection]:
    async with self._lock:
        yield self.connection # 把连接交给外部代码
```

独占使用连接

**执行过程：**

Repository 使用：

```py
async with self._database.session() as connection:
    cursor = await connection.execute(...)
```

实际过程：

```
1. 执行 session()
2. await 获取 self._lock
3. 通过 self.connection 检查连接是否存在
4. yield 数据库连接
5. 执行 async with 内部的查询
6. 查询结束或发生异常
7. 离开 async with self._lock
8. 自动释放锁
```

重点看`yield`这个地方：

`yield`把连接交给外部代码：

```py
async with database.session() as connection:
```

**此时 `connection` 就是 `yield` 出来的对象**

#### `transaction()`

```python
@asynccontextmanager
async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
    async with self._lock:
        connection = self.connection
        await connection.execute("BEGIN")
        try:
            yield connection
        except BaseException:
            await connection.rollback()
            raise
        else:
            await connection.commit()
```

Repository 中这样使用：

```py
async with self._database.transaction() as connection:
    await connection.execute("INSERT ...")
    await connection.executemany("INSERT ...")
```

成功时：

```
获取锁
  -> BEGIN
  -> yield connection
  -> INSERT jobs
  -> INSERT job_targets
  -> COMMIT
  -> 释放锁
```

失败时：

```
获取锁
  -> BEGIN
  -> yield connection
  -> INSERT jobs
  -> INSERT job_targets 失败
  -> ROLLBACK
  -> 继续抛出异常
  -> 释放锁
```

还是看`yield`这里：

`yield connection`把连接交给 Repository，开始执行`async with`代码块里面的 SQL

#### ==基础知识总结==

1. `yield`的作用
   - 进入 / 退出 的分界线：`yield`前后的代码分别是进入逻辑和退出/清理逻辑
   - `yield` 的值 = `as` 拿到的值
   - `yield`处是异常的回抛点：如果 `with` 体内抛异常，`asynccontextmanager` 会调用生成器的 `throw()`，让异常在 `yield` 这一行被抛出。

### 6.8 `app/repositories/jobs.py`：SQL 与对象映射

#### `create()`

```python
job_id = uuid4()
now = datetime.now(UTC)
```

应用生成 UUID，不依赖数据库自增 ID；时间统一使用带 UTC 时区的 datetime，避免服务器本地时区导致歧义。

第一条 SQL 写任务摘要：

```python
await connection.execute(
    """INSERT INTO jobs (...) VALUES (?, ?, ?, ?, 0, 0, ?, ?)""",
    (...),
)
```

使用 `?` 参数占位符，不直接拼接字符串，可正确处理转义并防止 SQL 注入。状态存储 `JobStatus.PENDING.value`，即字符串 `"pending"`。

第二条 SQL 批量写 URL：

```python
await connection.executemany(
    """INSERT INTO job_targets (job_id, position, url) VALUES (?, ?, ?)""",
    ((str(job_id), position, url) for position, url in enumerate(urls)),
)
```

`enumerate(urls)` 保存输入顺序，查询时才能用 `ORDER BY position` 恢复原顺序。`executemany()` 比在 Python 中逐条重复调用更清晰。

两段 SQL 位于同一个 `transaction()` 内。离开事务后，方法直接用已知数据构造 `Job`，没有多做一次查询。

#### `get()`

先查任务主体：

```python
cursor = await connection.execute(
    "SELECT * FROM jobs WHERE id = ?",
    (str(job_id),),
)
row = await cursor.fetchone()
await cursor.close()
```

单元素参数必须写成 `(str(job_id),)`，末尾逗号表示这是元组；没有逗号就只是括号包围的字符串。

若任务存在，再查 URL 并按原位置排序。两个查询放在同一个 session 锁作用域内，避免其他协程在中间使用共享连接。

游标显式关闭，连接则由应用生命周期统一关闭。最后 `_row_to_job()` 完成以下反向转换：

- 数据库 TEXT -> `UUID`；
- 状态字符串 -> `JobStatus`；
- ISO 时间字符串 -> `datetime`；
- 多行 URL -> `tuple[str, ...]`。

#### `list_results()`

它查询并映射 `inspection_results`。虽然当前总是空元组，但 API 和读取路径先搭好，下一阶段/后续阶段只需增加结果写入逻辑，不需要重写查询接口。

### 6.9 `app/core/config.py`：可注入配置

```python
@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path = Path("data/async_site_inspector.db")
```

默认把数据保存到项目的 `data` 目录。`Settings.from_env()` 允许通过 `APP_DATABASE_PATH` 覆盖。生产启动读环境变量，测试直接构造 `Settings(database_path=tmp_path / "test.db")`。

配置对象不可变，避免应用运行过程中某段代码意外修改数据库路径或日志级别。

### 6.10 `tests/conftest.py`：测试环境如何搭起来

```python
@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(database_path=tmp_path / "test.db", ...))
    transport = ASGITransport(app=app)

    async with lifespan(app):
        async with AsyncClient(transport=transport, base_url="http://test") as test_client:
            yield test_client
```

关键点：

- 每个测试得到独立临时目录和数据库，不污染真实 `data`；
- `ASGITransport` 直接在进程内调用 FastAPI，不监听真实端口；
- 测试仍使用真正的 HTTP 请求/响应转换，而不是直接调用路由函数；
- 显式进入 `lifespan(app)`，保证数据库已连接和建表；
- 离开作用域后先关闭客户端，再关闭应用数据库；
- 异步生成器 fixture 的 `yield` 前是准备，`yield` 后是清理。

`pyproject.toml` 中的 `asyncio_mode = "auto"` 让 pytest-asyncio 自动执行异步测试和夹具，因此测试函数不必逐个添加 `@pytest.mark.asyncio`。

### 6.11 `tests/test_jobs.py`：把测试当成需求看

各测试分别证明：

| 测试 | 它证明什么 |
| --- | --- |
| `test_create_and_query_job` | 清洗名称、创建返回 201、初始计数正确、随后可查询 |
| `test_new_job_has_no_results` | 当前阶段不执行任务，结果为空 |
| `test_unknown_job_returns_404` | 领域异常正确映射为 HTTP 404 |
| `test_duplicate_urls_are_rejected` | URL 规范化后去重，非法输入返回 422 |
| `test_job_survives_application_restart` | 数据确实落盘，不只是存在内存对象里 |

最后一个测试最值得仔细读：它先用第一个应用写入并完整退出 lifespan，再创建第二个应用连接同一个数据库文件并查询。只有这样才能证明关闭连接和“重启”后数据仍在。

## 7. 数据库设计应该看懂什么

### 7.1 为什么 URL 不直接存在 `jobs` 的一个 JSON 字段里

拆成 `job_targets` 后可以：

- 对单个 URL 建唯一约束；
- 保留顺序；
- 未来逐个处理和关联结果；
- 避免每次更新单个目标都重写整个 JSON。

### 7.2 为什么任务表同时保存计数

`total_count`、`completed_count` 和 `failed_count` 是任务摘要。未来查询进度时无需每次扫描所有结果再统计。代价是 Worker 更新结果时必须同步维护计数，这属于后续阶段要处理的数据一致性问题。

当前状态应满足：

```text
total_count == len(urls)
completed_count == 0
failed_count == 0
status == pending
```

### 7.3 为什么 ID 和时间存 TEXT

SQLite 没有专门的 UUID 和 datetime 类型。项目用规范字符串保存，再在 Repository 映射回强类型：

- UUID：`str(uuid)` / `UUID(text)`；
- datetime：`datetime.isoformat()` / `datetime.fromisoformat(text)`。

转换集中在 Repository，可避免字符串形式泄漏到领域层。

## 8. 动手学习顺序

只阅读很容易产生“好像懂了”的错觉。推荐按下面顺序操作。

### 8.1 第一步：跑测试并读测试名

```powershell
conda activate TEST
python -m pytest -q
```

先不看实现，只根据测试名说出系统应有什么行为，再打开测试确认输入与断言。

### 8.2 第二步：启动并手工走一遍接口

```powershell
python -m uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000/docs>，按顺序调用：

1. `POST /api/v1/jobs`；
2. `GET /api/v1/jobs/{job_id}`；
3. `GET /api/v1/jobs/{job_id}/results`；
4. 用随机 UUID 查询，观察 404；
5. 提交重复 URL，观察 422。

PowerShell 示例：

```powershell
$job = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/jobs" `
  -ContentType "application/json" `
  -Body '{"name":"demo","urls":["https://example.com"]}'

$job
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/jobs/$($job.id)"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/jobs/$($job.id)/results"
```

### 8.3 第三步：画出创建任务调用链

不要复制本文，自己写出每一步的函数名、输入类型、输出类型和 `await` 点。至少应包含：

```text
CreateJobRequest
-> create_job route
-> JobService.create_job
-> JobRepository.create
-> Database.transaction
-> Job
-> JobResponse
```

### 8.4 第四步：用断点观察类型变化

建议在以下位置打断点：

- `app/api/jobs.py` 的 `create_job()` 第一行；
- `app/services/jobs.py` 的 `create_job()`；
- `app/repositories/jobs.py` 的事务开始处和返回处；
- `app/repositories/database.py` 的 `yield connection`；
- `JobResponse.from_domain()`。

观察 `payload.urls`、`urls`、数据库参数、`Job` 和 `JobResponse` 分别是什么类型。

### 8.5 第五步：做三个小实验

实验 A：暂时去掉一层 `await`，运行对应测试，记录错误，再恢复。目的是亲眼看到协程对象与最终结果的区别。

实验 B：让第二条 INSERT 使用一个违反唯一约束的值，确认事务回滚后 `jobs` 也没有留下半成品。实验代码不要提交。

实验 C：创建任务、停止应用、重新启动并查询同一个 ID，确认持久化与 Python 内存对象无关。

## 9. 常见误区与排查方式

### 9.1 “写成 `async def` 就会并发”

错误。`async def` 只定义协程。只有运行到需要等待且尚未完成的操作时，当前 Task 才可能把执行权交回事件循环；创建并发任务还需要后续阶段的 `create_task()`、`TaskGroup` 等机制。

### 9.2 “`await` 会创建新线程”

错误。`await` 本身不创建线程，它只是挂起当前协程等待一个 awaitable。`aiosqlite` 的库内部使用工作线程是该库的实现选择，不是每个 `await` 都会如此。

### 9.3 “单线程不会有竞态条件”

错误。只要一次逻辑操作跨越 `await`，其他 Task 就可能在中间运行并修改共享状态。本项目用锁保护共享 SQLite 连接和事务边界。

### 9.4 “有 Pydantic 校验就不需要数据库约束”

错误。数据可能来自其他程序、旧版本或未来 Worker；并发请求也可能绕过单次请求层面的假设。API 校验负责友好反馈，数据库约束负责最终一致性。

### 9.5 “结果为空说明 Worker 出错了”

当前阶段没有 Worker，空结果是预期行为。排查前先确认项目处于哪个阶段，不要用最终系统的预期评判中间阶段。

### 9.6 “数据库异步后 SQL 会并行执行”

当前不会。单连接加全局锁让数据库会话串行。异步的收益是等待数据库期间不必阻塞事件循环线程，而不是让同一个 SQLite 连接同时执行多条事务。

### 9.7 “404 应该在 Repository 里返回”

Repository 不处理 HTTP。它返回 `None`；Service 抛业务异常；API 层统一映射为 404。这样 CLI、Worker 或其他入口仍可复用业务层。

## 10. 自测题

学完后应能不看文档回答：

1. 为什么当前任务永远是 `pending`，结果永远为空？
2. 从 POST JSON 到 SQLite，一共经过了哪些层和类型转换？
3. `await service.create_job()` 与 `service.create_job()` 的返回值有何区别？
4. Uvicorn 已经运行事件循环时，为什么路由里不能调用 `asyncio.run()`？
5. `aiosqlite` 解决了什么问题，又没有解决什么问题？
6. 为什么共享一个连接时要使用 `asyncio.Lock`？
7. 为什么任务和 URL 必须在同一个事务中写入？
8. `session()` 与 `transaction()` 的语义有什么区别？
9. 为什么事务中不应该包含未来的 HTTP 请求？
10. `Job` 与 `JobResponse` 为什么不合并成一个模型？
11. 任务查不到时，异常如何从 Repository 一直变成 404？
12. `test_job_survives_application_restart` 如何证明真正持久化？
13. 为什么 `get_job_service()` 可以是同步函数？
14. 为什么 `(str(job_id),)` 中的逗号不能省略？
15. 如果写入 URL 中途失败，代码的哪一部分负责回滚？

## 11. 完成本阶段的判断标准

如果你能做到下面这些，就可以进入阶段 2：

- 能独立画出创建、查询和 404 三条调用链；
- 能指出每个 `await` 在等待什么；
- 能解释 `async with transaction()` 的进入、正常退出和异常退出；
- 能说清 API、Service、Repository、Database、Domain 各自职责；
- 能解释为什么要同时有 Pydantic 校验和数据库约束；
- 能读懂 `tests/test_jobs.py` 每个测试证明的行为；
- 能启动项目并手工创建、查询任务；
- 能说明当前单连接加锁方案的优点和吞吐量限制。

## 12. 下一阶段如何衔接

阶段 2 会在现有创建链路后增加：

```text
Repository 提交 pending 任务
  -> asyncio.Queue.put(job_id)
  -> API 仍立即返回
  -> 后台 Worker: Queue.get()
  -> pending -> running -> 终态
  -> Queue.task_done()
```

届时重点学习 `asyncio.Queue`、生产者/消费者、`create_task()`、Task 引用和应用关闭。当前阶段的 Database、Repository 和 Service 不会被推翻，而会成为 Worker 复用的基础。

在进入下一阶段前，最重要的是牢记：API 请求的生命周期与后台任务的生命周期不同。API 只负责可靠创建任务并快速返回，真正的耗时工作应由独立的后台执行链路处理。
