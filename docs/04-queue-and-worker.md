# 阶段 2 学习指南：异步队列与后台 Worker

> 当前进度：任务创建后先以 `pending` 状态持久化，再进入内存队列；后台 Worker 取出任务 ID，将任务原子认领为 `running`。本阶段不访问 URL、不生成巡检结果，也不把任务推进到终态。

## 1. 相比阶段 1，本阶段增加了什么

阶段 1 只打通了 HTTP 到 SQLite 的同步业务链路：

```text
POST /jobs
  -> 校验请求
  -> 写入 SQLite
  -> 返回 pending

到此结束，没有后台执行者。
```

阶段 2 在“任务已经可靠入库”之后增加了异步后台处理链路：

```text
POST /jobs
  -> 写入 SQLite，状态为 pending
  -> job_id 放入 asyncio.Queue
  -> API 返回 pending 快照

后台 JobWorker
  -> 从 Queue 取出 job_id
  -> 将 pending 原子更新为 running
  -> 继续等待下一个任务
```

核心变化对比如下：

| 能力 | 阶段 1 | 阶段 2 |
| --- | --- | --- |
| 任务持久化 | 有 | 保留 |
| API 创建后返回 | `pending` | 仍返回 `pending` 快照 |
| 内存任务队列 | 无 | 新增 `asyncio.Queue` |
| 后台执行者 | 无 | 新增单个 `JobWorker` |
| 后台 Task | 无 | `asyncio.create_task()` 启动 |
| 状态变化 | 一直是 `pending` | `pending -> running` |
| 重启恢复 | 只证明数据仍存在 | 重置中断任务并重新入队 |
| 优雅关闭 | 关闭数据库 | 先停 Worker，再关数据库 |
| URL 巡检 | 无 | 仍无，阶段 3 实现 |
| 任务终态 | 无 | 仍无，阶段 3 实现 |

本阶段新增或修改的文件：

```text
app/
├── workers/
│   ├── __init__.py
│   └── jobs.py              # 新增：Queue、后台 Task、消费循环
├── main.py                  # 修改：创建、启动、恢复、停止 Worker
├── services/jobs.py         # 修改：任务入库后调用入队函数
└── repositories/jobs.py     # 修改：恢复未完成任务、原子认领任务

tests/
├── test_worker.py           # 新增：独立测试 Queue 和 Worker
└── test_jobs.py             # 修改：等待并验证 pending -> running

README.md                    # 修改：项目进度更新为阶段 2
docs/00-project-plan.md      # 修改：明确阶段 2 与阶段 3 的边界
```

以下文件的核心逻辑没有变化：

- `app/api/jobs.py`：仍负责参数校验、调用 Service 和响应转换；
- `app/api/schemas.py`：请求和响应结构没有变化；
- `app/domain/models.py`：`RUNNING` 状态在阶段 1 已提前定义；
- `app/repositories/database.py`：继续提供连接、锁、session 和 transaction。

## 2. Worker 是什么，有什么作用

### 2.1 Worker 的定义

Worker 可以理解为：

> 一个长期运行、不断从任务来源中取出工作并执行的后台消费者。

当前项目中的 Worker 是 `JobWorker`。它运行在 Uvicorn 管理的同一个事件循环中，是一个 asyncio Task：

```text
同一个 Python 进程
└── 同一个 asyncio 事件循环
    ├── HTTP 请求 Task A
    ├── HTTP 请求 Task B
    └── JobWorker Task
```

因此，当前 Worker：

- 不是新线程；
- 不是新进程；
- 不是 Celery Worker；
- 不是独立服务器；
- 是通过 `asyncio.create_task()` 启动的长期协程。

### 2.2 为什么需要 Worker

未来访问一批 URL 可能需要几秒甚至更久。如果直接在 POST 请求里执行：

```text
POST -> 逐个访问 URL -> 全部完成 -> 返回
```

客户端必须长时间等待，连接断开还会让任务生命周期难以管理。

使用 Worker 后：

```text
POST -> 保存任务 -> 入队 -> 快速返回 job_id
                               |
                               v
                     Worker 在后台继续处理
```

这样把两个生命周期分开：

- HTTP 请求生命周期：只负责可靠接单；
- 后台任务生命周期：负责耗时执行。

本阶段 Worker 只练习“消费任务并认领”，真正访问 URL 会在阶段 3 加入。

### 2.3 生产者、队列与消费者

这是典型的生产者/消费者模型：

```text
生产者                         消费者
JobService                    JobWorker
    |                             ^
    | await queue.put(job_id)     | await queue.get()
    v                             |
          asyncio.Queue
```

- `JobService` 是生产者：创建任务后把 `job_id` 放入队列；
- `asyncio.Queue` 是缓冲区：保存尚未消费的 ID；
- `JobWorker` 是消费者：等待并取出 ID；
- SQLite 是事实来源：保存任务的真实持久化状态。

### 2.4 Queue 不会自动处理任务

下面的代码只负责入队：

```python
await queue.put(job_id)
```

Queue 不会自己调用 Repository，也不会改变任务状态。必须有一个正在运行的消费者：

```python
while True:
    job_id = await queue.get()
    await repository.claim_pending(job_id)
```

如果 Worker 没有启动，`put()` 仍可成功，但 ID 只会留在内存队列里。

### 2.5 为什么队列中只放 `job_id`

队列保存：

```python
UUID
```

而不是完整的 `Job` 对象。这样只有一个业务事实来源：SQLite。

如果队列中保存完整 Job，数据库状态可能已变化，但队列里仍是旧对象，形成两个互相矛盾的状态副本。只传 ID 后，Worker 可根据 ID 原子更新或重新读取最新持久化数据。

### 2.6 当前内存 Queue 的限制

`asyncio.Queue` 只存在于当前 Python 进程内：

- 进程退出后，队列内容丢失；
- 多个 Uvicorn 进程各自有独立队列；
- 不能跨服务器共享；
- 不提供消息持久化、确认重投和死信队列。

项目通过“任务先写 SQLite”和“启动时扫描未完成任务”做基础恢复，但它仍不等于 Redis、RabbitMQ、Kafka 或专用任务系统。

## 3. 本阶段必须掌握的异步知识

### 3.1 协程对象与 Task 的区别

调用异步函数：

```python
worker._run()
```

只会产生协程对象，不会自动在后台持续执行。

直接等待：

```python
await worker._run()
```

会进入无限消费循环，lifespan 无法继续执行到 `yield`，应用也就无法完成启动。

创建 Task：

```python
task = asyncio.create_task(worker._run())
```

会把协程包装成由事件循环调度的 Task，并立即返回 Task 对象。当前启动流程可以继续，Worker 则在事件循环中独立推进。

### 3.2 为什么要保存 Task 引用

项目保存：

```python
self._task: asyncio.Task[None] | None
```

这样可以：

- 防止重复启动；
- 判断 Worker 是否已经结束；
- 关闭时等待它真正退出；
- 获取并传播后台 Task 的异常；
- 明确 Task 的生命周期归谁管理。

只调用 `create_task()` 却丢弃返回值，会让关闭和异常处理变得不可靠。

### 3.3 Queue 的四个核心操作

```python
await queue.put(item)      # 放入一项
item = await queue.get()   # 等待并取出一项
queue.task_done()          # 声明这项已处理完
await queue.join()         # 等待所有入队项处理完
```

Queue 内部维护“未完成项计数”：

```text
每次 put()       -> 计数 +1
每次 task_done() -> 计数 -1
计数归零         -> join() 返回
```

`get()` 只表示“取到了”，不表示“处理完成”。所以每次成功 `get()` 后都必须恰好调用一次 `task_done()`。

### 3.4 哨兵值

Worker 的队列类型是：

```python
asyncio.Queue[UUID | None]
```

普通数据是 UUID，特殊值 `None` 是停止哨兵：

```text
UUID -> 处理任务
None -> 退出消费循环
```

哨兵和普通数据一起按 FIFO 排队。把 `None` 放在现有任务之后，就能让 Worker 先处理之前已排队的任务，再退出。

### 3.5 `CancelledError` 与普通异常

普通任务处理错误应记录后继续消费下一项：

```python
except Exception:
    logger.exception(...)
```

但取消信号必须继续传播：

```python
except asyncio.CancelledError:
    raise
```

如果吞掉 `CancelledError`，外部即使请求取消 Worker，它也可能继续运行，导致应用无法可靠关闭。

## 4. `app/workers/jobs.py`：新增的 Worker

这是阶段 2 最重要的新文件。

### 4.1 `JobClaimer` Protocol

```python
class JobClaimer(Protocol):
    async def claim_pending(self, job_id: UUID) -> bool: ...
```

`Protocol` 描述 Worker 真正需要的最小能力：

> 只要对象拥有兼容的异步 `claim_pending()` 方法，就可以传给 JobWorker。

Worker 不需要知道完整 `JobRepository` 还有 `create()`、`get()` 等方法。这样依赖更小，测试也可以传入简单的 `FakeJobRepository`。

这是结构化类型：对象不必显式继承 `JobClaimer`，只要方法形状一致即可。

### 4.2 `__init__()`：保存依赖、创建队列

```python
def __init__(self, repository: JobClaimer) -> None:
    self._repository = repository
    self._queue: asyncio.Queue[UUID | None] = asyncio.Queue()
    self._task: asyncio.Task[None] | None = None
```

三个属性分别表示：

- `_repository`：用于原子认领任务；
- `_queue`：保存 UUID 和停止哨兵 `None`；
- `_task`：保存后台消费 Task，未启动时是 `None`。

`asyncio.Queue()` 没有传 `maxsize`，因此当前是无界队列。它不会因容量满而阻塞生产者，但生产速度远高于消费速度时可能占用过多内存。背压会在后续阶段讨论。

### 4.3 `start()`：启动后台 Task

```python
def start(self) -> None:
    if self._task is not None:
        raise RuntimeError("Job worker is already started")
    self._task = asyncio.create_task(self._run(), name="job-worker")
```

执行过程：

1. 检查 `_task`，防止同一个 Worker 重复启动两个消费循环；
2. 调用 `_run()` 得到协程对象；
3. `create_task()` 将其注册到当前事件循环；
4. 保存 Task 强引用；
5. `start()` 立即返回，Worker 由事件循环随后调度。

`name="job-worker"` 便于日志、调试器和 Task 检查工具识别。

`start()` 本身没有异步等待，所以使用普通 `def`。它必须在已有运行中事件循环的环境调用；当前调用位置是异步 lifespan。

### 4.4 `enqueue()`：生产者入口

```python
async def enqueue(self, job_id: UUID) -> None:
    await self._queue.put(job_id)
```

Service 不直接访问 `_queue`，只调用公开方法 `enqueue()`。

当前队列无界，`put()` 通常可以立即完成；仍保留 `await` 接口，未来改成有界队列时，队列满会暂停生产者并形成背压，而 Service 调用方式无需改变。

### 4.5 `join()`：等待队列处理完成

```python
async def join(self) -> None:
    await self._queue.join()
```

它不是等待 Worker 永久退出，而是等待所有已经 `put()` 的项目都收到对应 `task_done()`。

主要用于测试和需要等待队列排空的控制流程。

### 4.6 `stop()`：让 Worker 有序退出

```python
async def stop(self) -> None:
    task = self._task
    if task is None:
        return
    if task.done():
        self._task = None
        await task
        return

    await self._queue.put(None)
    try:
        await task
    finally:
        self._task = None
```

分三种情况：

1. 从未启动：`_task is None`，无需停止；
2. Task 已结束：清空引用，并 `await task` 取得其异常或结果；
3. Task 正在运行：把 `None` 放入队尾，再等待消费循环退出。

为什么不能只写：

```python
self._task = None
```

因为这只会丢掉引用，后台 Task 仍可能继续运行。

为什么 `await task` 很重要：

- 确认 Worker 真正结束；
- 让后台异常不会变成 “Task exception was never retrieved”；
- 确保数据库关闭前 Worker 不再使用数据库。

### 4.7 `_run()`：长期消费循环

```python
async def _run(self) -> None:
    logger.info("job_worker_started")
    try:
        while True:
            job_id = await self._queue.get()
            try:
                if job_id is None:
                    return
                await self._claim_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("job_worker_failed job_id=%s", job_id)
            finally:
                self._queue.task_done()
    finally:
        logger.info("job_worker_stopped")
```

逐步执行：

1. 记录 Worker 启动日志；
2. 进入无限循环；
3. `await queue.get()`：队列为空时挂起，不会忙循环占用 CPU；
4. 取到 `None`：通过 `return` 退出；
5. 取到 UUID：调用 `_claim_job()`；
6. 普通异常：记录完整异常并继续下一轮；
7. 取消：重新抛出；
8. `finally` 中调用 `task_done()`；
9. 整个循环结束后记录停止日志。

`task_done()` 必须放在 `finally`：认领成功、认领失败、抛异常或取到哨兵，凡是成功执行过一次 `get()`，都要把 Queue 的未完成计数减一。

### 4.8 `_claim_job()`：处理单个队列项

```python
async def _claim_job(self, job_id: UUID) -> None:
    claimed = await self._repository.claim_pending(job_id)
    if claimed:
        logger.info("job_claimed job_id=%s", job_id)
    else:
        logger.warning("job_not_claimed job_id=%s", job_id)
```

Repository 返回布尔值：

- `True`：确实将一个 `pending` 任务改成 `running`；
- `False`：任务不存在，或已不是 `pending`，因此没有认领。

重复 ID 不会让任务重复执行状态转换，失败认领只记录警告。

## 5. `app/services/jobs.py`：创建后入队

### 5.1 新增入队函数依赖

构造函数从阶段 1 的：

```python
JobService(repository)
```

变为：

```python
JobService(repository, enqueue_job)
```

类型声明：

```python
enqueue_job: Callable[[UUID], Awaitable[None]]
```

可以拆开理解：

- `Callable[...]`：一个可调用对象，例如函数或绑定方法；
- `[[UUID], ...]`：接收一个 UUID 参数；
- `Awaitable[None]`：调用后得到可等待对象，等待完成后没有返回值。

`worker.enqueue` 正好满足这个形状：

```python
async def enqueue(self, job_id: UUID) -> None
```

Service 没有依赖完整 `JobWorker`，只依赖“能够异步入队”的最小能力。这也是依赖注入。

### 5.2 `create_job()` 的新增步骤

阶段 1：

```python
return await self._repository.create(name=name, urls=urls)
```

阶段 2：

```python
job = await self._repository.create(name=name, urls=urls)
await self._enqueue_job(job.id)
return job
```

顺序不能颠倒：

```text
正确：先提交数据库，再入队
错误：先入队，再提交数据库
```

如果先入队，Worker 可能立刻取出 ID，但数据库事务还没有提交，导致查不到任务或无法认领。

当前顺序还存在一个已知故障窗口：数据库提交成功后、入队前，如果进程崩溃，任务会留在 `pending`，但 ID 尚未在内存队列中。启动恢复扫描会在下次应用启动时重新找到它。

### 5.3 为什么返回的 Job 仍是 `pending`

Repository 创建时构造的 `Job` 是已提交数据的创建快照：

```python
status=JobStatus.PENDING
```

入队后 Worker 在另一个 Task 中更新数据库。Service 不等待 Worker 认领，因此仍返回这个 `pending` 快照。这正是“API 快速接单，Worker 后台执行”的设计。

## 6. `app/repositories/jobs.py`：恢复和原子认领

阶段 2 新增两个方法。

### 6.1 `prepare_incomplete_for_recovery()`

```python
async def prepare_incomplete_for_recovery(self) -> tuple[UUID, ...]:
```

作用：应用启动时恢复需要重新处理的持久化任务。

第一步，把上次运行中断的 `running` 重置为 `pending`：

```sql
UPDATE jobs
SET status = 'pending', updated_at = ?
WHERE status = 'running';
```

为什么要重置？进程已经重启，旧 Worker 不再存在；数据库里的 `running` 只是中断前留下的状态，不能说明现在仍有人处理。

第二步，查询全部 `pending` ID：

```sql
SELECT id FROM jobs
WHERE status = 'pending'
ORDER BY created_at, id;
```

排序保证恢复顺序稳定：先按创建时间，再用 ID 打破相同时间的并列。

更新和查询位于同一个 transaction 中，因此其他使用此 Database 连接的协程不能在两步之间插入操作。最终把数据库字符串转换为：

```python
tuple[UUID, ...]
```

注意：这是单进程教学恢复策略。真实多实例系统不能让每个实例启动时都把所有 `running` 重置掉，否则一个实例可能破坏另一个实例正在执行的任务。生产系统通常使用租约、心跳、所有者标识和可靠队列。

### 6.2 `claim_pending()`

```python
async def claim_pending(self, job_id: UUID) -> bool:
```

核心 SQL：

```sql
UPDATE jobs
SET status = 'running', updated_at = ?
WHERE id = ? AND status = 'pending';
```

关键不是简单更新，而是条件：

```sql
AND status = 'pending'
```

这让“检查状态并更新状态”成为一条原子 SQL，而不是：

```text
先 SELECT 检查 pending
    -> 中间可能被其他消费者修改
再 UPDATE running
```

条件更新后检查：

```python
claimed = cursor.rowcount == 1
```

- 影响 1 行：任务存在且原来是 `pending`，认领成功；
- 影响 0 行：任务不存在，或已经是 `running`/其他状态，认领失败。

即使同一个 ID 重复入队：

```text
第一次：pending -> running，rowcount = 1
第二次：WHERE status = pending 不匹配，rowcount = 0
```

因此状态认领具有幂等保护。

## 7. `app/main.py`：把所有对象和生命周期连起来

### 7.1 启动顺序

阶段 2 的 lifespan 启动链路：

```text
1. 创建 Database
2. await database.connect()
3. await database.initialize()
4. 创建 JobRepository
5. await repository.prepare_incomplete_for_recovery()
6. 创建 JobWorker(repository)
7. 将恢复出的 job_id 逐个 enqueue
8. worker.start()
9. 保存 database、worker 到 app.state
10. 创建 JobService(repository, worker.enqueue)
11. yield，应用开始接收请求
```

相关代码：

```python
repository = JobRepository(database)
recoverable_job_ids = await repository.prepare_incomplete_for_recovery()
worker = JobWorker(repository)
for job_id in recoverable_job_ids:
    await worker.enqueue(job_id)
worker.start()

app.state.database = database
app.state.job_worker = worker
app.state.job_service = JobService(repository, worker.enqueue)
```

先把恢复任务放入队列，再启动 Worker，可以保证恢复项在启动时已经完整准备好。`worker.start()` 创建后台 Task，lifespan 不会被无限消费循环阻塞。

Service 接收到的是绑定方法：

```python
worker.enqueue
```

调用它时，Python 已经把 `worker` 实例绑定为 `self`，Service 只需传 `job_id`。

### 7.2 为什么保存到 `app.state`

```python
app.state.job_worker = worker
app.state.job_service = JobService(...)
```

- `job_service` 供请求依赖 `get_job_service()` 使用；
- `job_worker` 保存应用级 Worker 引用，也便于检查、测试或未来管理。

每个请求不会重新创建 Queue 和 Worker；它们与应用进程共享生命周期。

### 7.3 关闭顺序

```python
finally:
    if worker is not None:
        await worker.stop()
    await database.close()
```

必须先停 Worker，再关闭数据库：

```text
正确：停止接收请求 -> Worker 处理队列并退出 -> 关闭数据库
错误：先关闭数据库 -> Worker 继续 claim -> Database is not connected
```

`worker` 初始设为 `None`，是为了即使启动过程在创建 Worker 前失败，`finally` 也能安全判断是否需要停止。

## 8. 测试文件的新增部分

### 8.1 `tests/test_worker.py`

#### Fake Repository

```python
class FakeJobRepository:
    def __init__(self) -> None:
        self.claimed_job_ids: list[UUID] = []

    async def claim_pending(self, job_id: UUID) -> bool:
        self.claimed_job_ids.append(job_id)
        return True
```

它没有 SQLite，只记录 Worker 传入的 ID。因为满足 `JobClaimer` Protocol，能直接替代真实 Repository，用来单独验证 Worker，而不把数据库行为混进测试。

#### Worker 测试过程

```text
创建 Fake Repository
  -> 创建 Worker
  -> start()
  -> enqueue 三个 UUID
  -> await worker.join()
  -> stop()
  -> 断言认领顺序与入队顺序一致
```

```python
async with asyncio.timeout(1):
    await worker.join()
```

超时保护可以防止 `task_done()` 漏掉时测试永久挂起。`stop()` 放在 `finally`，断言或等待失败时也尝试清理后台 Task。

### 8.2 `tests/test_jobs.py`

阶段 1 可以在 POST 后立即断言数据库仍是 `pending`。阶段 2 中 Worker 何时运行由事件循环调度，不能假设固定时刻已经变为 `running`。

新增等待助手：

```python
async def wait_for_job_status(client, job_id, expected_status):
    async with asyncio.timeout(1):
        while True:
            response = await client.get(f"/api/v1/jobs/{job_id}")
            job = response.json()
            if job["status"] == expected_status:
                return job
            await asyncio.sleep(0.01)
```

它使用“有超时的业务状态轮询”：

- 状态达到预期就立即返回；
- 尚未达到时异步休眠 10ms，让出事件循环；
- 1 秒仍未达到就失败。

不要使用固定长等待后盲目断言：

```python
await asyncio.sleep(1)
assert status == "running"
```

固定 sleep 会让测试变慢，并且机器快慢变化时仍可能不稳定。

测试现在验证：

- POST 响应是创建时的 `pending` 快照；
- 随后轮询能看到 `running`；
- `updated_at` 在认领后不早于创建时间；
- 状态为 `running` 时结果仍为空；
- 应用重启后，未完成任务能够恢复并再次进入 `running`。

### 8.3 `README.md` 与项目计划

README 将当前进度更新为阶段 2，避免使用者误以为任务仍永远停在 `pending`。

项目计划明确阶段边界：

- 阶段 2：队列、Worker、原子认领，停在 `running`；
- 阶段 3：真实 HTTP 巡检、并发控制、结果与终态。

## ==9. 以 POST 为例：完整调用链==

完整链路必须分成“应用启动准备”“请求内前台链路”和“请求外后台链路”三部分理解。

### 9.1 应用启动准备

在任何 POST 到来前：

```text
Uvicorn 启动事件循环
  -> 进入 FastAPI lifespan
  -> 连接并初始化 SQLite
  -> 创建 JobRepository
  -> 恢复未完成任务并得到 UUID 列表
  -> 创建 JobWorker 和 Queue
  -> 恢复 ID 进入 Queue
  -> create_task(worker._run())
  -> 创建 JobService(repository, worker.enqueue)
  -> yield，开始接收 HTTP 请求
```

此时事件循环里至少有一个长期 Worker Task，它通常暂停在：

```python
job_id = await self._queue.get()
```

队列为空时，它不会占用 CPU。

### 9.2 POST 请求的前台链路

请求：

```http
POST /api/v1/jobs
Content-Type: application/json

{
  "name": "queue demo",
  "urls": ["https://example.com"]
}
```

执行过程：

```text
1. Uvicorn 接收 HTTP 请求，为请求运行一个 Task
2. FastAPI 匹配 POST /api/v1/jobs
3. CreateJobRequest 校验 name 和 urls
4. get_job_service() 从 app.state 取得共享 JobService
5. API 将 HttpUrl 列表转换为 tuple[str, ...]
6. await service.create_job(name, urls)
7. Service 调用 await repository.create(...)
8. Repository 生成 UUID 和 UTC 时间
9. Database.transaction() 获取数据库锁并 BEGIN
10. INSERT jobs，状态写为 pending
11. executemany INSERT job_targets
12. transaction() COMMIT 并释放锁
13. Repository 构造并返回 pending Job 领域对象
14. Service 执行 await worker.enqueue(job.id) # 就是这里！
15. Queue.put(job_id)，未完成项计数 +1
16. Service 返回原来的 pending Job
17. API 用 JobResponse.from_domain(job) 转换响应模型
18. FastAPI 序列化并返回 HTTP 201
```

最重要的可靠性顺序是：

```text
SQLite COMMIT
  -> Queue.put(job_id)
  -> HTTP 201
```

因此客户端收到 201 时，任务至少已经持久化并完成入队调用。

==对14. 这里的疑问做记录：==

在`main.py`函数中：

```py
worker = JobWorker(repository)

service = JobService(
    repository,
    worker.enqueue,
)
```

再看`JobService`构造函数：

```py
def __init__(
    self,
    repository: JobRepository,
    enqueue_job: Callable[[UUID], Awaitable[None]],
) -> None:
    self._repository = repository
    self._enqueue_job = enqueue_job
```

当执行：

```py
JobService(repository, worker.enqueue)
```

对应关系：

```
repository  <- repository
enqueue_job <- worker.enqueue
```

所以：

```
self._enqueue_job = worker.enqueue
```

**此后 `self._enqueue_job` 就是一个可以调用的方法。**

完整过程：

```
# 其实就是这一步没搞懂
self._enqueue_job
  -> 指向 worker.enqueue

self._enqueue_job(job.id)
  -> 调用 worker.enqueue(job.id)
  -> 得到一个协程对象

await
  -> 执行这个协程

enqueue() 内部
  -> await worker._queue.put(job.id)
  -> job.id 进入 Queue

enqueue() 结束
  -> 返回 None
```



### 9.3 Worker 的后台链路

Worker Task 独立执行：

```text
1. await queue.get() 取得 job_id
2. 调用 await _claim_job(job_id)
3. Repository.claim_pending(job_id)
4. Database.transaction() 获取锁并 BEGIN
5. 执行条件 UPDATE：pending -> running
6. rowcount == 1，认领成功
7. COMMIT 并释放数据库锁
8. 记录 job_claimed 日志
9. finally 调用 queue.task_done()，未完成项计数 -1
10. 回到 while True，再次等待 queue.get()
```

详细解析：

> 程序并没有神秘地自动跳到数据库

==`self._repository.claim_pending()` 执行的是谁？==

当前正在执行的是 `JobWorker` 的方法，所以这里的 `self` 是 Worker：

```
self
# JobWorker 对象
```

构造 Worker 时已经执行：

```
self._repository = repository
```

而传入的 `repository` 是：

```
JobRepository(database)
```

所以：

```
self._repository.claim_pending(job_id)
```

实际上就是：

```
repository.claim_pending(job_id)
```

继续展开，相当于：

```
JobRepository.claim_pending(
    repository,
    job_id,
)
```

因此程序进入 `repositories/jobs.py`：

对象引用关系：

```
worker._repository
        |
        v
JobRepository 实例
        |
        | repository._database
        v
Database 实例
        |
        | database._connection
        v
aiosqlite.Connection
        |
        v
SQLite 文件
```

方法调用关系：

```
worker._claim_job()
        |
        v
repository.claim_pending()
        |
        v
database.transaction()
        |
        v
connection.execute("BEGIN")
        |
        v
connection.execute("UPDATE jobs ...")
        |
        v
connection.commit()
```

完整后台链路：

```
POST Service
  |
  | await worker.enqueue(job.id)
  v
asyncio.Queue.put(job.id)
  |
  | 唤醒等待中的 Worker Task
  v
JobWorker._run()
  |
  | job_id = await queue.get()
  v
JobWorker._claim_job(job_id)
  |
  | self._repository 是 JobRepository
  | await self._repository.claim_pending(job_id)
  v
JobRepository.claim_pending(job_id)
  |
  | self._database 是 Database
  | async with self._database.transaction()
  v
Database.transaction()
  |
  | await asyncio.Lock
  | self.connection 取得 aiosqlite.Connection
  | await connection.execute("BEGIN")
  | yield connection
  v
回到 JobRepository
  |
  | await connection.execute("UPDATE jobs ...")
  | cursor.rowcount == 1
  | await cursor.close()
  v
退出 async with，回到 Database.transaction()
  |
  | 没异常：await connection.commit()
  | 有异常：await connection.rollback()
  | 释放 Lock
  v
JobRepository 返回 True / False
  |
  v
JobWorker 记录日志
  |
  | queue.task_done()
  v
Worker 回到 queue.get() 等待下一个任务
```





本阶段到此结束。没有以下操作：

```text
没有访问 https://example.com
没有写 inspection_results
没有更新 completed_count / failed_count
没有变成 completed / partial_failed / failed
```

### 9.4 为什么 POST 返回 `pending`，查询却很快看到 `running`

POST 返回使用的是 Repository 刚创建的领域对象：

```python
Job(status=JobStatus.PENDING)
```

Worker 更新的是数据库中的另一份持久化状态：

```text
pending -> running
```

两条链路由事件循环交错调度，因此可能出现：

```text
时间 A：数据库提交 pending
时间 B：job_id 入队
时间 C：API 构造 pending 响应
时间 D：Worker 更新数据库为 running
时间 E：响应真正发送到客户端
```

也可能先发送响应，再由 Worker 更新。应用不保证 C、D、E 的绝对先后。

无论 Worker 多快，POST 函数返回的对象仍是 `pending` 创建快照；随后 GET 会重新查询数据库，通常看到 `running`。

这不是数据错误，而是异步系统常见的“命令响应快照与后台状态随后变化”。客户端应使用返回的 `job_id` 查询最新状态。

## 10. 重启恢复完整链路

假设应用退出前数据库中存在：

```text
任务 A：running
任务 B：pending
```

内存 Queue 随进程退出而消失。再次启动时：

```text
1. 连接 SQLite
2. running 全部重置为 pending
3. 查询所有 pending ID，得到 A、B
4. 将 A、B 放入新的 Queue
5. 启动新的 Worker Task
6. Worker 依次将 A、B 原子认领为 running
```

SQLite 提供“任务没有丢”的持久化事实，启动扫描弥补内存队列在重启后丢失的问题。

## 11. 优雅关闭完整链路

FastAPI 退出 lifespan 时：

```text
1. 应用停止正常接收新请求
2. await worker.stop()
3. stop() 将 None 放到队尾
4. Worker 处理哨兵之前已有的 job_id
5. Worker get() 取到 None
6. finally 调用 task_done()
7. _run() 退出并记录停止日志
8. stop() 对后台 Task 的 await 完成
9. await database.close()
```

关闭顺序表达了资源依赖：

```text
Worker 依赖 Repository
Repository 依赖 Database

所以关闭时：Worker -> Database
```

## 12. 运行与验证

运行测试：

```powershell
conda activate TEST
python -c "import sys; print(sys.executable)"
python -m pytest -q
```

启动应用：

```powershell
python -m uvicorn app.main:app --reload
```

创建任务：

```powershell
$job = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/jobs" `
  -ContentType "application/json" `
  -Body '{"name":"queue demo","urls":["https://example.com"]}'

$job
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/jobs/$($job.id)"
```

预期：

- POST 响应状态是 `pending`；
- 随后的 GET 通常是 `running`；
- `/results` 仍返回空数组；
- 日志出现 `job_worker_started` 和 `job_claimed`。

## 13. 常见误区

### 13.1 “Worker 是一个新线程”

当前不是。它是同一事件循环中的 asyncio Task。只有未来显式使用线程、进程或独立服务时，才会跨线程或进程。

### 13.2 “放入 Queue 就等于任务已执行”

`put()` 只表示进入缓冲区。Worker 完成 `get()` 和处理后，才算消费；`task_done()` 只标记 Queue 项处理完成，也不自动代表业务执行成功。

### 13.3 “`create_task()` 后函数立即完整执行”

`create_task()` 只注册并返回 Task。具体何时运行由事件循环调度。

### 13.4 “POST 返回 running 才正确”

当前设计刻意返回持久化创建时的 `pending` 快照。后台认领与响应发送并行推进，客户端通过 GET 获取最新状态。

### 13.5 “Worker 已经在巡检 URL”

当前 Worker 只把任务认领为 `running`。HTTPX、TaskGroup、Semaphore、结果保存和终态属于阶段 3。

### 13.6 “内存 Queue 可以保证任务永不丢失”

进程崩溃会清空 Queue。当前依靠 SQLite pending 状态和启动扫描恢复；跨进程可靠投递仍需要外部消息系统。

### 13.7 “可以先关闭数据库再停 Worker”

不可以。Worker 的认领操作依赖数据库，必须先让 Worker 退出。

## 14. 本阶段自测题

1. 阶段 2 相比阶段 1 增加了哪一条后台链路？
2. 当前 Worker 是线程、进程，还是 asyncio Task？
3. 为什么 lifespan 不能直接 `await worker._run()`？
4. `create_task()` 返回的 Task 为什么必须保存？
5. `put()`、`get()`、`task_done()`、`join()` 分别负责什么？
6. 为什么 `task_done()` 必须放在 `finally`？
7. `None` 哨兵怎样让 Worker 有序退出？
8. 为什么 Queue 中只放 job_id，不放完整 Job？
9. 为什么必须先提交 SQLite，再把 ID 放入 Queue？
10. `Callable[[UUID], Awaitable[None]]` 表示什么？
11. `Protocol` 给 Worker 测试带来了什么好处？
12. `claim_pending()` 为什么把状态条件写进同一条 UPDATE？
13. `cursor.rowcount == 1` 表示什么？
14. 重复入队同一个 ID 时，为什么第二次不会再次认领？
15. POST 为什么返回 `pending`，随后 GET 却可能看到 `running`？
16. 重启后内存 Queue 已丢失，任务为什么仍能恢复？
17. 为什么关闭时必须先停止 Worker，再关闭 Database？
18. 为什么异步测试应该轮询状态而不是固定 sleep？

## 15. 完成本阶段的判断标准

进入阶段 3 前，应当能够：

- 画出生产者、Queue、Worker 和 SQLite 的关系；
- 解释协程与 Task 的区别；
- 手写并解释 `put/get/task_done/join` 的配对关系；
- 逐行说明 `JobWorker._run()`；
- 解释哨兵停止和 Task 引用管理；
- 解释 `pending -> running` 条件更新为什么是原子的；
- 说清应用启动恢复和关闭顺序；
- 完整追踪一次 POST 的前台链路和后台链路；
- 明确当前 Worker 还没有访问 URL，也没有产生终态。

## 16. 下一阶段

阶段 3 会在 Worker 认领任务之后加入真实执行器：

```text
Worker 认领任务
  -> HTTPX AsyncClient
  -> TaskGroup 并发巡检多个 URL
  -> Semaphore 限制并发
  -> 逐项保存 InspectionResult
  -> 更新进度
  -> completed / partial_failed / failed
```

阶段 2 建立的是“任务如何可靠地从 API 交给后台执行者”；阶段 3 才回答“后台执行者如何受控并发地完成任务”。
