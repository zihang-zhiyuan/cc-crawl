"""下载器：基于 curl_cffi 的 AsyncSession 实现异步 HTTP 下载。"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from curl_cffi.requests import AsyncSession
from loguru import logger

from .exceptions import DownloadError
from .request import Request
from .response import Response
from .settings import Settings
from .stats import StatsCollector


class Downloader:
    """封装 curl_cffi 的异步下载器。

    默认整个爬虫共用一个 ``AsyncSession``；请求上指定 ``Request(session="...")``
    时会按会话键隔离出一个独立会话（独立 Cookie 罐与连接），互不串号。
    账号怎么分配给请求由外部调度层（如 crawl-kit）决定，框架只提供隔离能力。

    下载器内部负责：

    - **失败重试**：连接错误、超时以及命中 ``retry_status_codes`` 的响应会按
      ``retry_times`` 重试，重试前等待 ``retry_backoff * 第几次`` 秒
    - **请求间隔**：所有 worker 共享一个起始时间闸门，两次请求的发起至少间隔
      ``request_interval`` 秒
    - **运行统计**：请求次数、响应数、状态码分布、下载耗时、重试与异常，写入 StatsCollector
    """

    def __init__(self, settings: Settings, stats: StatsCollector | None = None) -> None:
        self.settings = settings
        self.stats = stats
        self._sessions: dict[str | None, AsyncSession] = {}
        self._opened = False
        self._pace_lock: asyncio.Lock | None = None
        self._next_request_at = 0.0

    async def open(self) -> None:
        if self._opened:
            return
        self._pace_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._opened = True
        logger.debug(
            "下载器已启动，impersonate={}，重试 {} 次，请求间隔 {}s",
            self.settings.impersonate,
            self.settings.retry_times,
            self.settings.request_interval,
        )

    async def close(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        self._opened = False
        self._pace_lock = None
        if sessions:
            await asyncio.gather(
                *(session.close() for session in sessions), return_exceptions=True
            )

    async def fetch(self, request: Request) -> Response:
        """下载单个请求，失败时按配置重试；全部失败后抛 DownloadError。"""
        if not self._opened:
            raise DownloadError(request.url, "下载器尚未启动，请先调用 open()")
        session = self._session_for(request.session)

        attempts = self.settings.retry_times + 1
        last_error: DownloadError | None = None

        for attempt in range(1, attempts + 1):
            await self._pace()
            self._inc("downloader/attempt_count")
            started = time.perf_counter()
            try:
                raw = await session.request(
                    request.method,
                    request.url,
                    **self._request_kwargs(request),
                )
            except Exception as exc:  # noqa: BLE001 - 统一转换为框架异常
                last_error = DownloadError(request.url, f"{type(exc).__name__}: {exc}")
                self._record_exception(exc, final=attempt >= attempts)
                if attempt >= attempts:
                    raise last_error from exc
                self._record_retry(request, attempt, last_error)
                await self._sleep_backoff(attempt)
                continue

            latency = time.perf_counter() - started
            response = Response.from_curl(raw, request)
            if (
                response.status_code in self.settings.retry_status_codes
                and attempt < attempts
            ):
                self._record_retry(request, attempt, None)
                await self._sleep_backoff(attempt)
                continue

            self._record_latency(latency)
            return response

        raise last_error or DownloadError(request.url, "未知下载错误")

    # ------------------------------------------------------------------ 内部工具
    def _session_for(self, key: str | None) -> AsyncSession:
        session = self._sessions.get(key)
        if session is None:
            session = self._new_session(self.settings.headers)
            self._sessions[key] = session
        return session

    def _new_session(self, headers: dict[str, str]) -> AsyncSession:
        options: dict[str, Any] = {
            "impersonate": self.settings.impersonate,
            "headers": headers,
            "timeout": self.settings.timeout,
            "verify": self.settings.verify,
        }
        # curl_cffi 0.9+ 通过 max_clients 限制并发句柄数，旧版本没有该参数
        if "max_clients" in inspect.signature(AsyncSession.__init__).parameters:
            options["max_clients"] = max(self.settings.concurrency, 10)
        return AsyncSession(**options)

    def _request_kwargs(self, request: Request) -> dict[str, Any]:
        """把 Request 转成 curl_cffi 的请求参数，未设置的项不传。"""
        kwargs: dict[str, Any] = {
            "headers": request.headers or None,
            "cookies": request.cookies,
            "params": request.params,
            "data": request.data,
            "json": request.json,
            "timeout": request.timeout if request.timeout is not None else self.settings.timeout,
            "allow_redirects": request.allow_redirects,
        }
        proxy = request.proxy or self.settings.proxy
        if proxy:
            kwargs["proxy"] = proxy
        if request.verify is not None:
            kwargs["verify"] = request.verify
        if request.impersonate:
            kwargs["impersonate"] = request.impersonate
        # 去掉未设置的可选参数，避免覆盖 session 默认值
        return {key: value for key, value in kwargs.items() if value is not None}

    async def _pace(self) -> None:
        """保证两次请求的发起间隔不小于 request_interval。"""
        interval = self.settings.request_interval
        if interval <= 0 or self._pace_lock is None:
            return
        loop = asyncio.get_running_loop()
        async with self._pace_lock:
            now = loop.time()
            if self._next_request_at > now:
                await asyncio.sleep(self._next_request_at - now)
            self._next_request_at = loop.time() + interval

    async def _sleep_backoff(self, attempt: int) -> None:
        if self.settings.retry_backoff > 0:
            await asyncio.sleep(self.settings.retry_backoff * attempt)

    def _inc(self, key: str, count: int = 1) -> None:
        if self.stats is not None:
            self.stats.inc_value(key, count)

    def _record_latency(self, latency: float) -> None:
        if self.stats is None:
            return
        self.stats.inc_value("downloader/download_latency_total_seconds", latency)
        self.stats.max_value("downloader/download_latency_max_seconds", latency)
        self.stats.min_value("downloader/download_latency_min_seconds", latency)

    def _record_exception(self, exc: Exception, final: bool) -> None:
        if self.stats is None:
            return
        self.stats.inc_value(f"downloader/exception_type_count/{type(exc).__name__}")
        if final:
            self.stats.inc_value("downloader/exception_count")

    def _record_retry(
        self,
        request: Request,
        attempt: int,
        error: DownloadError | None,
    ) -> None:
        self._inc("downloader/retry_count")
        reason = str(error) if error is not None else "HTTP 状态码命中重试规则"
        logger.warning(
            "第 {}/{} 次重试 {}：{}",
            attempt,
            self.settings.retry_times,
            request.url,
            reason,
        )
