# Async Site Inspector

一个按生产调用链逐步构建的 Python 异步编程学习项目。

当前进度：阶段 2，已包含应用骨架、任务 API、SQLite 异步持久化、内存队列和单后台 Worker。任务会从 `pending` 异步进入 `running`；真实 URL 巡检将在下一阶段实现。

## 安装

本项目固定使用 Anaconda 的 `TEST` 环境：

```powershell
conda activate TEST
python -c "import sys; print(sys.executable)"
python -m pip install -e ".[dev]"
```

解释器路径应为 `D:\Anaconda\envs\TEST\python.exe`。

## 启动

```powershell
python -m uvicorn app.main:app --reload
```

浏览器访问：

- 健康检查：<http://127.0.0.1:8000/health>
- OpenAPI 文档：<http://127.0.0.1:8000/docs>

创建任务示例：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/jobs" `
  -ContentType "application/json" `
  -Body '{"name":"local demo","urls":["https://example.com"]}'
```

## 测试

```powershell
python -m pytest
```

## 环境变量

```powershell
$env:APP_ENVIRONMENT = "development"
$env:APP_LOG_LEVEL = "INFO"
$env:APP_DATABASE_PATH = "data/async_site_inspector.db"
```
