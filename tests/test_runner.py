"""Spider 注册表与任务入口测试。"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

import pytest

from cc_crawl import Item, Spider, SpiderRegistry, run_task
from cc_crawl.exceptions import SpiderNotFound

SPIDER_TEMPLATE = '''
from cc_crawl import Spider

class {cls}(Spider):
    name = "{name}"

    async def start_requests(self):
        return []

    async def parse(self, request, response):
        yield None
'''


def write_spider(root: Path, filename: str, cls: str, name: str) -> None:
    root.joinpath(filename).write_text(
        textwrap.dedent(SPIDER_TEMPLATE.format(cls=cls, name=name)),
        encoding="utf-8",
    )


def test_registry_discovers_directory():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_spider(root, "a.py", "ASpider", "a")
        write_spider(root, "b.py", "BSpider", "b")

        registry = SpiderRegistry.discover(root)

    assert registry.names == ["a", "b"]
    assert registry.get("a").__name__ == "ASpider"
    assert registry.get("b").__name__ == "BSpider"


def test_registry_rejects_duplicate_names():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_spider(root, "a.py", "ASpider", "dup")
        write_spider(root, "b.py", "BSpider", "dup")

        with pytest.raises(ValueError):
            SpiderRegistry.discover(root)


def test_registry_get_unknown_raises():
    registry = SpiderRegistry()

    with pytest.raises(SpiderNotFound):
        registry.get("missing")


class EchoSpider(Spider):
    name = "echo"

    def __init__(self, keyword: str = "") -> None:
        if keyword != "hi":
            raise ValueError(f"keyword={keyword!r}")
        self.keyword = keyword
        super().__init__()

    async def start_requests(self):
        return []

    async def parse(self, request, response):
        yield Item(keyword=self.keyword)


def test_run_task_executes_with_params():
    registry = SpiderRegistry([EchoSpider])

    result = run_task(
        {
            "task_id": "t1",
            "spider": "echo",
            "params": {"keyword": "hi"},
            "settings": {"stats_interval": 0},
        },
        registry,
    )

    assert result["task_id"] == "t1"
    assert result["spider"] == "echo"
    assert result["status"] == "success"
    assert result["error"] is None
    assert result["items"] == 0
    assert result["stats"]["requests"] == 0


def test_run_task_accepts_task_code_and_kwargs():
    registry = SpiderRegistry([EchoSpider])

    result = run_task(
        {"task_code": "echo", "kwargs": {"keyword": "hi"}, "settings": {"stats_interval": 0}},
        registry,
    )

    assert result["status"] == "success"


def test_run_task_returns_failed_result_on_error():
    registry = SpiderRegistry([EchoSpider])

    result = run_task(
        {"spider": "echo", "params": {"keyword": "bad"}, "settings": {"stats_interval": 0}},
        registry,
    )

    assert result["status"] == "failed"
    assert "ValueError" in result["error"]
