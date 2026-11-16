"""引擎：并发调度、下载、回调解析、结果收集与运行统计。"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import json
from collections import deque
from typing import Any, AsyncIterator, Mapping

from loguru import logger

from .downloader import Downloader
from .exceptions import DownloadError, ValidationFailed
from .item import Item
from .log import setup_logging
from .middlewares import DownloaderMiddlewareManager, load_middlewares
from .pipelines import PipelineManager, load_pipelines
from .request import Request
from .response import Response
from .settings import Settings
from .spider import Spider
from .stats import StatsCollector


class Engine:
    """爬取引擎。

    ``concurrency`` 个 worker 从同一个队列取请求，下载后执行回调；
    回调中 yield 的 Request 会重新入队，yield 的 Item 会被收集，直到队列清空。

    运行期间由 ``StatsCollector`` 记录请求、响应、成功/失败、重试、Item 与耗时；
    ``stats_interval`` 秒输出一次周期日志，结束时输出完整统计。
    """

    def __init__(
        self,
        spider: Spider,
        overrides: Mapping[str, Any] | None = None,
        *,
        item_sink: Any | None = None,
    ) -> None:
        self.spider = spider
        self.settings: Settings = spider.settings.merged(overrides or {})
        self.stats = StatsCollector()
        self.downloader = Downloader(self.settings, stats=self.stats)
        self.middleware_manager = DownloaderMiddlewareManager(
            load_middlewares(self.settings.downloader_middlewares)
        )
        self.pipeline_manager = PipelineManager(
            load_pipelines(self.settings.item_pipelines)
        )
        self.items: list[Item] = []
        self._item_sink = item_sink
        self._collect_items = bool(self.settings.collect_items)
        # 优先级高的先出队；counter 保证同优先级按入队顺序 FIFO
        self._queue: asyncio.PriorityQueue[tuple[int, int, Request]] = asyncio.PriorityQueue()
        self._request_counter = itertools.count()
        self._workers: list[asyncio.Task[None]] = []
        self._stats_task: asyncio.Task[None] | None = None
        # 请求去重指纹（先进先出淘汰，避免无界增长）
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()

    # 解析 / 校验失败重试次数记录在请求 meta 上
    ATTEMPT_KEY = "_cc_attempt"

    async def crawl(self) -> list[Item]:
        """执行爬取，返回收集到的全部 Item。"""
        setup_logging(self.settings.log_level, self.settings.log_file)
        self.stats.start()
        logger.info(
            "启动爬虫 {}（并发 {}，超时 {}s，重试 {} 次，请求间隔 {}s）",
            self.spider.name,
            self.settings.concurrency,
            self.settings.timeout,
            self.settings.retry_times,
            self.settings.request_interval,
        )
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"cc_crawl-worker-{index}")
            for index in range(self.settings.concurrency)
        ]
        self._stats_task = self._start_stats_task()
        try:
            await self.downloader.open()
            await self.pipeline_manager.open(self.spider)
            async for request in _aiter(self.spider.start_requests()):
                self._enqueue(request)
            await self._queue.join()
        finally:
            await self._stop_stats_task()
            for task in self._workers:
                task.cancel()
            results = await asyncio.gather(*self._workers, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    logger.error("worker 异常退出: {!r}", result)
            self._workers.clear()
            try:
                await _maybe_await(self.spider.end_callback())
            except Exception:  # noqa: BLE001 - 收尾钩子失败不应掩盖主流程结果
                logger.exception("spider.end_callback() 执行失败")
            try:
                await _maybe_await(self.spider.closed())
            except Exception:  # noqa: BLE001 - 收尾钩子失败不应掩盖主流程结果
                logger.exception("spider.closed() 执行失败")
            try:
                await self.pipeline_manager.close(self.spider)
            except Exception:  # noqa: BLE001
                logger.exception("管道关闭失败")
            await self.downloader.close()
            self.stats.finish()
            self._log_final_stats()

        return self.items

    def add_middleware(self, *middlewares: Any) -> None:
        """运行前追加下载中间件，接受类、实例或 ``module:Class`` 字符串。"""
        self.middleware_manager.add(*middlewares)

    # ------------------------------------------------------------------ 统计
    def _start_stats_task(self) -> asyncio.Task[None] | None:
        interval = self.settings.stats_interval
        if interval <= 0:
            return None
        return asyncio.create_task(self._stats_loop(interval), name="cc_crawl-stats")

    async def _stats_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            self._log_periodic_stats()

    async def _stop_stats_task(self) -> None:
        if self._stats_task is None:
            return
        self._stats_task.cancel()
        await asyncio.gather(self._stats_task, return_exceptions=True)
        self._stats_task = None

    def _log_periodic_stats(self) -> None:
        if self.settings.stats_format == "json":
            logger.info("{}", self._stats_json("crawler_stats"))
        else:
            logger.info("运行统计：{}", self.stats.brief())

    def _log_final_stats(self) -> None:
        if self.settings.stats_format == "json":
            logger.info("{}", self._stats_json("crawler_finished"))
        else:
            logger.info("爬虫 {} 结束\n{}", self.spider.name, self.stats.summary())

    def _stats_json(self, event: str) -> str:
        payload = {"event": event, "spider": self.spider.name, **self.stats.to_dict()}
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # ------------------------------------------------------------------ 调度
    def _enqueue(self, request: Request) -> None:
        if not isinstance(request, Request):
            raise TypeError(
                f"期望 Request，实际是 {type(request)!r}；"
                "如果辅助函数是 async 定义的，请 await 它，或改成普通 def 返回 Request"
            )
        if self.settings.dupefilter and not request.dont_filter:
            fingerprint = request.fingerprint()
            if fingerprint in self._seen:
                self.stats.inc_value("dupefilter/filtered")
                return
            self._seen.add(fingerprint)
            self._seen_order.append(fingerprint)
            while len(self._seen) > self.settings.dupefilter_maxsize:
                expired = self._seen_order.popleft()
                self._seen.discard(expired)
        self.stats.inc_value("crawler/request_count")
        self._queue.put_nowait((-request.priority, next(self._request_counter), request))

    async def _worker(self, _index: int) -> None:
        while True:
            _, _, request = await self._queue.get()
            try:
                await self._handle(request)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 单个请求失败不能拖垮 worker
                logger.exception("处理请求失败: {}", request.url)
            finally:
                self._queue.task_done()

    async def _handle(self, request: Request) -> None:
        try:
            response = await self._download(request)
        except DownloadError as exc:
            logger.error("{}", exc)
            await self._failed(request, None, exc)
            return

        try:
            verdict = await _maybe_await(self._call_hook(self.spider.validate, request, response))
        except Exception as exc:  # noqa: BLE001 - 校验异常按可重试失败处理
            self.stats.inc_value("spider/validate_error_count")
            logger.warning("validate 执行失败 {}: {!r}", request.url, exc)
            if not self._retry(request):
                await self._failed(request, response, exc)
            return

        if verdict is False:
            self.stats.inc_value("spider/validation_failed")
            await self._failed(
                request,
                response,
                ValidationFailed(request.url),
            )
            return

        self._record_response(response)
        if not response.ok:
            logger.warning("HTTP {} {}", response.status_code, response.url)

        callback = request.callback or self.spider.parse
        try:
            async for result in _iter_callback(callback, request, response):
                if isinstance(result, Request):
                    self._enqueue(result)
                elif isinstance(result, Item):
                    await self._process_item(result)
                else:
                    logger.warning("忽略回调返回的未知类型: {!r}", type(result))
        except Exception as exc:  # noqa: BLE001 - 解析异常按可重试失败处理
            self.stats.inc_value("spider/parse_error_count")
            logger.exception("解析失败 {}", request.url)
            if not self._retry(request):
                await self._failed(request, response, exc)

    def _call_hook(self, callback: Any, request: Request, response: Response) -> Any:
        """validate 只按参数个数传参：``validate(response)`` 或 ``validate(request, response)``。"""
        if _positional_params(callback) >= 2:
            return callback(request, response)
        return callback(response)

    def _call_failed_hook(
        self,
        request: Request,
        response: Response | None,
        exception: BaseException,
    ) -> Any:
        """failed_request 支持 (response, exception) / (response) / 旧版三参数。"""
        callback = self.spider.failed_request
        count = _positional_params(callback)
        if count >= 3:
            return callback(request, response, exception)
        if count == 2:
            return callback(response, exception)
        if count == 1:
            return callback(response)
        return callback()

    def _retry(self, request: Request) -> bool:
        """解析 / 校验类失败重试（下载失败由下载器自己重试，不重复）。"""
        attempt = int(request.meta.get(self.ATTEMPT_KEY, 0))
        if attempt >= self.settings.retry_times:
            return False
        self.stats.inc_value("spider/retry_count")
        retried = request.replace(
            meta={**request.meta, self.ATTEMPT_KEY: attempt + 1},
            dont_filter=True,
        )
        self._enqueue(retried)
        return True

    async def _failed(
        self,
        request: Request,
        response: Response | None,
        exception: BaseException,
    ) -> None:
        """请求最终失败：交给 ``failed_request``，返回 Request 则改投。"""
        try:
            replacement = await _maybe_await(
                self._call_failed_hook(request, response, exception)
            )
        except Exception:  # noqa: BLE001 - 失败钩子异常不能拖垮 worker
            logger.exception("failed_request 执行失败 {}", request.url)
            replacement = None
        if isinstance(replacement, Request):
            self._enqueue(replacement)
            return
        self.stats.inc_value("spider/failed_requests")

    async def _process_item(self, item: Item) -> None:
        try:
            processed = await self.pipeline_manager.process(item, self.spider)
        except Exception:  # noqa: BLE001 - 单个 Item 失败不影响其它请求
            self.stats.inc_value("pipeline/error_count")
            self.stats.inc_value("item_dropped_count")
            logger.exception("管道处理 Item 失败")
            return
        if processed is None:
            self.stats.inc_value("item_dropped_count")
            return
        self.stats.inc_value("item_scraped_count")
        if self._collect_items:
            self.items.append(processed)
        if self._item_sink is not None:
            try:
                await _maybe_await(self._item_sink(processed))
            except Exception:  # noqa: BLE001 - 下游消费失败不应中断爬取
                self.stats.inc_value("item_sink/error_count")
                logger.exception("Item 下游消费失败")

    async def _download(self, request: Request) -> Response:
        """执行下载中间件与下载器，直到拿到一个最终 Response。"""
        modified = await _maybe_await(self.spider.download_midware(request))
        if isinstance(modified, Request):
            request = modified
        elif modified is not None:
            raise TypeError(
                f"download_midware 必须返回 Request 或 None，实际是 {type(modified)!r}"
            )

        while True:
            result = await self.middleware_manager.process_request(request, self.spider)
            if isinstance(result, Response):
                return result
            if isinstance(result, Request):
                request = result
                continue

            try:
                response = await self.downloader.fetch(request)
            except DownloadError as exc:
                recovered = await self.middleware_manager.process_exception(
                    request, exc, self.spider
                )
                if isinstance(recovered, Response):
                    return recovered
                if isinstance(recovered, Request):
                    request = recovered
                    continue
                raise

            processed = await self.middleware_manager.process_response(
                request, response, self.spider
            )
            if isinstance(processed, Request):
                request = processed
                continue
            return processed

    def _record_response(self, response: Response) -> None:
        self.stats.inc_value("downloader/response_count")
        self.stats.inc_value(f"downloader/response_status_count/{response.status_code}")


async def _aiter(value: Any) -> AsyncIterator[Any]:
    """同时支持异步可迭代对象、可等待对象和普通可迭代对象。"""
    if inspect.isawaitable(value):
        value = await value
    if isinstance(value, Request):
        yield value
        return
    if value is None:
        raise TypeError("start_requests 没有返回任何请求")
    if hasattr(value, "__aiter__"):
        async for element in value:
            element = await _maybe_await(element)
            if element is not None:
                yield element
        return
    if isinstance(value, (str, bytes, dict)):
        raise TypeError(f"start_requests 必须返回 Request 序列，实际是 {type(value)!r}")
    for element in value:
        element = await _maybe_await(element)
        if element is not None:
            yield element


async def _iter_callback(
    callback: Any, request: Request, response: Response
) -> AsyncIterator[Any]:
    """兼容同步函数、异步函数、生成器、异步生成器等回调写法。

    回调签名同时支持 feapder 风格 ``parse(request, response)`` 与
    Scrapy 风格 ``parse(response)``，按参数个数自动判断。
    """
    if _accepts_request(callback):
        result = callback(request, response)
    else:
        result = callback(response)
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return
    if isinstance(result, (Request, Item)):
        yield result
        return
    if isinstance(result, dict):
        yield Item(**result)
        return
    if hasattr(result, "__aiter__"):
        async for element in result:
            element = await _maybe_await(element)
            if element is not None:
                yield _to_item(element)
        return
    if hasattr(result, "__iter__"):
        for element in result:
            element = await _maybe_await(element)
            if element is not None:
                yield _to_item(element)
        return
    yield result


def _accepts_request(callback: Any) -> bool:
    """按位置参数个数判断回调风格（参考 Scrapy）。

    - 0/1 个位置参数：``callback(response)``（Scrapy 风格，参数名叫什么不影响）
    - 2 个及以上：``callback(request, response)``（feapder 风格）
    """
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return False

    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional) >= 2


def _to_item(element: Any) -> Any:
    """回调里直接 yield 的 dict 自动转成 Item。"""
    if isinstance(element, dict) and not isinstance(element, Item):
        return Item(**element)
    return element


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def crawl(
    spider: Spider,
    overrides: Mapping[str, Any] | None = None,
) -> list[Item]:
    """以协程方式运行一个 spider 实例。"""
    return await Engine(spider, overrides=overrides).crawl()


def run_spider(
    spider_cls: type[Spider],
    *spider_args: Any,
    settings: Mapping[str, Any] | None = None,
    **spider_kwargs: Any,
) -> list[Item]:
    """同步入口：按传入参数创建 spider 并运行到结束，返回 Item 列表。

    ``spider_args`` / ``spider_kwargs`` 原样传给 Spider 构造函数，
    运行配置通过 ``settings`` 覆盖，例如::

        run_spider(MySpider, keyword="python", settings={"concurrency": 4})
    """
    spider = spider_cls(*spider_args, **spider_kwargs)
    return asyncio.run(crawl(spider, overrides=settings))


def _positional_params(callback: Any) -> int:
    """统计回调接受的位置参数个数（绑定方法不算 self；*args 视为很多）。"""
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return 2
    count = 0
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return 99
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            count += 1
    return count
