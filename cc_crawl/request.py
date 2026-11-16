"""Request：描述一次待抓取的请求。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit


@dataclass
class Request:
    """一个待抓取的请求。

    ``callback`` 为该请求下载完成后调用的解析函数，签名 ``async def callback(self, response)``。
    ``meta`` 用于在请求之间透传自定义数据。
    """

    url: str
    method: str = "GET"
    callback: Callable[..., Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] | None = None
    params: dict[str, Any] | None = None
    data: Any = None
    json: Any = None
    meta: dict[str, Any] = field(default_factory=dict)
    # 调度优先级：数值越大越先下载，默认 0；相同优先级按入队顺序执行
    priority: int = 0
    # 指定响应解码方式，默认交给 curl_cffi 按响应头/内容推断
    encoding: str | None = None
    timeout: float | None = None
    impersonate: str | None = None
    proxy: str | None = None
    verify: bool | None = None
    allow_redirects: bool = True
    # 会话隔离键：同一个键共用一个 AsyncSession（Cookie/连接），None 为默认会话。
    # 账号调度由外部调度层（如 crawl-kit）决定，框架只负责按 key 隔离会话。
    session: str | None = None
    # 跳过请求去重（同一个 URL 需要重复抓取时使用）
    dont_filter: bool = False

    def __post_init__(self) -> None:
        self.priority = int(self.priority)

    def fingerprint(self) -> str:
        """去重指纹：方法 + URL + 参数 + 请求体 + 会话键。

        ``session`` 参与指纹：同一个 URL 用不同账号/会话抓取不算重复；
        ``callback`` / ``meta`` 不参与，与 Scrapy 一致。
        """
        parts = [
            self.method.upper(),
            urlsplit(self.url)._replace(fragment="").geturl(),
            self.session or "",
        ]
        if self.params:
            items: list[tuple[str, str]] = []
            for key in sorted(self.params):
                value = self.params[key]
                if isinstance(value, (list, tuple)):
                    items.extend((key, str(item)) for item in value)
                else:
                    items.append((key, str(value)))
            parts.append(urlencode(items))
        body = self.data if self.data is not None else self.json
        if body is not None:
            if isinstance(body, (str, bytes)):
                parts.append(str(body))
            else:
                parts.append(
                    json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
                )
        return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()

    def replace(self, **kwargs: Any) -> "Request":
        """基于当前请求生成一个新请求（不修改原对象）。"""
        return replace(self, **kwargs)

    def __repr__(self) -> str:
        callback = getattr(self.callback, "__name__", None) or self.callback
        return f"<{self.method} {self.url} callback={callback}>"
