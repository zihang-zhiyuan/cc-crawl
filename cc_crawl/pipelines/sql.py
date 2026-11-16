"""SQL 管道：PostgreSQL / MySQL。"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

from ..item import Item
from .base import ItemPipeline

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_table(table: str) -> str:
    if not _TABLE_RE.match(table):
        raise ValueError(f"非法表名：{table!r}，只允许字母、数字和下划线")
    return table


def _item_json(item: Item) -> str:
    return json.dumps(dict(item), ensure_ascii=False, default=str)


class PostgresPipeline(ItemPipeline):
    """把 Item 写入 PostgreSQL 的 JSONB 表。

    表结构（首次启动自动创建）：``id / spider / item JSONB / created_at``。
    依赖 asyncpg：``pip install 'cc-crawl[postgres]'``。
    """

    def __init__(
        self,
        dsn: str | None = None,
        table: str = "cc_crawl_items",
        pool: Any = None,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        self.dsn = dsn
        self.table = _validate_table(table)
        self.min_size = min_size
        self.max_size = max_size
        self._pool = pool

    async def open_spider(self, spider: Any) -> None:
        if self._pool is None:
            try:
                import asyncpg
            except ImportError as exc:  # pragma: no cover - 取决于安装环境
                raise ImportError(
                    "PostgresPipeline 需要 asyncpg：pip install 'cc-crawl[postgres]'"
                ) from exc
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn, min_size=self.min_size, max_size=self.max_size
            )
        async with self._pool.acquire() as connection:
            await connection.execute(self._create_sql())

    async def process_item(self, item: Item, spider: Any) -> Item:
        async with self._pool.acquire() as connection:
            await connection.execute(
                self._insert_sql(), spider.name, _item_json(item)
            )
        return item

    async def close_spider(self, spider: Any) -> None:
        if self._pool is None:
            return
        result = self._pool.close()
        if inspect.isawaitable(result):
            await result
        self._pool = None

    def _create_sql(self) -> str:
        return (
            f"CREATE TABLE IF NOT EXISTS {self.table} ("
            "id BIGSERIAL PRIMARY KEY, "
            "spider TEXT NOT NULL, "
            "item JSONB NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )

    def _insert_sql(self) -> str:
        return f"INSERT INTO {self.table} (spider, item) VALUES ($1, $2::jsonb)"


class MysqlPipeline(ItemPipeline):
    """把 Item 写入 MySQL 的 JSON 表（MySQL 5.7+）。

    表结构（首次启动自动创建）：``id / spider / item JSON / created_at``。
    依赖 aiomysql：``pip install 'cc-crawl[mysql]'``。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "cc_crawl",
        table: str = "cc_crawl_items",
        pool: Any = None,
        min_size: int = 1,
        max_size: int = 10,
        charset: str = "utf8mb4",
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.table = _validate_table(table)
        self.min_size = min_size
        self.max_size = max_size
        self.charset = charset
        self._pool = pool

    async def open_spider(self, spider: Any) -> None:
        if self._pool is None:
            try:
                import aiomysql
            except ImportError as exc:  # pragma: no cover - 取决于安装环境
                raise ImportError(
                    "MysqlPipeline 需要 aiomysql：pip install 'cc-crawl[mysql]'"
                ) from exc
            self._pool = await aiomysql.create_pool(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                db=self.database,
                charset=self.charset,
                autocommit=True,
                minsize=self.min_size,
                maxsize=self.max_size,
            )
        async with self._pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(self._create_sql())

    async def process_item(self, item: Item, spider: Any) -> Item:
        async with self._pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    self._insert_sql(), (spider.name, _item_json(item))
                )
        return item

    async def close_spider(self, spider: Any) -> None:
        if self._pool is None:
            return
        self._pool.close()
        wait_closed = getattr(self._pool, "wait_closed", None)
        if wait_closed is not None:
            result = wait_closed()
            if inspect.isawaitable(result):
                await result
        self._pool = None

    def _create_sql(self) -> str:
        return (
            f"CREATE TABLE IF NOT EXISTS `{self.table}` ("
            "id BIGINT NOT NULL AUTO_INCREMENT, "
            "spider VARCHAR(255) NOT NULL, "
            "item JSON NOT NULL, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )

    def _insert_sql(self) -> str:
        return f"INSERT INTO `{self.table}` (spider, item) VALUES (%s, %s)"
