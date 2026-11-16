# cc-crawl 部署与被调用方式

> 面向「一个项目里有几十到几百个爬虫程序」的场景，说明进程模型、任务入口和消息队列消费方式。
> 结论先行：**不要用一个进程加载并串行调用全部爬虫**；推荐「注册表 + 任务级消费进程」。

## 1. 先看结论

| 场景 | 推荐方式 | 说明 |
| --- | --- | --- |
| 本地开发 / 单个定时任务 | `run_spider(MySpider)` 或 `python -m cc_crawl run` | 最简单，一个进程跑一个爬虫 |
| 几十个爬虫、无 MQ | 每个爬虫一个独立进程（systemd / supervisor / k8s CronJob） | 隔离好，部署稍重 |
| 几百个爬虫、由任务平台调度 | **RabbitMQ + cc-crawl worker 进程**，账号池并发 | 与 crawl-master-pro 的消息协议天然对齐 |
| 高频 / 大数据量爬虫 | 列表解析出的详情请求由账号池分给多个账号执行 | 调度在 crawl-kit，见 `crawl_kit.cc_crawl` 文档 |

一个进程「加载全部爬虫 + 循环调用」只适合本地调试或极少量轻量爬虫。爬虫数量到几十上百后，
进程内故障、内存、阻塞调用、超时和发布都会互相影响。

## 2. 为什么不建议单进程跑全部爬虫

- **故障隔离**：一个爬虫 OOM、死循环或触发风控，会拖垮同进程的所有爬虫。
- **超时与取消**：需要按任务粒度强制超时/杀死；进程级隔离最可靠，进程内容易残留协程和连接。
- **阻塞代码**：用户爬虫里可能有同步 IO、CPU 计算或重库；在同一个事件循环里会互相拖慢。
- **资源与发布**：无法单独扩缩某个热门爬虫，也不能灰度/滚动更新单个爬虫。
- **全局状态**：`curl_cffi` 会话、Cookie、代理、日志配置都是进程级；多爬虫共享时容易串号。
- **GIL**：CPU 密集或同步解析仍是单核瓶颈。

量级建议：一个 worker 进程承载一个账号池（或按爬虫程序分片），每个任务内部用 `concurrency`
控制请求并发。需要更高吞吐时加账号/进程，而不是在一个进程里堆任务。
账号调度（账号池、QPS、任务槽位、多账号 worker）见 crawl-kit 的 `docs/cc-crawl调度与启动.md`。

## 3. 推荐架构

```text
crawl-master-pro dispatcher
        │  按 cron 生成任务，写 crawl_task
        ▼
   RabbitMQ (exchange / queue)
        │  每条消息 = 一个任务
        ▼
┌───────────────────── cc-crawl worker × N ─────────────────────┐
│ 1. 启动时 SpiderRegistry.discover("spiders")                │
│ 2. 消费一条消息 → payload                                    │
│ 3. run_task_async(payload, registry)                        │
│    ├─ 按 spider name 取类                                    │
│    ├─ params 传给 Spider 构造函数                            │
│    ├─ Engine 执行：下载 → 回调 → 管道落库                    │
│    └─ 返回 {status, stats, items, error}                     │
│ 4. 回写任务状态 / 上报结果，ack 消息                          │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
 MongoDB / PostgreSQL / MySQL（Item Pipeline）
```

进程无状态：所有任务参数来自消息，所有结果写入管道或回写接口；水平扩容只需要增加副本。

## 4. 任务入口：`run_task_async`

`cc_crawl.runner` 提供与传输层无关的任务入口，MQ / HTTP / 定时器都可以直接调用：

```python
from cc_crawl import SpiderRegistry, run_task_async

registry = SpiderRegistry.discover("spiders")   # 启动时扫描一次

result = await run_task_async(
    {
        "task_id": "0192...",           # 可选，原样带回
        "spider": "quotes",             # 爬虫 name
        "params": {"start_page": 2},    # 传给 Spider 构造函数
        "settings": {"concurrency": 8}, # 可选，覆盖运行配置
    },
    registry,
)
```

返回结构：

```json
{
  "task_id": "0192...",
  "spider": "quotes",
  "status": "success",
  "error": null,
  "elapsed_seconds": 12.75,
  "items": 28,
  "stats": {
    "requests": 28, "attempts": 28, "responses": 28,
    "success": 28, "failure": 0, "success_rate": 100.0,
    "status_counts": {"200": 28}, "retries": 0, "errors": 0,
    "items": 28, "items_dropped": 0, "pipeline_errors": 0,
    "avg_latency_seconds": 1.563, "max_latency_seconds": 1.746,
    "responses_per_minute": 131.8, "items_per_minute": 131.8
  }
}
```

`spider` 字段也兼容 `task_code` / `collector_code` / `name`，方便直接对接 crawl-master-pro 的
`SpiderTaskMessage`。执行异常不会向外抛，统一返回 `status=failed` 和 `error`，消费者负责 nack/重投或回写失败。

### 与 crawl-master-pro 字段映射

| 任务消息字段 | cc-crawl 用法 |
| --- | --- |
| `task_id` | 原样带回，用于幂等和回写 |
| `task_code` / `collector_code` | 作为 `spider` 名（需与 `Spider.name` 对应） |
| `params` | 传给 Spider 构造函数 |
| `target_path` / `operate_date` | 合并进 `params`，由爬虫自己使用 |
| `acct_ids` / `tenant_id` 等 | 合并进 `params`，由爬虫决定如何使用 |

如果 collector_code 和 spider name 不一致，在消费端做一层映射即可，例如
`SPIDER_BY_COLLECTOR = {"c1234_abcd...": "quotes"}`。

## 5. RabbitMQ 消费示例（aio-pika）

```python
import json
import os

import aio_pika

from cc_crawl import SpiderRegistry, run_task_async

async def main() -> None:
    registry = SpiderRegistry.discover("spiders")

    connection = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"])
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=1)  # 一个 worker 同时只处理一条任务
    queue = await channel.declare_queue("crawl.tasks", durable=True)

    async with queue.iterator() as messages:
        async for message in messages:
            async with message.process(requeue=False):
                payload = json.loads(message.body)
                result = await run_task_async(payload, registry)
                await report_back(payload.get("task_id"), result)  # 回写 crawl_task

async def report_back(task_id, result):
    ...  # PUT /crawl/task/open/update/{key}，带 status / stats / error
```

要点：

- `prefetch_count=1`：整任务模式下每个进程一次只拿一条任务，避免任务在进程内堆积；
  账号池模式见 crawl-kit 的 `docs/cc-crawl调度与启动.md`。
- `requeue=False` + 业务回写：任务失败由平台按 `retry_*` 字段决定是否重新生成，避免消息无限重投。
- **先回写结果再 ack**：ack 之后进程崩溃会导致平台既没有结果也没有重投。
- 消费端超时用 `asyncio.wait_for(...)` 包住 `run_task_async`，超时后回写失败并 ack/nack。
- 多副本消费同一队列即可水平扩展，前提是任务本身幂等（用 `task_id` 去重）。

## 6. 爬虫注册表

```python
from cc_crawl import SpiderRegistry

registry = SpiderRegistry.discover("spiders")     # 目录、包名或单个 .py
print(registry.names)                             # 启动时自检，name 必须唯一
spider_cls = registry.get("quotes")
```

也可以显式注册：`SpiderRegistry([QuotesSpider, DetailSpider])`。

命令行自检：

```bash
uv run python -m cc_crawl list spiders
```

推荐目录：

```text
project/
├─ spiders/
│  ├─ __init__.py
│  ├─ quotes.py        # class QuotesSpider(Spider): name = "quotes"
│  ├─ detail.py
│  └─ ...
├─ common/             # 共享解析/管道
├─ settings.py         # RETRY_TIMES / MONGO_URI 等全局配置
└─ worker.py           # 上面的 MQ 消费入口
```

## 7. 任务粒度与进程模型

### 方案 A：每条任务一个进程（推荐）

- 消费进程收到消息后 `subprocess` 拉起 `python -m worker.run_task --payload ...`，父进程等待退出码。
- 优点：内存/连接/异常完全隔离，超时可以直接 `kill`，爬虫升级不影响常驻消费者。
- 缺点：进程启动有几十到几百毫秒开销；几百个爬虫场景可接受。

### 方案 B：常驻进程内逐条执行

- 一个进程消费一条任务、执行完、再消费下一条（上面的 aio-pika 示例）。
- 优点：无进程启动开销，代码简单；异常被限制在任务内。
- 风险：用户代码卡死时进程无法自愈；需要配合外部超时 + 健康检查 + 定期重启。

### 方案 C：进程内并发多条任务

- 同一进程里 `asyncio.create_task` 跑多条任务。
- 仅建议在任务很轻、且你能接受相互影响时使用；否则优先加进程副本。

## 8. 超时、重试、优雅退出

- **任务超时**：`asyncio.wait_for(run_task_async(...), timeout=task_timeout)`；超时后回写失败。
- **爬虫重试**：crawx 内部已有请求级重试（`retry_times` / `retry_backoff`）；任务级重试由调度平台决定。
- **优雅退出**：收到 SIGTERM 后停止取新消息，等当前任务结束（或超时后取消），回写结果再退出。
- **健康检查**：进程存活 + 最近一次心跳时间；卡死任务由外部 watchdog 杀掉重启。

## 9. 监控与日志

- 每个任务结束会输出一条 JSON 统计日志（`event=crawler_finished`），字段含义见 README「运行监控」。
- 建议在消费端把 `task_id` / `collector_code` 写进日志上下文，或把 `run_task_async` 的结果直接回写平台。
- 关键指标：任务成功率、平均耗时、`retries`、`errors`、`pipeline_errors`、QPS。
- 日志文件按 `log_file` 输出，容器里建议交给 stdout + 日志采集，而不是落本地磁盘。

## 10. 部署形态

**Docker / k8s Deployment（推荐）**

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
CMD ["python", "worker.py"]
```

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: cc-crawl-worker }
spec:
  replicas: 4
  template:
    spec:
      containers:
        - name: worker
          image: registry/cc-crawl-worker:1.0.0
          env:
            - name: RABBITMQ_URL
              valueFrom: { secretKeyRef: { name: cc-crawl, key: rabbitmq-url } }
          resources:
            requests: { cpu: "500m", memory: "512Mi" }
            limits:   { cpu: "2",    memory: "2Gi" }
```

每个 Pod 一个 worker 进程；扩容改 `replicas`。爬虫代码随镜像发布，滚动更新时新旧 Pod 可以并存消费。

## 11. 常见问题

**Q：一个爬虫要跑很多租户/账号，会不会为每个租户起进程？**
不会。一条任务里把租户/账号放进 `params`，爬虫内部循环调用；
需要并行时在爬虫里 `yield` 多个 `Request`，由 `concurrency` 控制。

**Q：几百个爬虫的代码怎么组织？**
按业务分子目录，`Spider.name` 全局唯一；`cc-crawl list spiders` 在 CI 里跑一遍做重名检查。

**Q：能不能一个进程只跑某一个爬虫、单独扩容？**
可以。用独立 Deployment 并让队列路由只投递对应 `collector_code` 的消息；
或者消费端按 `spider` 过滤。大多数场景先共享 worker 池，热门任务再单独拆。

**Q：任务失败会不会丢？**
`run_task_async` 返回 `status=failed`，消费者先回写平台再 ack；平台按 `retry_*` 规则重新生成任务。
消息本身不无限重投，避免毒消息堵塞队列。

---

# 账号调度（在 crawl-kit）

cc-crawl 不包含账号池、账号 QPS、任务槽位和多账号 worker。这部分作为公共调度/启动入口
放在 crawl-kit 的 `crawl_kit.cc_crawl`：

| 能力 | 入口 |
| --- | --- |
| 账号池（在途上限 / QPS / 租约回收） | `AccountPool` |
| 请求级账号分配 | `AccountMiddleware`（设置 `Request.session`） |
| 任务级 `(账号, 爬虫)` 互斥 | `SlotManager` |
| 任务执行入口 | `CcCrawlTaskRunner.run_task` / `run_envelope` |
| 多账号启动入口 | `CcCrawlProjectWorker` / `start_cc_crawl_worker` |

完整说明见 crawl-kit 仓库 `docs/cc-crawl调度与启动.md`。

框架侧只提供会话隔离：`Request(session="1001")` 为同一个键复用独立 `AsyncSession`，
不同账号的 Cookie / Token 不串号。账号怎么分配给请求、一个进程跑哪些任务，由 crawl-kit 决定。
