"""下载中间件：Scrapy 风格的请求 / 响应 / 异常钩子。"""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Iterable

from .exceptions import CrawlxError
from .request import Request
from .response import Response


class DownloaderMiddleware:
    """下载中间件基类，按需重写三个钩子。

    返回约定与 Scrapy 一致：

    - ``process_request`` 返回 ``None`` 继续，返回 ``Request`` 替换请求并重新走中间件链，
      返回 ``Response`` 直接短路（不再下载）
    - ``process_response`` 返回 ``Response`` 继续，返回 ``Request`` 重新调度
    - ``process_exception`` 返回 ``None`` 继续抛错，返回 ``Request`` / ``Response`` 恢复
    """

    async def process_request(self, request: Request, spider: Any) -> Request | Response | None:
        return None

    async def process_response(
        self, request: Request, response: Response, spider: Any
    ) -> Request | Response:
        return response

    async def process_exception(
        self, request: Request, exception: Exception, spider: Any
    ) -> Request | Response | None:
        return None


def load_middleware(entry: Any) -> Any:
    """加载一个中间件：支持实例、类，以及 ``module:Class`` 字符串。"""
    if isinstance(entry, str):
        module_ref, separator, attr = entry.rpartition(":")
        if not separator or not module_ref or not attr:
            raise CrawlxError(f"中间件格式应为 module:Class，收到 {entry!r}")
        try:
            module = importlib.import_module(module_ref)
        except ImportError as exc:
            raise CrawlxError(f"无法导入中间件模块 {module_ref}: {exc}") from exc
        entry = getattr(module, attr, None)
        if entry is None:
            raise CrawlxError(f"模块 {module_ref} 中没有 {attr}")
    return entry() if isinstance(entry, type) else entry


def load_middlewares(entries: Iterable[Any] | None) -> list[Any]:
    return [load_middleware(entry) for entry in entries or []]


class DownloaderMiddlewareManager:
    """按配置顺序执行中间件。

    请求阶段正序，响应与异常阶段逆序（与 Scrapy 一致）。
    """

    def __init__(self, middlewares: Iterable[Any] | None = None) -> None:
        self.middlewares = list(middlewares or [])

    def add(self, *middlewares: Any) -> None:
        """追加中间件，接受类、实例或 ``module:Class`` 字符串。"""
        self.middlewares.extend(load_middlewares(middlewares))

    async def process_request(self, request: Request, spider: Any) -> Request | Response | None:
        for middleware in self.middlewares:
            result = await _maybe_await(middleware.process_request(request, spider))
            if result is None:
                continue
            if not isinstance(result, (Request, Response)):
                raise CrawlxError(
                    f"{type(middleware).__name__}.process_request 必须返回 Request、Response 或 None，"
                    f"实际是 {type(result)!r}"
                )
            return result
        return None

    async def process_response(
        self, request: Request, response: Response, spider: Any
    ) -> Request | Response:
        for middleware in reversed(self.middlewares):
            result = await _maybe_await(
                middleware.process_response(request, response, spider)
            )
            if isinstance(result, Request):
                return result
            if not isinstance(result, Response):
                raise CrawlxError(
                    f"{type(middleware).__name__}.process_response 必须返回 Response 或 Request，"
                    f"实际是 {type(result)!r}"
                )
            response = result
        return response

    async def process_exception(
        self, request: Request, exception: Exception, spider: Any
    ) -> Request | Response | None:
        for middleware in reversed(self.middlewares):
            result = await _maybe_await(
                middleware.process_exception(request, exception, spider)
            )
            if result is None:
                continue
            if not isinstance(result, (Request, Response)):
                raise CrawlxError(
                    f"{type(middleware).__name__}.process_exception 必须返回 Request、Response 或 None，"
                    f"实际是 {type(result)!r}"
                )
            return result
        return None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
