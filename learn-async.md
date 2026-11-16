# Python `asyncio` 精炼教程

> 整理自 Hucci写代码的 B 站视频：[《asyncio：为异步编程而生》](https://www.bilibili.com/video/BV157mFYEEkH/)。

## 1. 为什么使用 `asyncio`

网络请求、文件读写、数据库查询等 I/O 操作经常需要等待。同步代码会在等待期间阻塞，`asyncio` 则能让当前任务暂停，把执行权交给其他任务。

核心结论：

- `asyncio` 适合 **I/O 密集型**任务，目标是提高并发吞吐量和资源利用率。
- 它通常在单线程内通过协作式调度实现并发，不等于多线程并行。
- 它不会让一次 I/O 本身变快，而是利用原本被浪费的等待时间。
- CPU 密集型计算不能仅靠 `asyncio` 提速，通常应考虑多进程。

## 2. 三个核心概念

| 概念 | 含义 |
| --- | --- |
| 协程（Coroutine） | 可暂停、恢复的函数。用 `async def` 定义；调用后得到协程对象，函数体不会立刻执行。 |
| 任务（Task） | 协程在事件循环中的可调度包装。常用 `asyncio.create_task()` 创建。 |
| 事件循环（Event Loop） | 负责运行任务；当一个任务等待 I/O 时，切换去执行其他可运行任务。 |

三者的关系是：**协程描述工作，Task 跟踪工作，事件循环调度工作。**

```python
import asyncio


async def hello():
    return "hello"


coroutine = hello()  # 只创建协程对象，尚未执行函数体
result = asyncio.run(coroutine)  # 建立事件循环并运行协程
print(result)
```

若协程对象既没有被 `await`，也没有交给事件循环，程序会出现 `coroutine was never awaited` 警告。

## 3. 把同步代码改成异步代码

异步改造可以概括成三步：

1. 用 `async def` 定义协程函数。
2. 将需要并发执行的协程包装成 Task。
3. 用 `asyncio.run()` 启动事件循环。

```python
import asyncio
from time import perf_counter


async def fetch_url(url: str) -> str:
    print(f"开始请求：{url}")
    await asyncio.sleep(1)  # 模拟异步网络等待；不会阻塞整个线程，让当前协程暂停1s
    print("请求完成")
    return "url_content"


async def read_file(filepath: str) -> str:
    print(f"开始读取：{filepath}")
    await asyncio.sleep(1)  # 模拟异步文件等待
    print("读取完成")
    return "file_content"


async def main():
    fetch_task = asyncio.create_task(fetch_url("https://example.com"))
    read_task = asyncio.create_task(read_file("example.txt"))

    fetch_result = await fetch_task
    read_result = await read_task
    print(fetch_result, read_result)


start = perf_counter()
asyncio.run(main())
print(f"耗时：{perf_counter() - start:.2f} 秒")
```

两个任务都等待 1 秒：同步执行约需 2 秒，并发执行约需 1 秒。

注意下面的写法通常仍是顺序执行，因为第二个协程要等第一个完成后才开始：

```python
fetch_result = await fetch_url("https://example.com")
read_result = await read_file("example.txt")
```

## ==4. `await` 到底做了什么==

`await` 有三个主要作用：

1. 暂停当前协程，等待目标完成。
2. 目标尚未完成时，把执行权交回事件循环。
3. 目标完成后恢复当前协程，并取得结果或抛出异常。

`await` 只能等待可等待对象，例如协程、Task 或 Future。它不会自动把普通阻塞函数变成非阻塞函数：

```python
time.sleep(1)           # 阻塞事件循环，不应直接放在协程中
await asyncio.sleep(1)  # 异步等待，期间可以运行其他任务
```

当 await 后是一个协程对象时，它会直接执行该协程，而不是将其包装为任务。

## 5. 按完成顺序处理多个任务

`asyncio.as_completed()` 会并发执行一组可等待对象，并按完成先后提供结果；先完成的任务不必等待慢任务。

```python
async def main():
    operations = [
        fetch_url("https://example.com"),
        read_file("example.txt"),
    ]

    for operation in asyncio.as_completed(operations):
        result = await operation
        print(result)
```

如果只需要少量固定任务，显式使用 `create_task()` 更容易理解任务何时开始；如果希望结果一完成就处理，`as_completed()` 很合适。

## 6. 使用真正的异步 I/O 库

仅把函数改成 `async def` 不会自动获得异步能力，内部使用的库本身也必须支持异步。视频用 `aiohttp` 发送 HTTP 请求，用 `aiofiles` 读取文件：

```bash
pip install aiohttp aiofiles
```

```python
import asyncio

import aiofiles
import aiohttp


async def fetch_url(url: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.text()


async def read_file(filepath: str) -> str:
    async with aiofiles.open(filepath, "r", encoding="utf-8") as file:
        return await file.read()


async def main():
    operations = [
        fetch_url("https://jsonplaceholder.typicode.com/posts"),
        read_file("example.txt"),
    ]

    for operation in asyncio.as_completed(operations):
        print(await operation)


asyncio.run(main())
```

`async with` 会在操作结束或异常发生时正确关闭会话和文件。

## 7. 兼容没有异步接口的阻塞函数

当旧库只提供同步阻塞 API，且暂时无法换成异步库时，可以用 `asyncio.to_thread()` 把调用放到工作线程，避免阻塞事件循环：

```python
import asyncio
import time


def blocking_function(value: str) -> str:
    time.sleep(1)
    return value.upper()


async def main():
    result = await asyncio.to_thread(blocking_function, "hello")
    print(result)


asyncio.run(main())
```

`to_thread()` 主要用于阻塞 I/O 的兼容，不是解决 CPU 密集型计算的首选方案。

## 8. 异步并发原语

并发原语用于协调同一个事件循环中的多个 Task：

| 原语 | 作用 |
| --- | --- |
| `Lock` | 同一时刻只允许一个 Task 访问共享资源 |
| `Semaphore` | 限制同时执行某段代码的 Task 数量 |
| `Event` | 向一个或多个 Task 发送“条件已满足”的通知 |
| `Queue` | 在 Task 之间安全地传递数据 |

这些原语主要协调协程，不是 MySQL 数据库锁，也不能直接协调其他进程。

### 8.1 `asyncio.Lock`：互斥锁

**概念**

`Lock` 保证同一时刻只有一个 Task 进入临界区，适合保护共享的内存状态。

**使用格式**

```python
lock = asyncio.Lock()

async with lock:
    # 一次只允许一个 Task 执行这里的代码
    ...
```

`async with` 会自动等待锁，并在代码块结束时释放锁。

它做了三件事：

1. 获取锁
2. 执行`async with`内的代码
3. 释放锁

**简单示例**

```python
import asyncio

lock = asyncio.Lock()


async def use_printer(name: str):
    async with lock:
        print(f"{name} 开始打印")
        await asyncio.sleep(1)
        print(f"{name} 打印完成")


async def main():
    await asyncio.gather(
        use_printer("任务 A"),
        use_printer("任务 B"),
    )


asyncio.run(main())
```

两个任务可以并发启动，但只能依次使用打印机。

### 8.2 `asyncio.Semaphore`：限制并发数

**概念**

`Semaphore`（信号量） 内部维护一定数量的“通行证”。每个 Task 执行前获取一个，执行后归还一个。它适合限制 HTTP 请求数、数据库操作数等。

**使用格式**

```python
semaphore = asyncio.Semaphore(3)  # 最多允许 3 个 Task 同时进入

async with semaphore:
    await operation()
```

**简单示例**

```python
import asyncio

semaphore = asyncio.Semaphore(2)


async def request(number: int):
    async with semaphore:
        print(f"请求 {number} 开始")
        await asyncio.sleep(1)
        print(f"请求 {number} 完成")


async def main():
    await asyncio.gather(*(request(i) for i in range(5))) # 生成器 + 解包


asyncio.run(main())
```

虽然创建了 5 个请求，但同一时刻最多运行 2 个。

该代码耗时为 3 秒，输出通常如下：

```
请求 0 开始
请求 1 开始
请求 0 完成
请求 1 完成
请求 2 开始
请求 3 开始
请求 2 完成
请求 3 完成
请求 4 开始
请求 4 完成
```

### 8.3 `asyncio.Event`：事件通知

**概念**

`Event` 表示一个“未发生/已发生”的共享信号。Task 可以等待信号；调用 `set()` 后，所有等待者都会继续执行。

**原理**

`asyncio.Event` 可以理解成一个**公共信号开关**，只有两种状态：

- 未触发状态：内部标志为 False，调用 `wait()` 的协程需要等待。
- 已触发状态：内部标志为 True，等待中的协程可以继续运行。

默认情况下，Event 的内部标志为 `False`。

因此：

```py
await ready.wait()
```

**会让当前协程暂停。**

直到其他协程执行：

```py
ready.set()
```

**使用格式**

```python
event = asyncio.Event() # 创建 Event
```

| 方法             | 作用                                   | 是否需要 `await` |
| ---------------- | -------------------------------------- | ---------------- |
| `ready.wait()`   | 等待 Event 被设置为 True               | 是               |
| `ready.set()`    | 设置为 True，通知所有等待者            | 否               |
| `ready.clear()`  | 重置为 False，让后续 `wait()` 再次等待 | 否               |
| `ready.is_set()` | 检查当前标志是否为 True                | 否               |

**简单示例**

```python
import asyncio

ready = asyncio.Event()


async def worker(name: str):
    print(f"{name} 等待配置")
    await ready.wait()
    print(f"{name} 开始工作")


async def load_config():
    await asyncio.sleep(1)
    print("配置加载完成")
    ready.set()


async def main():
    await asyncio.gather(
        worker("任务 A"),
        worker("任务 B"),
        load_config(),
    )


asyncio.run(main())
```

`Event` 只负责通知，不携带业务数据；需要传递数据时通常使用 `Queue`。

该程序耗时约 1 秒，正常情况下输出为：

```
任务 A 等待配置
任务 B 等待配置
配置加载完成
任务 A 开始工作
任务 B 开始工作
```



### 8.4 `asyncio.Queue`：任务间传递数据

**概念**

`asyncio.Queue` 可以理解为一个供多个协程共享的**数据缓冲区**。

它通常采用 FIFO（First In, First Out）规则，即**先进先出**。

生产者和消费者可以独立运行：

- 生产者速度快，队列就会逐渐装满。
- 消费者速度快，队列就可能变空。
- 队列满了，生产者等待。
- 队列空了，消费者等待。

这样的机制称为**背压（Backpressure）**：当消费者处理不过来时，队列会限制生产者继续积累数据，避免无限占用内存。

**使用格式**

```python
queue = asyncio.Queue(maxsize=10)

await queue.put(item)
item = await queue.get()
queue.task_done()   # 当前数据处理完成
await queue.join()  # 等待所有数据处理完成
```

每次成功 `get()` 后都应对应一次 `task_done()`。

1. `await queue.put()`：向队列放入数据；队列满时异步等待。
2. `await queue.get()`：从队列取出数据；队列空时异步等待。
3. `queue.task_done()`：标记一个取出的数据已经处理完成。
4. `await queue.join()`：等待队列中所有已加入的数据都被标记处理完成。

**重点记住：**

1. `put()`：向队列添加数据，满了就等。
2. `get()`：从队列取出数据，空了就等。
3. `task_done()`：告诉队列，一个元素对应的工作已处理结束。
4. `join()`：等待所有入队元素都被标记处理完成。
5. `None`：在本例中是通知消费者退出的结束标记。

**简单示例**

```python
import asyncio


async def producer(queue: asyncio.Queue):
    for item in range(3):
        await queue.put(item)
        print(f"生产：{item}")
    await queue.put(None)  # 结束标记


async def consumer(queue: asyncio.Queue):
    while True:
        item = await queue.get()
        try:
            if item is None:
                return # 即使执行了return, finally仍然会执行
            print(f"消费：{item}")
            await asyncio.sleep(1)
        finally:
            queue.task_done()


async def main():
    queue = asyncio.Queue(maxsize=2)
    consumer_task = asyncio.create_task(consumer(queue)) # 创建消费者任务

    await producer(queue) # 开始执行生产者
    await queue.join()
    await consumer_task


asyncio.run(main())
```

**执行过程**

时间轴：

<img src="https://pic1.imgdb.cn/i/034wvgHuiiGoSAEU8kR7cv.png" style="zoom:50%;" align = 'left' />

按照典型执行顺序，关键状态可以总结为：

| 时间     | 生产者行为               | 消费者行为               |
| -------- | ------------------------ | ------------------------ |
| 0 秒附近 | 放入 0、1，放入 2 时等待 | 取出 0，开始处理         |
| 0 秒附近 | 放入 2，再等待放入 None  | 正在处理 0               |
| 约 1 秒  | 放入 None，生产者结束    | 0 完成，开始处理 1       |
| 约 2 秒  | 已结束                   | 1 完成，开始处理 2       |
| 约 3 秒  | 已结束                   | 2 完成，取出 None 并退出 |

下面的表格展示了典型处理过程：

| 操作            | 队列元素   | 未完成计数 |
| --------------- | ---------- | ---------- |
| 初始            | `[]`       | 0          |
| put(0)          | `[0]`      | 1          |
| put(1)          | `[0,1]`    | 2          |
| get(0)          | `[1]`      | 2          |
| put(2)          | `[1,2]`    | 3          |
| task_done(0)    | `[1,2]`    | 2          |
| get(1)          | `[2]`      | 2          |
| put(None)       | `[2,None]` | 3          |
| task_done(1)    | `[2,None]` | 2          |
| get(2)          | `[None]`   | 2          |
| task_done(2)    | `[None]`   | 1          |
| get(None)       | `[]`       | 1          |
| task_done(None) | `[]`       | 0          |

这张表揭示了一个重要概念：**队列为空，不代表所有任务都处理完成。**

**答疑解惑**

> 这段代码中，await consumer_task是最后执行的吗？它有什么用？是否可以删去？

`await consumer_task` **是** `main()` **函数中最后执行的一条语句**。

但要特别注意：

`await consumer_task` **最后执行，不代表** `consumer_task` **最后才开始运行。**

这是理解这段代码最关键的地方。

有两个作用：

- 作用 1：确认消费者协程真正结束

- 作用 2：获取消费者任务的最终结果或异常

  假设消费者返回一个值：

  ```py
  async def consumer(queue):    ...    return "消费完成"
  ```

  那么：

  ```py
  result = await consumer_task
  ```

  可以获取：

  ```
  "消费完成"
  ```

对于给出的这段特定代码，正常运行的情况下，可以删去，而且输出结果通常不会改变。

但推荐保留，因为它明确表达了：

> 不仅要等队列中的任务全部处理完成，还要确保消费者协程本身正常退出。

这样有几个好处：

- 明确保证消费者生命周期结束。
- 能接收消费者返回值。
- 能传播消费者任务中的异常。
- 如果以后给消费者添加清理代码，主程序仍然会正确等待清理完成。

### 8.5 为什么单线程也会产生竞态条件

**概念**

事件循环一般只在遇到 `await` 时切换 Task。但如果一次“读取—等待—修改”操作中间出现 `await`，其他 Task 就可能读取同一个旧值，产生竞态条件。

**问题示例**

```python
import asyncio

counter = 0


async def increment():
    global counter
    old_value = counter
    await asyncio.sleep(0)  # 此处可能切换到另一个 Task
    counter = old_value + 1


async def main():
    await asyncio.gather(increment(), increment())
    print(counter)  # 可能是 1，而不是 2


asyncio.run(main())
```

两个 Task 可能都读取到 `0`，最后又都写入 `1`。

**使用 `Lock` 修复**

```python
lock = asyncio.Lock()


async def increment():
    global counter
    async with lock:
        old_value = counter
        await asyncio.sleep(0)
        counter = old_value + 1
```

判断是否需要锁的关键，不是程序有没有多个线程，而是：**多个 Task 是否会在 `await` 前后读写同一份共享状态。** 临界区应尽量短，不要在持锁期间执行无关的慢速 I/O。

## 9. 练习：交替输出奇偶数

编写两个协程：一个输出 `0～9` 中的偶数，一个输出奇数；每次输出后异步等待 1 秒，并让两个协程并发执行。

<details>
<summary>参考答案</summary>

```python
import asyncio


async def print_even_numbers():
    for number in range(0, 10, 2):
        print(f"偶数：{number}")
        await asyncio.sleep(1)


async def print_odd_numbers():
    for number in range(1, 10, 2):
        print(f"奇数：{number}")
        await asyncio.sleep(1)


async def main():
    even_task = asyncio.create_task(print_even_numbers())
    odd_task = asyncio.create_task(print_odd_numbers())
    await even_task
    await odd_task


asyncio.run(main())
```

</details>


## 10. 常见错误速查

- 调用协程却没有 `await` 或创建 Task：协程不会执行，并出现警告。
- 在协程里调用 `time.sleep()`、同步 HTTP 请求等阻塞函数：整个事件循环都会被卡住。
- 认为连续两个 `await` 就一定并发：直接逐个 `await` 通常是顺序执行。
- 创建 Task 后不等待、不保留引用：可能丢失结果，异常也可能无人处理。
- 用 `asyncio` 处理大量 CPU 计算：事件循环仍会被计算占满。
- 把 `asyncio.Lock` 当成数据库锁：它只能协调当前事件循环中的 Task。
- `Queue.get()` 后漏掉 `task_done()`：会导致 `Queue.join()` 一直等待。

## 11. 视频章节索引

| 时间 | 内容 |
| --- | --- |
| 00:23 | `asyncio` 的意义 |
| 02:00 | 协程、任务与事件循环 |
| 05:14 | 异步代码的三个步骤 |
| 07:59 | `await` 的作用 |
| 08:38 | 自动调度多个协程与 `as_completed()` |
| 11:17 | `aiohttp` 与 `aiofiles` |
| 12:35 | `asyncio.to_thread()` |
| 14:12 | 奇偶数协程练习 |

一句话复习：**用 `async def` 描述可暂停的工作，用 Task 启动并发，用 `await` 在等待点交还控制权，再由事件循环统一调度。**
