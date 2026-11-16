"""Item Pipeline 测试：执行链、内置管道与数据库管道（fake 驱动）。"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from cc_crawl import Item, ItemPipeline, Spider
from cc_crawl.exceptions import CrawlxError
from cc_crawl.pipelines import (
    JsonLinesPipeline,
    MongoPipeline,
    MysqlPipeline,
    PipelineManager,
    PostgresPipeline,
    load_pipeline,
)


class NamedSpider(Spider):
    name = "pipeline-test"

    async def start_requests(self):
        return []

    async def parse(self, request, response):
        yield Item()


class AddField(ItemPipeline):
    async def process_item(self, item, spider):
        item["name"] = str(item.get("name", "")).upper()
        return item


class DropBad(ItemPipeline):
    async def process_item(self, item, spider):
        return None if item.get("bad") else item


def test_pipeline_manager_runs_in_order_and_drops():
    spider = NamedSpider()

    async def run():
        manager = PipelineManager([AddField(), DropBad()])
        await manager.open(spider)
        kept = await manager.process(Item(name="abc"), spider)
        dropped = await manager.process(Item(name="x", bad=True), spider)
        await manager.close(spider)
        return kept, dropped

    kept, dropped = asyncio.run(run())

    assert kept["name"] == "ABC"
    assert dropped is None


class Recording(ItemPipeline):
    def __init__(self, name: str) -> None:
        self.name = name
        self.events: list[str] = []

    async def open_spider(self, spider):
        self.events.append(f"open:{self.name}")

    async def close_spider(self, spider):
        self.events.append(f"close:{self.name}")

    async def process_item(self, item, spider):
        self.events.append(f"process:{self.name}")
        return item


def test_pipeline_order_open_process_close():
    spider = NamedSpider()
    first, second = Recording("a"), Recording("b")

    async def run():
        manager = PipelineManager([first, second])
        await manager.open(spider)
        await manager.process(Item(), spider)
        await manager.close(spider)

    asyncio.run(run())

    assert first.events == ["open:a", "process:a", "close:a"]
    assert second.events == ["open:b", "process:b", "close:b"]


def test_load_pipeline_supports_dict_with_kwargs():
    pipeline = load_pipeline(
        {"class": JsonLinesPipeline, "kwargs": {"path": "out/items.jsonl"}}
    )

    assert isinstance(pipeline, JsonLinesPipeline)
    assert pipeline.path == Path("out/items.jsonl")


def test_load_pipeline_rejects_invalid_object():
    with pytest.raises(CrawlxError):
        load_pipeline(object())


def test_jsonlines_pipeline_writes_file():
    spider = NamedSpider()

    async def run(path: Path):
        pipeline = JsonLinesPipeline(path)
        await pipeline.open_spider(spider)
        await pipeline.process_item(Item(a=1), spider)
        await pipeline.process_item(Item(b="中文"), spider)
        await pipeline.close_spider(spider)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "items.jsonl"
        asyncio.run(run(path))
        lines = path.read_text(encoding="utf-8").splitlines()

    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"b": "中文"}


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def insert_one(self, doc: dict) -> None:
        self.docs.append(doc)


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


class FakeMongoClient:
    def __init__(self) -> None:
        self.databases: dict[str, FakeDatabase] = {}
        self.closed = False

    def __getitem__(self, name: str) -> FakeDatabase:
        return self.databases.setdefault(name, FakeDatabase())

    async def close(self) -> None:
        self.closed = True


def test_mongo_pipeline_inserts_and_closes():
    spider = NamedSpider()
    client = FakeMongoClient()

    async def run():
        pipeline = MongoPipeline(database="db", collection="items", client=client)
        await pipeline.open_spider(spider)
        await pipeline.process_item(Item(a=1), spider)
        await pipeline.close_spider(spider)

    asyncio.run(run())

    assert client.databases["db"].collections["items"].docs == [{"a": 1}]
    assert client.closed is True


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc_info):
        return False


class FakePgConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args):
        self.calls.append((sql, args))
        return "OK"


class FakePgPool:
    def __init__(self) -> None:
        self.connection = FakePgConnection()
        self.closed = False

    def acquire(self):
        return AsyncContext(self.connection)

    async def close(self) -> None:
        self.closed = True


def test_postgres_pipeline_creates_table_and_inserts():
    spider = NamedSpider()
    pool = FakePgPool()

    async def run():
        pipeline = PostgresPipeline(table="items", pool=pool)
        await pipeline.open_spider(spider)
        await pipeline.process_item(Item(name="x"), spider)
        await pipeline.close_spider(spider)

    asyncio.run(run())

    create_sql, _ = pool.connection.calls[0]
    insert_sql, args = pool.connection.calls[1]
    assert "CREATE TABLE IF NOT EXISTS items" in create_sql
    assert "INSERT INTO items" in insert_sql
    assert args == ("pipeline-test", '{"name": "x"}')
    assert pool.closed is True


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def execute(self, sql: str, args=None) -> None:
        self.calls.append((sql, args))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeMySqlConnection:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj


class FakeMySqlPool:
    def __init__(self) -> None:
        self.connection = FakeMySqlConnection()
        self.closed = False

    def acquire(self):
        return AsyncContext(self.connection)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def test_mysql_pipeline_creates_table_and_inserts():
    spider = NamedSpider()
    pool = FakeMySqlPool()

    async def run():
        pipeline = MysqlPipeline(table="items", pool=pool)
        await pipeline.open_spider(spider)
        await pipeline.process_item(Item(name="x"), spider)
        await pipeline.close_spider(spider)

    asyncio.run(run())

    create_sql, _ = pool.connection.cursor_obj.calls[0]
    insert_sql, args = pool.connection.cursor_obj.calls[1]
    assert "CREATE TABLE IF NOT EXISTS `items`" in create_sql
    assert "INSERT INTO `items`" in insert_sql
    assert args == ("pipeline-test", '{"name": "x"}')
    assert pool.closed is True
