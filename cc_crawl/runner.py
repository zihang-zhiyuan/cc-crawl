"""任务入口：把外部任务（消息队列 / HTTP）映射到 Spider 并执行。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Mapping

from loguru import logger

from .engine import Engine
from .item import Item
from .registry import SpiderRegistry
from .stats import StatsCollector

ItemSink = Callable[[Item], Any]


async def run_task_async(
    payload: Mapping[str, Any],
    registry: SpiderRegistry,
    *,
    item_sink: ItemSink | None = None,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """执行一条任务并返回结构化结果。

    payload 约定::

        {
          "task_id": "可选，原样带回结果",
          "spider": "爬虫 name",
          "params": {"传给 Spider 构造函数": "..."},
          "settings": {"覆盖运行配置": "..."}   # 可选
        }

    ``spider`` 也兼容 ``task_code`` / ``collector_code`` / ``name``。
    执行异常不会向外抛，而是写进结果的 ``status`` / ``error``。
    """
    name = (
        payload.get("spider")
        or payload.get("task_code")
        or payload.get("collector_code")
        or payload.get("name")
    )
    if not name:
        raise ValueError("任务缺少 spider 字段")

    spider_cls = registry.get(name)
    params = dict(payload.get("params") or payload.get("kwargs") or {})
    merged_settings = dict(settings or {})
    merged_settings.update(payload.get("settings") or {})
    # 任务级执行不保留全部 Item（交给管道或 item_sink），避免大任务把内存撑爆
    merged_settings.setdefault("collect_items", False)

    started = time.perf_counter()
    engine: Engine | None = None
    error: str | None = None

    try:
        spider = spider_cls(**params)
        engine = Engine(spider, overrides=merged_settings, item_sink=item_sink)
        await engine.crawl()
    except Exception as exc:  # noqa: BLE001 - 任务失败通过结果返回
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("任务执行失败：{}", name)

    elapsed = round(time.perf_counter() - started, 3)
    stats = engine.stats.to_dict() if engine is not None else _empty_stats()
    result = {
        "task_id": payload.get("task_id"),
        "spider": name,
        "status": "failed" if error else "success",
        "error": error,
        "elapsed_seconds": elapsed,
        "items": int(stats.get("items", 0)),
        "stats": stats,
    }
    logger.info(
        "任务结束 spider={} status={} items={} elapsed={}s",
        name,
        result["status"],
        result["items"],
        elapsed,
    )
    return result


def run_task(
    payload: Mapping[str, Any],
    registry: SpiderRegistry,
    *,
    item_sink: ItemSink | None = None,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """同步入口，内部使用 asyncio.run；MQ 消费者建议直接 await run_task_async。"""
    return asyncio.run(
        run_task_async(payload, registry, item_sink=item_sink, settings=settings)
    )


def _empty_stats() -> dict[str, Any]:
    stats = StatsCollector()
    stats.start()
    stats.finish()
    return stats.to_dict()
