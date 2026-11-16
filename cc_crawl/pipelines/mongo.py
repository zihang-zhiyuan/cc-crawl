"""MongoDB 管道。"""

from __future__ import annotations

import inspect
from typing import Any

from ..item import Item
from .base import ItemPipeline


def _import_mongo_client() -> Any:
    """优先使用 pymongo 4.9+ 的异步客户端，退回 motor。"""
    try:
        from pymongo import AsyncMongoClient

        return AsyncMongoClient
    except ImportError:
        pass
    try:
        from motor.motor_asyncio import AsyncIOMotorClient

        return AsyncIOMotorClient
    except ImportError as exc:  # pragma: no cover - 取决于安装环境
        raise ImportError(
            "MongoPipeline 需要 pymongo>=4.9 或 motor：pip install 'cc-crawl[mongo]'"
        ) from exc


class MongoPipeline(ItemPipeline):
    """把 Item 写入 MongoDB 集合。

    用法::

        item_pipelines = [
            MongoPipeline("mongodb://localhost:27017", database="cc_crawl", collection="items")
        ]
    """

    def __init__(
        self,
        uri: str = "mongodb://localhost:27017",
        database: str = "cc_crawl",
        collection: str = "items",
        client: Any = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.uri = uri
        self.database = database
        self.collection = collection
        self.client_kwargs = client_kwargs or {}
        self._client = client
        self._owns_client = client is None
        self._collection: Any = None

    async def open_spider(self, spider: Any) -> None:
        if self._client is None:
            self._client = _import_mongo_client()(self.uri, **self.client_kwargs)
        self._collection = self._client[self.database][self.collection]

    async def process_item(self, item: Item, spider: Any) -> Item:
        await self._collection.insert_one(dict(item))
        return item

    async def close_spider(self, spider: Any) -> None:
        # 外部传入的 client 由调用方负责关闭（进程级共享时不随任务结束而断开）
        if self._client is None or not self._owns_client:
            return
        result = self._client.close()
        if inspect.isawaitable(result):
            await result
        self._client = None
        self._collection = None
