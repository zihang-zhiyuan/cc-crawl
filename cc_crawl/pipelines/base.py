"""Item Pipeline 基类与执行链。"""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Iterable

from ..exceptions import CrawlxError
from ..item import Item


class ItemPipeline:
    """管道基类，按配置顺序处理 Item。

    - ``open_spider`` / ``close_spider`` 在爬虫开始/结束时调用
    - ``process_item`` 返回 Item（或 dict）继续传给下一个管道，返回 None 丢弃
    - 三个方法都可以写成普通函数或 async 函数
    """

    async def open_spider(self, spider: Any) -> None:
        """爬虫启动时调用，用于建立连接、打开文件等。"""

    async def close_spider(self, spider: Any) -> None:
        """爬虫结束时调用，用于关闭连接、刷盘等。"""

    async def process_item(self, item: Item, spider: Any) -> Item | None:
        """处理单个 Item，默认原样返回。"""
        return item


def _load_pipeline_class(entry: Any) -> type | None:
    if isinstance(entry, str):
        module_ref, separator, attr = entry.rpartition(":")
        if not separator or not module_ref or not attr:
            raise CrawlxError(f"管道格式应为 module:Class，收到 {entry!r}")
        try:
            module = importlib.import_module(module_ref)
        except ImportError as exc:
            raise CrawlxError(f"无法导入管道模块 {module_ref}: {exc}") from exc
        target = getattr(module, attr, None)
        if target is None:
            raise CrawlxError(f"模块 {module_ref} 中没有 {attr}")
        if not isinstance(target, type):
            raise CrawlxError(f"{module_ref}:{attr} 不是类")
        return target
    if isinstance(entry, type):
        return entry
    return None


def load_pipeline(entry: Any) -> Any:
    """加载一个管道：支持类、实例、"module:Class" 以及带 kwargs 的 dict。

    dict 写法便于配置文件使用::

        {"class": "cc_crawl.pipelines:MongoPipeline",
         "kwargs": {"uri": "...", "database": "db", "collection": "items"}}
    """
    if isinstance(entry, dict):
        target = entry.get("class") or entry.get("pipeline")
        if target is None:
            raise CrawlxError("item_pipelines 的 dict 项需要 class 或 pipeline 字段")
        pipeline_cls = _load_pipeline_class(target)
        if pipeline_cls is None:
            raise CrawlxError(f"无法解析管道 {target!r}")
        pipeline = pipeline_cls(**(entry.get("kwargs") or {}))
    else:
        pipeline_cls = _load_pipeline_class(entry)
        pipeline = pipeline_cls() if pipeline_cls is not None else entry

    if not hasattr(pipeline, "process_item"):
        raise CrawlxError(f"{pipeline!r} 不是有效的管道，缺少 process_item 方法")
    return pipeline


def load_pipelines(entries: Iterable[Any] | None) -> list[Any]:
    return [load_pipeline(entry) for entry in entries or []]


class PipelineManager:
    """按顺序执行管道；任一管道返回 None 即丢弃该 Item。"""

    def __init__(self, pipelines: Iterable[Any] | None = None) -> None:
        self.pipelines = list(pipelines or [])

    def add(self, *pipelines: Any) -> None:
        self.pipelines.extend(load_pipelines(pipelines))

    async def open(self, spider: Any) -> None:
        for pipeline in self.pipelines:
            await _maybe_await(pipeline.open_spider(spider))

    async def close(self, spider: Any) -> None:
        for pipeline in reversed(self.pipelines):
            await _maybe_await(pipeline.close_spider(spider))

    async def process(self, item: Item, spider: Any) -> Item | None:
        for pipeline in self.pipelines:
            result = await _maybe_await(pipeline.process_item(item, spider))
            if result is None:
                return None
            if isinstance(result, dict) and not isinstance(result, Item):
                result = Item(**result)
            if not isinstance(result, Item):
                raise CrawlxError(
                    f"{type(pipeline).__name__}.process_item 必须返回 Item、dict 或 None，"
                    f"实际是 {type(result)!r}"
                )
            item = result
        return item


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
