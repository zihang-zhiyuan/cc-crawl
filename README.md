# cc-crawl

> 仓库：https://github.com/zihang-zhiyuan/cc-crawl

基于 [curl_cffi](https://github.com/lexiforest/curl_cffi) 的轻量异步爬虫框架，API 参考 Scrapy 与 feapder。
当前已实现下载、重试、中间件、管道、请求去重与运行统计；账号调度与多账号 worker 在 crawl-kit 的 `crawl_kit.cc_crawl`。

## 特性（v0.1）

- `curl_cffi` 异步下载，支持浏览器 TLS 指纹（`impersonate`）、代理、超时、Cookie 复用
- 会话隔离：`Request(session=...)` 为不同账号/身份维护独立 Cookie 会话（账号调度在 crawl-kit）
- 下载失败自动重试（连接错误、超时、指定 HTTP 状态码），支持重试等待与全局请求间隔
- 下载中间件：`process_request` / `process_response` / `process_exception` 三个钩子
- Item Pipeline：内置 Console / JSON Lines / MongoDB / PostgreSQL / MySQL 落库
- 运行监控：请求、响应、成功/失败、状态码、重试、耗时与速率统计，支持周期日志
- 日志默认使用 loguru，可输出到控制台或文件
- `Spider` + `Request` + `Response` + `Item` 的 Scrapy 式回调模型
- 协程并发调度：`concurrency` 个 worker 共用请求队列，回调里可继续 yield 请求
- 回调支持同步函数 / 异步函数 / 生成器 / 异步生成器；返回 dict 会自动转 `Item`
- 响应提供 `.text` / `.json()` / `.css()` / `.xpath()` / `.follow()`
- 内置命令行入口，支持按文件或模块加载爬虫

## 安装

项目使用 [uv](https://docs.astral.sh/uv/) 管理虚拟环境，Python 默认 3.13（见 `.python-version`）。

```bash
cd D:/project/py-project/cc-crawl
uv sync
```

`uv sync` 会创建 `.venv` 并安装 `curl_cffi`、`parsel`、`pytest` 等依赖。

## 快速开始

新建 `examples/demo_spider.py`（仓库已自带）：

```python
from cc_crawl import Item, Request, Response, Spider

class QuotesSpider(Spider):
    name = "quotes"
    base_url = "https://quotes.toscrape.com"
    custom_settings = {"concurrency": 4, "timeout": 20}

    def __init__(self, start_page: int = 1):
        self.start_page = int(start_page)
        super().__init__()

    async def start_requests(self):
        yield Request(f"{self.base_url}/page/{self.start_page}/", callback=self.parse)

    async def parse(self, request: Request, response: Response):
        for quote in response.css("div.quote"):
            yield Item(
                text=quote.css("span.text::text").get(),
                author=quote.css("small.author::text").get(),
                tags=quote.css("div.tags a.tag::text").getall(),
            )

        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            yield response.follow(next_page, callback=self.parse)
```

`parse` 支持两种签名，框架按位置参数个数判断：`parse(self, response)`（Scrapy 风格，单参数叫什么名字都一样）或 `parse(self, request, response)`（feapder 风格）。

运行：

```bash
uv run python -m cc_crawl run examples/demo_spider.py:QuotesSpider
uv run python -m cc_crawl run examples/demo_spider.py:QuotesSpider -a start_page=2
# 或
uv run python examples/demo_spider.py
```

也可以用代码直接运行，构造参数原样传给 Spider：

```python
from cc_crawl import run_spider
items = run_spider(QuotesSpider, start_page=2, settings={"concurrency": 4})
```

### 给爬虫传参

爬虫通过 `__init__` 接收参数，框架只负责实例化，不限制参数形式：

- 代码运行：`run_spider(MySpider, keyword="python", page=2, settings={"concurrency": 4})`
- 命令行运行：`-a key=value`，可重复；值会按 JSON 解析（`2` → int、`true` → bool、`["a"]` → list），解析失败则按字符串
- 只依赖 `spider_cls` 的 `__init__` 签名，不要求继承特定参数类

初始请求统一写在 `start_requests()` 里，支持三种写法：异步生成器（`async def` + `yield Request`）、同步函数返回列表、`async def` 直接 `return` 列表。框架不提供 `start_urls`，起始地址由爬虫自己决定。`start_requests()` / `parse()` 里 yield 的协程（比如 async 辅助函数返回 `Request`）会自动 await。

## 核心对象

| 对象 | 说明 |
| --- | --- |
| `Spider` | 爬虫基类，定义 `name`、`start_requests()`、`parse()`、`custom_settings` |
| `Request` | 一次请求：url / method / headers / params / data / json / meta / priority / callback 等 |
| `Response` | 下载结果：`status_code` / `text` / `content` / `headers` / `json()` / `css()` / `xpath()` / `follow()` |
| `Item` | dict 子类，支持属性访问 |
| `Settings` | 运行配置，Spider 的 `custom_settings` 会覆盖默认值（键名大小写不敏感） |
| `Engine` | 调度引擎：并发、下载、回调、收集 Item、统计 |
| `StatsCollector` | 运行统计：请求 / 响应 / 成功失败 / 状态码 / 重试 / 耗时 |
| `DownloaderMiddleware` | 下载中间件基类：请求、响应、异常三个钩子 |

当前版本的几个行为约定：

- HTTP 4xx/5xx 不抛异常；命中 `retry_status_codes` 的会先重试，重试耗尽后把最后一次响应交给回调
- 连接失败、超时等下载错误按 `retry_times` 重试；全部失败后记入 `stats.errors` 并跳过该请求
- `request_interval` 是全局的：所有 worker 共享一个发车闸门，并发再高也会被间隔限制实际 QPS
- 暂未实现按域名限速，按路线图后续补充
- Windows 上 curl_cffi 可能提示 Proactor 事件循环警告（会自动用选择器线程兜底）；
  如不想看到，可在入口处加 `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`

## 配置项

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `concurrency` | `8` | 并发下载数 |
| `timeout` | `30.0` | 单请求超时（秒） |
| `retry_times` | `2` | 下载失败重试次数（不含首次），`0` 表示不重试 |
| `retry_backoff` | `0.0` | 第 n 次重试前等待 `retry_backoff * n` 秒 |
| `retry_status_codes` | `(429, 500, 502, 503, 504)` | 命中这些状态码也按失败重试 |
| `request_interval` | `0.0` | 两次请求之间的最小间隔（秒），0 表示不限速 |
| `dupefilter` | `true` | 请求去重：同一方法 + URL + 参数 + 请求体 + `session` 只调度一次 |
| `dupefilter_maxsize` | `1000000` | 去重指纹最多保留条数，超出按先进先出淘汰 |
| `collect_items` | `true` | 是否在内存里保留全部 Item；`run_task_async` 默认关闭，改为逐条交给管道 / `item_sink` |
| `stats_interval` | `60.0` | 运行统计的周期日志间隔（秒），0 表示关闭 |
| `stats_format` | `"json"` | 运行统计输出格式：`json`（单行 JSON）或 `text`（方框表格） |
| `downloader_middlewares` | `[]` | 下载中间件列表：类、实例或 `"module:Class"` |
| `item_pipelines` | `[]` | Item 管道列表：类、实例、`"module:Class"` 或带 kwargs 的 dict |
| `impersonate` | `"chrome"` | curl_cffi 浏览器指纹，如 `chrome124` / `safari` |
| `headers` | `Accept-Language` | 全局默认请求头 |
| `proxy` | `None` | 代理地址，如 `http://127.0.0.1:7890` |
| `verify` | `False` | 是否校验 HTTPS 证书（默认关闭） |
| `log_level` | `"INFO"` | 日志级别 |
| `log_file` | `None` | 日志文件路径，按 10MB 切分；None 只输出控制台 |

> `verify` 默认关闭，避免自签/过期证书中断抓取；需要严格校验时用 `custom_settings={"verify": True}` 或命令行 `--verify`。

### 全局配置

全局配置对之后创建的所有爬虫生效。优先级：内置默认值 < 全局配置 < `Spider.custom_settings` < `run_spider` / CLI 参数。

```python
import cc_crawl

# 程序启动时调用一次
cc_crawl.configure(
    retry_times=3,          # 全局重试次数
    retry_backoff=0.5,      # 重试前等待
    request_interval=1.0,   # 每次请求之间的间隔（睡眠时间）
    concurrency=4,
)
```

也可以用配置文件。TOML（`cc-crawl.toml`）：

```toml
[cc-crawl]
retry_times = 3
retry_backoff = 0.5
request_interval = 1.0
concurrency = 4
```

或 Python 模块（`settings.py`，大写变量）：

```python
RETRY_TIMES = 3
RETRY_BACKOFF = 0.5
REQUEST_INTERVAL = 1.0
CONCURRENCY = 4
```

加载方式：

```bash
# 环境变量：进程启动时自动加载
CC_CRAWL_SETTINGS=cc-crawl.toml uv run python -m cc_crawl run examples/demo_spider.py:QuotesSpider

# 或命令行指定
uv run python -m cc_crawl run examples/demo_spider.py:QuotesSpider --settings cc-crawl.toml
```

代码里也可以显式调用 `cc_crawl.load_settings("cc-crawl.toml")` / `cc_crawl.load_settings("settings")`（模块名）。

### 配置优先级

运行配置（并发、超时、重试、代理、校验等）从高到低：

1. `run_spider(settings=...)` / CLI 参数
2. `Spider.custom_settings`
3. `cc_crawl.configure(...)` / 配置文件
4. 框架内置默认值

单个请求可以在 `Request` 上覆盖部分字段，优先级高于上面的配置：

```python
yield Request(
    url,
    timeout=5,                         # 覆盖全局 timeout
    proxy="http://127.0.0.1:7890",     # 覆盖全局 proxy
    verify=True,                       # 覆盖全局 verify
    impersonate="safari",              # 覆盖全局指纹
    headers={"Referer": "..."},        # 与全局 headers 合并，同名以请求为准
)
```

`cookies` 由请求参数和会话 Cookie 共同组成。重试次数、请求间隔是全局配置，目前不支持按请求单独设置。

下载中间件在请求发出前执行，所以中间件里改写的 request 最终生效；多个中间件按配置顺序执行。

### 请求优先级

`Request(priority=...)` 数值越大越先下载，默认 `0`；相同优先级按入队顺序 FIFO。优先级只对队列中等待的请求生效，已经被 worker 取走的请求不会被抢占。

```python
class DemoSpider(Spider):
    name = "demo"

    async def parse_list(self, request, response):
        # 列表翻页优先，详情页随后
        next_page = response.css("a.next::attr(href)").get()
        if next_page:
            yield response.follow(next_page, callback=self.parse_list, priority=100)
        for href in response.css("a.detail::attr(href)").getall():
            yield response.follow(href, callback=self.parse_detail, priority=10)
```

### 重试与请求间隔

```python
class DemoSpider(Spider):
    name = "demo"
    custom_settings = {
        "retry_times": 3,        # 失败后最多再试 3 次
        "retry_backoff": 1.0,    # 第 n 次重试前等待 1 * n 秒
        "request_interval": 0.5, # 所有 worker 共享，两次请求至少间隔 0.5 秒
    }
```

连接错误、超时以及命中 `retry_status_codes` 的响应都会触发重试；连接错误重试耗尽后该请求计为失败并跳过，HTTP 状态码重试耗尽后则把最后一次响应交给回调处理。每次重试都会记入 `engine.stats.retries`。

## 请求去重

默认开启请求去重：方法 + URL + 参数 + 请求体 + `session` 相同的请求只会调度一次。
列表页里重复出现的详情链接、翻页回退等情况不会重复抓取。

```python
# 同一个 URL 需要再抓一次时，单独跳过去重
yield Request(url, callback=self.parse, dont_filter=True)
```

- 指纹由 `Request.fingerprint()` 生成；`callback` / `meta` 不参与（与 Scrapy 一致）
- `session` 参与指纹：同一个 URL 用不同账号/会话抓取不算重复
- 关闭全局去重：`custom_settings = {"dupefilter": False}` 或命令行 `--no-dedupe`
- 去重集合有上限（`dupefilter_maxsize`），超出按先进先出淘汰，适合超大规模抓取
- 被过滤的数量记在 `stats.duplicates`，统计 JSON 的 `duplicates` 字段

## 会话隔离

同一个爬虫里如果需要区分身份（多个账号、多份 Cookie），给请求设置 `session` 键即可。
下载器会为每个键维护独立的 `AsyncSession`（独立 Cookie 罐与连接），互不串号：

```python
yield Request("https://example.com/list", session="1001", callback=self.parse)
```

- `session` 为 `None`（默认）时所有请求共用一个会话
- `response.follow(...)` / `Request.replace(...)` 会保留 `session`
- 会话按 key 复用，登录后写入的 Cookie 会在同 key 的后续请求里继续生效

切换账号、账号 QPS、在途上限、任务槽位这些调度逻辑不属于框架，公共实现放在 crawl-kit：
`crawl_kit.cc_crawl`（见 crawl-kit 的 `docs/cc-crawl调度与启动.md`）。

## 运行监控

每次运行都会自动统计，默认以单行 JSON 通过 loguru 输出（方便日志采集）：

```json
{"event":"crawler_finished","spider":"quotes","elapsed_seconds":1.234,"requests":10,"duplicates":2,"attempts":12,"responses":10,"success":9,"failure":1,"success_rate":90.0,"status_counts":{"200":9,"503":1},"retries":2,"errors":0,"items":42,"avg_latency_seconds":0.12,"max_latency_seconds":0.31,"responses_per_minute":487.8,"items_per_minute":2048.8}
```

周期日志同样输出 JSON（`event=crawler_stats`），间隔由 `stats_interval` 控制。

字段说明：

| 字段 | 含义 |
| --- | --- |
| `event` | 日志类型：`crawler_finished`（结束报告）或 `crawler_stats`（周期报告） |
| `spider` | 爬虫的 `name` |
| `elapsed_seconds` | 从开始爬取到当前的运行时长（秒） |
| `requests` | 进入调度队列的请求总数，含 `start_requests` 和回调里新产出的请求 |
| `duplicates` | 被请求去重过滤掉的请求数（不进入调度队列） |
| `attempts` | 实际发起下载的次数，含失败重试；`attempts - requests` 即重试次数 |
| `responses` | 最终拿到并进入回调的响应数（重试成功只算一次） |
| `success` | 状态码为 2xx 的响应数 |
| `failure` | 失败数 = 重试耗尽的下载异常 + 非 2xx 响应 |
| `success_rate` | `success / responses`，百分比 |
| `status_counts` | 最终响应的状态码分布，如 `{"200": 28}` |
| `retries` | 触发重试的次数（连接错误或命中 `retry_status_codes`） |
| `errors` | 重试耗尽后仍失败的请求数（只统计下载异常，不含 HTTP 4xx/5xx） |
| `items` | 通过管道后保留的 Item 数 |
| `items_dropped` | 被管道丢弃或管道异常导致丢弃的 Item 数 |
| `pipeline_errors` | 管道 `process_item` 抛异常的次数 |
| `validation_failed` | `validate` 返回 `False` 被丢弃的请求数 |
| `failed_requests` | `failed_request` 最终放弃的请求数（返回新 Request 的不算） |
| `parse_errors` | 解析回调抛异常的次数（含重试后成功的） |
| `spider_retries` | 解析 / 校验失败触发的请求级重试次数 |
| `avg_latency_seconds` | 最终响应的平均下载耗时（不含排队、中间件和回调时间） |
| `max_latency_seconds` | 单次响应最长下载耗时 |
| `responses_per_minute` | 响应速率（按运行时长折算） |
| `items_per_minute` | Item 产出速率 |

- `stats_format` 设为 `"text"` 时输出方框表格；命令行用 `--stats-format text`
- 代码里可读 `engine.stats.to_dict()` / `engine.stats.to_json()`，或直接用属性：`requests` / `responses` / `success_count` / `failure_count` / `status_counts` / `item_count` / `retries` / `errors` / `avg_latency` / `duration`
- `engine.stats` 是 Scrapy 风格的 `StatsCollector`，也可以用 `inc_value` / `max_value` / `get_value` 自定义指标

## 下载中间件

继承 `DownloaderMiddleware`，按需重写三个钩子，同步或 async 方法都可以：

```python
from cc_crawl.middlewares import DownloaderMiddleware

class HeaderMiddleware(DownloaderMiddleware):
    async def process_request(self, request, spider):
        request.headers["User-Agent"] = "cc-crawl/0.1"

class DemoSpider(Spider):
    name = "demo"
    downloader_middlewares = [HeaderMiddleware]
```

执行顺序与返回值约定参考 Scrapy：

- 请求阶段按列表正序执行，响应/异常阶段逆序执行
- `process_request` 返回 `None` 继续；返回 `Request` 替换请求并重新走中间件链；返回 `Response` 直接短路，不再下载
- `process_response` 返回 `Response` 继续，返回 `Request` 重新调度
- `process_exception` 返回 `None` 继续抛错，返回 `Request` / `Response` 可从下载失败中恢复

除了 `downloader_middlewares` 类属性，也可以在代码里用下面几种方式设置：

```python
# 1. custom_settings（键名大小写不敏感，兼容 DOWNLOADER_MIDDLEWARES 等写法）
class DemoSpider(Spider):
    custom_settings = {"DOWNLOADER_MIDDLEWARES": [HeaderMiddleware]}

# 2. run_spider 的 settings 覆盖
run_spider(DemoSpider, settings={"downloader_middlewares": [HeaderMiddleware]})

# 3. 运行时给 Engine 追加（追加到列表末尾）
engine = Engine(DemoSpider())
engine.add_middleware(HeaderMiddleware)
items = asyncio.run(engine.crawl())
```

支持 `downloader_middlewares`、`download_middlewares`、`download_middleware`、`download_middle` 以及 Scrapy 风格的 `DOWNLOADER_MIDDLEWARES`。

### 爬虫内的下载钩子（feapder 风格）

不想单独写中间件类时，可以直接在 Spider 里实现 `download_midware`：

```python
class DemoSpider(Spider):
    name = "demo"

    def download_midware(self, request):
        request.headers["Referer"] = "https://example.com/"
        return request  # 返回 None 表示不修改
```

同步、异步写法都支持，会在每个请求进入下载中间件链之前调用。

### 响应校验与失败钩子（feapder 风格）

```python
class DemoSpider(Spider):
    name = "demo"

    async def start_requests(self):
        yield Request("https://example.com/list", callback=self.parse)

    def validate(self, response):
        """解析前校验响应；True/None 继续，False 丢弃，抛异常按失败重试。"""
        if "请登录" in response.text:
            return False
        if response.status_code == 403:
            raise RuntimeError("被封禁")
        return True

    async def parse(self, response):
        yield Item(...)

    async def failed_request(self, response, exception):
        """请求最终失败时调用；返回 Request 表示改投，返回 None 表示放弃。"""
        self.logger.warning("失败：{} {}", getattr(response, "url", None), exception)
        return None

    async def end_callback(self):
        """整轮爬取结束（队列清空后）调用一次，适合最终上报。"""
        ...
```

行为约定：

- `validate(response)`：在解析回调之前执行，参数和解析回调一样只有 `response`。返回
  `True` / `None` 进入解析；返回 `False` 丢弃该请求并调用 `failed_request`；
  抛异常按可重试失败处理（最多 `retry_times` 次）。
- `failed_request(response, exception)`：在 `validate` 返回 `False`、下载重试耗尽、
  解析回调异常且重试耗尽时调用。返回 `Request` 会重新入队（可用于换 URL / 换代理）；
  返回 `None` 则放弃，计入 `failed_requests`。
- 解析回调异常和 `validate` 异常会自动重试（次数受 `retry_times` 限制，记录在
  `request.meta["_cc_attempt"]`）；下载失败由下载器自己重试，不会重复。
- 回调重试时，回调在抛异常前已经 yield 的 Item 不会回滚，业务侧按需幂等。
- `end_callback()`：队列清空、worker 停止后调用一次，在 `closed()` 之前；同步/异步都支持。
- 一个请求都没成功（`success == 0` 且有失败）时，`run_task_async` 的 `status` 为 `failed`。

## Item Pipeline

处理抓到的 Item：清洗、校验、去重、落库。继承 `ItemPipeline`，按 `item_pipelines` 顺序执行；任一管道返回 `None` 就丢弃该 Item。

```python
from cc_crawl import ItemPipeline

class CleanPipeline(ItemPipeline):
    async def open_spider(self, spider):
        ...  # 建连接、打开文件

    async def process_item(self, item, spider):
        item["title"] = item.get("title", "").strip()
        return item  # 返回 None 表示丢弃

    async def close_spider(self, spider):
        ...  # 关闭连接、刷盘

class DemoSpider(Spider):
    name = "demo"
    item_pipelines = [CleanPipeline]
```

也可以写在 `custom_settings = {"item_pipelines": [...]}`、`run_spider(..., settings={"pipelines": [...]})`，或用 dict 形式带参数（方便配置文件）：

```python
item_pipelines = [
    {"class": "cc_crawl.pipelines:JsonLinesPipeline", "kwargs": {"path": "out/items.jsonl"}},
]
```

内置管道：

| 管道 | 说明 | 依赖 |
| --- | --- | --- |
| `ConsolePipeline()` | 以 JSON 打到日志 | 无 |
| `JsonLinesPipeline(path)` | 逐行追加写 `.jsonl` | 无 |
| `MongoPipeline(uri, database, collection)` | 写入 MongoDB 集合 | `pymongo>=4.9` 或 `motor` |
| `PostgresPipeline(dsn, table)` | 写入 PostgreSQL JSONB 表 | `asyncpg` |
| `MysqlPipeline(host, port, user, password, database, table)` | 写入 MySQL JSON 表 | `aiomysql` |

```bash
uv sync --extra mongo      # 或 postgres / mysql / db（三个都装）
```

数据库管道首次启动会自动建表：`id / spider / item(JSON/JSONB) / created_at`，表名可用 `table=` 指定。Mongo 直接写入 `{...item}` 文档。

每个任务都会新建并 `open_spider` / `close_spider` 一次管道。如果想让多个任务共用一个连接池（一账号一进程 + 子任务场景很常见），把 pool / client 注入进去即可，注入的句柄由调用方负责关闭，任务结束不会断开：

```python
import asyncpg
from cc_crawl.pipelines import PostgresPipeline

pool = await asyncpg.create_pool(dsn)          # 进程启动时建一次
pipeline = PostgresPipeline(pool=pool, table="cc_crawl_items")
# 同一实例放进 item_pipelines，后续任务复用；建表只在第一次执行
```

Item 经过管道后才会进入 `engine.items` 并计入 `item_scraped_count`；被丢弃或管道异常会记入 `items_dropped` / `pipeline_errors`，管道异常不会中断爬取。管道在下载 worker 内按 Item 顺序执行，数据库管道使用异步驱动，不会阻塞事件循环。

## 日志

默认使用 [loguru](https://github.com/Delgan/loguru)，无需 `import logging`。日志前缀是 `时间 | 级别 | 文件名:函数:行号 - 消息`，例如 `demo_spider.py:parse:76`。

**在爬虫里打日志**：`Spider` 自带 `self.logger`，就是 loguru 的 logger：

```python
class DemoSpider(Spider):
    name = "demo"

    async def parse(self, response):
        self.logger.info("解析 {}，状态码 {}", response.url, response.status_code)
        self.logger.warning("字段缺失")
        try:
            ...
        except ValueError:
            self.logger.exception("解析失败")  # 自动带上堆栈
        yield Item(...)
```

其它地方统一从框架导入：

```python
from cc_crawl import logger

logger.debug("调试信息")
logger.info("普通信息")   # loguru 用 {} 占位符，不是 %s
```

**配置方式**：

```python
# 1. 爬虫内
class DemoSpider(Spider):
    name = "demo"
    custom_settings = {"log_level": "DEBUG", "log_file": "logs/demo.log"}

# 2. 运行时覆盖
run_spider(DemoSpider, settings={"log_level": "DEBUG"})

# 3. 手动初始化（想在导入阶段就生效时用）
from cc_crawl import setup_logging

setup_logging("DEBUG", log_file="logs/cc_crawl.log", rotation="10 MB")
```

命令行：`--log-level DEBUG --log-file logs/cc_crawl.log`。

框架启动时只在“还没有人配置过 loguru”时自动配置，不会覆盖你先 `logger.add(...)` 的 sink；确实想重置可以 `setup_logging(..., force=True)`。需要自定义格式或输出到其它地方时，直接用 loguru 原生 API：

```python
from cc_crawl import logger

logger.add("logs/{time:YYYY-MM-DD}.log", rotation="00:00", retention="7 days", level="DEBUG")
```

## 命令行

```bash
uv run python -m cc_crawl run examples/demo_spider.py:QuotesSpider \
    --concurrency 8 --timeout 20 --impersonate chrome --log-level INFO --json \
    --retry-times 3 --retry-backoff 1 --request-interval 0.5 --no-dedupe \
    --stats-interval 30 --log-file logs/cc_crawl.log \
    -a start_page=2

# 列出一个目录/包里注册到的爬虫
uv run python -m cc_crawl list examples
```

不想联网时可以先跑离线示例（自带本地 HTTP 服务）：

```bash
uv run python examples/local_demo.py
uv run python examples/pipeline_demo.py   # 带 JSON Lines 管道，写入 output/items.jsonl
```

## 部署与任务调用

几十上百个爬虫时，推荐「注册表 + 任务级消费进程」：

```python
from cc_crawl import SpiderRegistry, run_task_async

registry = SpiderRegistry.discover("spiders")   # 启动时扫描一次

result = await run_task_async(
    {
        "task_id": "0192...",
        "spider": "quotes",             # 也兼容 task_code / collector_code
        "params": {"start_page": 2},
        "settings": {"concurrency": 8},
    },
    registry,
)
# result = {task_id, spider, status, error, elapsed_seconds, items, stats}
```

`run_task_async` 默认 `collect_items=False`：Item 通过管道落库，或在 `item_sink` 里边抓边消费，
不在内存里累积；`run_spider` 仍返回完整 Item 列表。

进程模型、RabbitMQ 消费示例、与 crawl-master-pro 任务字段的映射、超时与回滚建议，见
[docs/deployment.md](docs/deployment.md)。核心建议：**一条任务一次消费**，进程内用 `concurrency`
控制请求并发；不要用一个进程串行调用全部爬虫。

账号池、账号 QPS、任务槽位和多账号 worker 的启动入口在 crawl-kit：
`crawl_kit.cc_crawl`（`CcCrawlTaskRunner` / `CcCrawlProjectWorker`），见 crawl-kit 的
`docs/cc-crawl调度与启动.md`。

## 测试

```bash
uv run pytest
```

测试使用本地 `http.server`，不访问外网。

## 目录结构

```
cc-crawl/
├─ cc-crawl/
│  ├─ engine.py      并发调度与回调执行
│  ├─ downloader.py  curl_cffi 异步下载 / 重试 / 请求间隔 / 会话隔离
│  ├─ middlewares.py 下载中间件与执行链
│  ├─ pipelines/     Item 管道（Console / JsonLines / Mongo / Postgres / MySQL）
│  ├─ stats.py       运行监控统计（StatsCollector）
│  ├─ log.py         loguru 日志配置
│  ├─ spider.py      爬虫基类
│  ├─ registry.py    爬虫注册表（目录/包发现）
│  ├─ runner.py      任务入口（MQ / HTTP 调用）
│  ├─ request.py / response.py / item.py / settings.py
│  └─ cli.py         命令行入口
├─ docs/deployment.md  部署与消息队列调用方案
├─ examples/        demo_spider.py / local_demo.py / pipeline_demo.py
└─ tests/
```

## 路线图

- [x] 下载中间件（请求/响应/异常钩子）
- [x] 会话隔离（`Request.session`，账号调度在 crawl-kit）
- [x] 运行监控统计（周期日志 + 结束报告）
- [x] 失败重试与退避
- [x] 全局请求间隔（限速）
- [ ] 内置 UA / 代理轮换中间件
- [x] Item Pipeline（Console / JSON Lines / MongoDB / PostgreSQL / MySQL）
- [x] 请求去重与指纹（`fingerprint()` / `dont_filter`）
- [ ] 按域名限速
- [ ] 断点续爬 / 分布式队列
