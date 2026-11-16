"""Item：爬虫产出的数据结构，本质是一个支持属性访问的 dict。"""

from __future__ import annotations

from typing import Any


class Item(dict):
    """最小可用的 Item。

    用法与 dict 完全一致，同时支持 ``item.title`` 这种属性访问::

        item = Item(title="hello", url="https://example.com")
        item["score"] = 10
        print(item.title, item.score)
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __repr__(self) -> str:
        body = ", ".join(f"{k}={v!r}" for k, v in self.items())
        return f"Item({body})"
