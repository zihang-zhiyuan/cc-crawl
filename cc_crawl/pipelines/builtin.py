"""内置管道：控制台输出、JSON Lines 文件。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from ..item import Item
from .base import ItemPipeline


class ConsolePipeline(ItemPipeline):
    """把 Item 以 JSON 打印到日志。"""

    def __init__(self, level: str = "INFO", ensure_ascii: bool = False) -> None:
        self.level = level.upper()
        self.ensure_ascii = ensure_ascii

    async def process_item(self, item: Item, spider: Any) -> Item:
        logger.log(self.level, json.dumps(item, ensure_ascii=self.ensure_ascii, default=str))
        return item


class JsonLinesPipeline(ItemPipeline):
    """把 Item 逐行追加写入 JSON Lines 文件。"""

    def __init__(
        self,
        path: str | Path,
        encoding: str = "utf-8",
        ensure_ascii: bool = False,
    ) -> None:
        self.path = Path(path)
        self.encoding = encoding
        self.ensure_ascii = ensure_ascii
        self._file: Any = None
        self._lock = asyncio.Lock()

    async def open_spider(self, spider: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding=self.encoding)
        self._lock = asyncio.Lock()

    async def process_item(self, item: Item, spider: Any) -> Item:
        line = json.dumps(item, ensure_ascii=self.ensure_ascii, default=str)
        async with self._lock:
            if self._file is not None:
                self._file.write(line + "\n")
                self._file.flush()
        return item

    async def close_spider(self, spider: Any) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
