"""cc-crawl：基于 curl_cffi 的轻量异步爬虫框架。

示例::

    from cc_crawl import Item, Request, Response, Spider, run_spider

    class DemoSpider(Spider):
        name = "demo"

        def __init__(self, keyword: str = "demo") -> None:
            self.keyword = keyword
            super().__init__()

        async def start_requests(self):
            yield Request("https://example.com", callback=self.parse)

        async def parse(self, request: Request, response: Response):
            yield Item(title=response.css("title::text").get(), keyword=self.keyword)

    items = run_spider(DemoSpider, keyword="python", settings={"concurrency": 4})
"""

from .engine import Engine, crawl, run_spider
from .exceptions import CrawlxError, DownloadError, SpiderNotFound
from .item import Item
from .log import logger, setup_logging
from .middlewares import DownloaderMiddleware, DownloaderMiddlewareManager
from .pipelines import ItemPipeline, PipelineManager
from .registry import SpiderRegistry
from .request import Request
from .response import Response
from .runner import run_task, run_task_async
from .settings import Settings, configure, global_settings, load_settings
from .spider import Spider
from .stats import StatsCollector

__version__ = "0.1.0"

# 兼容旧的统计对象名字
Stats = StatsCollector

__all__ = [
    "CrawlxError",
    "DownloadError",
    "DownloaderMiddleware",
    "DownloaderMiddlewareManager",
    "Engine",
    "Item",
    "ItemPipeline",
    "PipelineManager",
    "Request",
    "Response",
    "Settings",
    "Spider",
    "SpiderNotFound",
    "SpiderRegistry",
    "Stats",
    "StatsCollector",
    "configure",
    "crawl",
    "global_settings",
    "load_settings",
    "logger",
    "run_spider",
    "run_task",
    "run_task_async",
    "setup_logging",
    "__version__",
]
