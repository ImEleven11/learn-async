# Python 异步编程核心知识清单

只列出实际生产开发中最常用、最容易出问题的内容。后续每完成一个模块，可将对应项目从 `[ ]` 更新为 `[x]`。

## 1. 必备心智模型

- [ ] 区分同步/异步、阻塞/非阻塞。
- [ ] 区分并发与并行：asyncio 主要解决 I/O 并发，CPU 并行通常需要进程。
- [ ] 理解事件循环：一个线程在多个可等待任务之间协作调度。
- [ ] 理解 `await`：等待结果，并在对方尚未完成时把执行权交回事件循环。
- [ ] 知道异步提高的是吞吐量和资源利用率，不会让单次 I/O 本身变快。

## 2. 协程与 Task

- [ ] 会用 `async def` 定义协程函数，理解调用它只会得到协程对象。
- [ ] 会用 `asyncio.run()` 从同步入口启动异步程序。
- [ ] 会正确使用 `await`，能发现“创建协程但没有 await”的问题。
- [ ] 会用 `asyncio.create_task()` 让协程并发运行。
- [ ] 理解“直接 await”和“先 create_task 再 await”的执行时序差异。
- [ ] 会保留后台 Task 的引用，并获取其结果或异常。
- [ ] 会用 `asyncio.TaskGroup` 管理一组有共同生命周期的任务。
- [ ] 了解 `asyncio.gather()`，知道它适合按输入顺序收集多个结果。



## 3. 受控并发与队列

- [ ] 会用 `asyncio.Semaphore` 限制并发请求数。
- [ ] 知道不能一次性创建无限数量的 Task。
- [ ] 会使用 `asyncio.Queue` 实现生产者/消费者。
- [ ] 会正确配对 `get()`、`task_done()` 和 `join()`。
- [ ] 理解有界队列 `maxsize` 如何对生产者形成背压。
- [ ] 理解队列容量、Worker 数量、单任务并发度三者的关系。
- [ ] 知道进程内 `asyncio.Queue` 在重启或崩溃后会丢任务。



## 4. 超时、异常、重试与取消

- [ ] 会用 `asyncio.timeout()` 限制一段异步操作的最长时间。
- [ ] 会区分连接、读取和整体业务超时。
- [ ] 能区分可重试的暂时性错误与不应重试的永久错误。
- [ ] 会实现有限次数的异步重试和指数退避，并使用 `asyncio.sleep()`。
- [ ] 理解幂等性：重试不能产生重复副作用。
- [ ] 理解 TaskGroup 中一个子任务失败时其他子任务的取消行为。
- [ ] 会根据业务选择“整批失败”或“记录单项失败后继续”。
- [ ] 会调用 `task.cancel()`，知道取消只是请求，不是立即强制停止。
- [ ] 会在 `try/finally` 中清理资源，通常不吞掉 `CancelledError`。



## 5. 共享状态与竞态条件

- [ ] 知道单线程异步程序仍可能发生竞态条件。
- [ ] 能识别“读取共享状态 -> await -> 修改状态”的竞争窗口。
- [ ] 会用 `asyncio.Lock` 保护确实需要互斥的临界区。
- [ ] 会用 `asyncio.Event` 在 Task 之间通知状态变化。
- [ ] 优先通过队列传递消息，减少共享可变状态。



## 6. 异步资源与 I/O

- [ ] 会使用 `async with` 管理异步资源。
- [ ] 会使用 `contextlib.asynccontextmanager` 管理应用级生命周期。
- [ ] 会复用 HTTPX AsyncClient 及其连接池，而不是每次请求都新建客户端。
- [ ] 会使用异步数据库驱动，并控制连接和事务作用域。
- [ ] 不在等待外部 HTTP 时长期持有数据库事务或锁。
- [ ] 知道 `async def` 内调用同步阻塞函数仍会阻塞事件循环。
- [ ] 会用 `asyncio.to_thread()` 隔离无法替换的阻塞 I/O。



## 7. 并发与并行的选择

- [ ] I/O 密集工作优先使用 asyncio 并发。
- [ ] 阻塞 I/O 可临时放入线程，但优先选择原生异步库。
- [ ] 纯 Python CPU 密集工作不能靠 asyncio 提速。
- [ ] CPU 密集工作需要进程池或独立计算服务，同时考虑序列化和进程切换成本。
- [ ] 小型 CPU 任务通常直接执行更简单，不必为了“并行”增加额外开销。



## 8. 应用生命周期与优雅停机

- [ ] 能在应用启动时创建数据库、HTTP Client、队列和 Worker。
- [ ] 能在关闭时停止接收新任务，再处理或取消在途任务。
- [ ] 会等待已取消 Task 真正结束，避免遗留任务和资源泄漏。
- [ ] 能按顺序关闭 Worker、HTTP Client 和数据库连接。
- [ ] 理解多个 Web 进程各自有独立事件循环、内存和队列。
- [ ] 知道需要可靠执行和崩溃恢复时，应使用外部消息队列与独立 Worker。



## 9. 测试与排障

- [ ] 会用 pytest-asyncio 测试异步函数。
- [ ] 会测试成功、超时、部分失败和取消路径。
- [ ] 使用本地模拟服务或 Mock Transport，避免测试依赖不稳定公网。
- [ ] 能验证并发上限，而不只验证最终结果。
- [ ] 能检查测试或关闭后是否残留未完成 Task。
- [ ] 会开启 asyncio debug mode 排查慢回调和资源泄漏。
- [ ] 会给日志附加 `job_id`、Task 名称、耗时和错误上下文。
- [ ] 能识别 “coroutine was never awaited” 和 “Task exception was never retrieved”。



## 10. 最常用 API 速查

必须熟练：

```python
asyncio.run()
await expression
asyncio.create_task()
asyncio.TaskGroup
asyncio.gather()
asyncio.sleep()
asyncio.timeout()
asyncio.Queue
asyncio.Semaphore
asyncio.Lock
asyncio.Event
task.cancel()
asyncio.to_thread()
```



## 11. 完成项目后应能回答

1. 调用 `async def` 函数后，为什么函数体还没有执行？
2. `await coroutine()` 和 `create_task(coroutine())` 有什么区别？
3. 为什么并发度不能无限提高？
4. 为什么单线程 asyncio 仍可能产生竞态条件？
5. 超时和取消发生后，如何保证连接、锁和任务状态被正确清理？
6. 为什么异步 HTTP Client 应该复用？
7. 为什么把同步数据库调用写进 `async def` 仍会阻塞？
8. 为什么 CPU 密集任务不能只靠 asyncio 提速？
9. 应用关闭时应该按什么顺序处理队列、Worker 和共享资源？
10. 为什么 `asyncio.Queue` 不能替代生产级消息队列？

