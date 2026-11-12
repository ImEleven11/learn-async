# 阶段 0：可运行项目骨架

## 1. 本阶段目标

建立一个最小但结构清晰的 FastAPI 应用，确认项目可以安装、启动和测试。

本阶段只做：

- 项目依赖与测试配置；
- 环境变量配置；
- 基础日志；
- FastAPI 应用工厂；
- 异步应用生命周期；
- `/health` 健康检查；
- 配置测试和异步接口测试。

本阶段不做数据库、任务 API、队列、Worker 或并发 URL 请求。这些属于后续模块。

## 2. 文件变化

```text
.
├── app/
│   ├── api/
│   │   └── health.py       # 异步健康检查路由
│   ├── core/
│   │   ├── config.py       # 从环境变量读取配置
│   │   └── logging.py      # 日志初始化
│   └── main.py             # 应用工厂和生命周期
├── tests/
│   ├── test_health.py      # 异步 ASGI 接口测试
│   └── test_settings.py    # 配置测试
├── .gitignore
├── pyproject.toml
└── README.md
```

`create_app()` 使用应用工厂，而不是把所有初始化逻辑直接堆在模块顶层。测试可以向工厂传入专用配置，后续也方便替换数据库或外部客户端。

## 3. 调用链

### 启动链路

```text
uvicorn
  -> 导入 app.main:app
  -> create_app()
  -> Settings.from_env()
  -> configure_logging()
  -> FastAPI 启动
  -> 进入 lifespan()
  -> yield 后开始接收 HTTP 请求
```

### 请求链路

```text
GET /health
  -> ASGI Server 接收请求
  -> FastAPI 路由匹配
  -> await health_check(request)
  -> 从 request.app.state 读取 Settings
  -> 构造 HealthResponse
  -> FastAPI 序列化 JSON 响应
```

### 关闭链路

```text
收到停止信号
  -> FastAPI 停止接收新请求
  -> lifespan() 从 yield 之后继续执行
  -> finally 中记录停止日志
  -> Uvicorn 退出
```

## 4. 核心异步知识

### `async def` 不等于自动并发

`health_check()` 是协程函数。FastAPI 调用它并由事件循环执行。但本阶段的函数内部没有慢速 I/O，也不需要为了“看起来异步”而加入无意义的 `await asyncio.sleep(0)`。

后续访问数据库和 HTTP 服务时，路由会通过 `await` 等待真正的异步 I/O；等待期间事件循环才能处理其他请求。

### 事件循环由 Uvicorn 管理

普通脚本常用 `asyncio.run(main())` 创建事件循环。Web 应用中由 ASGI Server（这里是 Uvicorn）启动和管理事件循环，因此业务代码不应在路由中再次调用 `asyncio.run()`。

### `asynccontextmanager` 管理应用生命周期

`lifespan()` 中：

- `yield` 之前执行启动逻辑；
- `yield` 期间应用处理请求；
- `yield` 之后执行关闭逻辑；
- `finally` 保证正常关闭或异常退出时都尝试清理资源。

目前只有日志，后续数据库连接、HTTP Client 和 Worker 都会在这里统一创建和关闭。

### 健康检查为什么不访问外部资源

当前 `/health` 是存活检查，只证明进程和 HTTP 服务能够响应。它应该便宜、稳定，不应因数据库或第三方服务短暂故障而把当前进程误判为死亡。

以后如需判断是否可以承接流量，会单独增加就绪检查。

## 5. 运行与验证

### 安装

```powershell
conda activate TEST
python -c "import sys; print(sys.executable)"
python -m pip install -e ".[dev]"
```

本项目约定解释器为 `D:\Anaconda\envs\TEST\python.exe`。在安装或运行前先打印解释器路径，可以避免把依赖误装到 Anaconda base 环境。

### 启动

```powershell
python -m uvicorn app.main:app --reload
```

访问 <http://127.0.0.1:8000/health>，预期结果：

```json
{
  "status": "ok",
  "service": "Async Site Inspector",
  "environment": "development"
}
```

### 运行测试

```powershell
python -m pytest
```

`test_health.py` 没有启动真实网络端口，而是使用 HTTPX 的 `ASGITransport` 在进程内调用 FastAPI。测试本身仍然是异步的，并显式进入项目定义的 `lifespan()`。

## 6. 常见错误与下一阶段

### 常见错误

- 在异步路由中调用 `time.sleep()`：会阻塞事件循环，拖慢同一进程中的其他请求。
- 在路由中调用 `asyncio.run()`：当前线程已经存在运行中的事件循环，会直接报错。
- 认为函数写成 `async def` 就会自动并发：只有发生可等待操作，或者显式创建 Task，调度才会交错。
- 忘记关闭共享客户端：会产生连接和资源泄漏，因此后续资源必须放入 lifespan 管理。
- 每次请求重复创建连接池：功能可能正常，但会浪费连接建立成本。

### 下一阶段

阶段 1 将增加任务领域模型、SQLite 异步持久化，以及创建任务、查询状态、查询结果三个 API。任务仍不会执行，以便先看清 HTTP 到数据库的异步调用链。
