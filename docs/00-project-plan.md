# 异步学习项目精炼计划

## 1. 项目目标

项目名称：**Async Site Inspector（异步站点巡检平台）**。

用户提交一批 URL，API 创建巡检任务并立即返回任务 ID；后台 Worker 并发访问这些 URL，记录响应状态和耗时；用户随后查询任务进度与结果。

项目只学习生产中最常见的异步能力：

- `async` / `await` 与事件循环；
- `create_task()`、`TaskGroup` 与并发任务；
- `Queue`、`Semaphore` 与背压；
- 超时、重试、异常隔离与取消；
- 异步 HTTP、数据库和资源生命周期；
- 优雅停机、测试与日志；
- I/O 并发和 CPU 并行的边界。

业务保持简单，默认全部在本机运行，不使用付费服务。

## 2. 最终业务能力

只实现四个核心接口：

```http
POST /api/v1/jobs                  # 提交一批 URL，返回 job_id
GET  /api/v1/jobs/{job_id}         # 查询状态和进度
GET  /api/v1/jobs/{job_id}/results # 查询每个 URL 的结果
POST /api/v1/jobs/{job_id}/cancel  # 取消未完成任务
```

项目还会提供本地模拟 URL，用于稳定制造正常响应、慢响应、错误和重试成功等情况，不依赖公网。

## 3. 核心调用链

```text
POST /jobs
  -> API 参数校验
  -> JobService 创建任务
  -> SQLite 保存 pending 状态
  -> asyncio.Queue 放入 job_id
  -> Worker 消费任务
  -> TaskGroup 并发巡检多个 URL
  -> Semaphore 限制并发
  -> HTTPX 发起异步请求
  -> timeout / retry 处理故障
  -> SQLite 保存结果和进度
  -> GET API 返回任务状态与结果
```

这条链路体现三个生产原则：

1. API 不等待耗时任务执行完，只快速返回任务 ID。
2. 并发必须受控，不能无限创建 Task 或请求下游。
3. 单个 URL 失败可以记录为部分失败，不应让整批结果丢失。

## 4. 技术选择

- Python 3.11+；
- `asyncio`：异步运行时；
- FastAPI + Uvicorn：异步 API；
- HTTPX：异步 HTTP 请求；
- SQLite + `aiosqlite`：低成本持久化；
- pytest + pytest-asyncio：测试。

暂不引入 Redis、Celery、Kafka、PostgreSQL 和 Docker 编排。它们会增加环境成本，并分散对 `asyncio` 核心机制的注意力。最终文档会说明内存队列与真实分布式任务系统的差异。

## 5. 项目结构

```text
learn-async/
├── app/
│   ├── api/             # HTTP 路由
│   ├── core/            # 配置、日志、生命周期
│   ├── domain/          # 数据模型和任务状态
│   ├── repositories/    # SQLite 访问
│   ├── services/        # 业务编排
│   ├── workers/         # 队列和后台任务
│   ├── clients/         # HTTPX 客户端
│   └── main.py
├── tests/
├── docs/
├── pyproject.toml
└── README.md
```

## 6. 精炼实施顺序

每次只完成一个阶段。该阶段代码通过验证后，再进入下一阶段，并在 `docs/` 新增一份独立说明文档。

### 阶段 0：可运行项目骨架

实现依赖配置、FastAPI 入口、健康检查、基础配置和日志。

核心知识：同步入口与异步路由、事件循环、应用生命周期。

对应文档：`docs/02-project-bootstrap.md`。

### 阶段 1：任务 API 与持久化

定义任务状态和数据模型，初始化 SQLite，实现创建任务、查询状态和查询结果。此时任务只入库，不执行。

核心知识：异步函数调用链、`async with`、数据库连接和事务边界。

对应文档：`docs/03-job-api-and-storage.md`。

### 阶段 2：队列与后台 Worker

加入 `asyncio.Queue`；应用启动时创建 Worker；任务能够从 `pending` 进入 `running`，再进入终态。

核心知识：`create_task()`、生产者/消费者、`put()`、`get()`、`task_done()`、Task 生命周期。

对应文档：`docs/04-queue-and-worker.md`。

### 阶段 3：受控并发巡检

接入 HTTPX，使用 `TaskGroup` 并发巡检多个 URL，用 `Semaphore` 限制并发，并逐项保存结果。

核心知识：协程、Task、`await`、结构化并发、连接池、并发限制。

对应文档：`docs/05-concurrent-inspection.md`。

### 阶段 4：生产级故障处理

增加分层超时、有限重试、异步退避、错误分类、部分失败和有界队列。

核心知识：`asyncio.timeout()`、异常传播、重试幂等性、背压、禁止无限并发。

对应文档：`docs/06-resilience-and-backpressure.md`。

### 阶段 5：取消与优雅停机

实现取消接口和进度更新；应用停止时不再接收新任务，正确处理 Worker、在途 Task、HTTP Client 和数据库资源。

核心知识：`CancelledError`、取消传播、`try/finally`、竞态条件、关闭顺序。

对应文档：`docs/07-cancellation-and-shutdown.md`。

### 阶段 6：测试、观测与并行边界

补齐关键异步测试、结构化日志和串行/并发耗时实验；用一个小实验说明阻塞函数、`to_thread()` 和进程池分别适合什么场景。

核心知识：异步测试、Task 泄漏、上下文日志、I/O 并发与 CPU 并行。

对应文档：`docs/08-testing-observability-and-parallelism.md`。

## 7. 每阶段文档固定内容

每份阶段文档只保留六部分：

1. 本阶段目标；
2. 文件变化；
3. 调用链；
4. 核心异步知识；
5. 运行与验证方法；
6. 常见错误和下一阶段衔接。

## 8. 实施约束

- 不提前实现后续阶段，保留清晰的演进过程。
- 每阶段结束时项目都必须可运行。
- 每个异步工具都要说明“解决什么问题”和“不使用会怎样”。
- 通过日志、测试或计时实验证明异步行为。
- 优先使用 Python 标准能力，不为展示概念引入复杂框架。
- 教学版本使用单进程内存队列；明确说明它不能提供跨进程和崩溃恢复能力。

