"""引擎集成测试：用本地 HTTP 服务验证下载、回调链、并发与错误处理。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cc_crawl import Engine, Item, ItemPipeline, Request, Response, Spider, run_spider
from cc_crawl.middlewares import DownloaderMiddleware


class Handler(BaseHTTPRequestHandler):
    flaky_counts: dict[str, int] = {}
    flaky_lock = threading.Lock()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        if self.path == "/":
            body = '<a href="/page/1">1</a><a href="/page/2">2</a>'
            self._write(body, "text/html; charset=utf-8")
        elif self.path.startswith("/page/"):
            number = int(self.path.rsplit("/", 1)[-1])
            body = f"<h1>page {number}</h1>"
            self._write(body, "text/html; charset=utf-8")
        elif self.path.startswith("/slow/"):
            time.sleep(0.25)
            self._write(self.path, "text/plain; charset=utf-8")
        elif self.path.startswith("/flaky/"):
            with Handler.flaky_lock:
                count = Handler.flaky_counts.get(self.path, 0) + 1
                Handler.flaky_counts[self.path] = count
            if count <= 2:
                self._write("server error", "text/plain; charset=utf-8", status=503)
            else:
                self._write("ok", "text/plain; charset=utf-8")
        elif self.path == "/status/503":
            self._write("unavailable", "text/plain; charset=utf-8", status=503)
        elif self.path == "/echo-header":
            self._write(self.headers.get("X-Test", ""), "text/plain; charset=utf-8")
        elif self.path.startswith("/cookie"):
            self._write(self.headers.get("Cookie", ""), "text/plain; charset=utf-8")
        elif self.path.startswith("/set-cookie/"):
            name = self.path.rsplit("/", 1)[-1]
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Set-Cookie", f"sid={name}; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/json":
            self._write(json.dumps({"ok": True}), "application/json")
        else:
            self._write("not found", "text/plain; charset=utf-8", status=404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length)
        self._write(
            json.dumps({"body": payload.decode(), "ctype": self.headers.get("Content-Type")}),
            "application/json",
        )

    def _write(self, body: str, content_type: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def base_url() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


class PagingSpider(Spider):
    name = "paging"
    last_page = 3

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    async def parse(self, response):
        yield Item(page=response.url, body=response.text)
        number = int(response.url.rsplit("/", 1)[-1])
        if number < self.last_page:
            yield response.follow(f"/page/{number + 1}")


def test_engine_follows_requests_and_collects_items(base_url):
    engine = Engine(PagingSpider(f"{base_url}/page/1"))
    items = asyncio.run(engine.crawl())

    assert len(items) == 3
    assert [item["page"] for item in items] == [
        f"{base_url}/page/1",
        f"{base_url}/page/2",
        f"{base_url}/page/3",
    ]
    assert engine.stats.requests == 3
    assert engine.stats.responses == 3
    assert engine.stats.item_count == 3
    assert engine.stats.errors == 0
    assert engine.stats.duration > 0


class SyncCallbackSpider(Spider):
    name = "sync-callback"

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        # 返回 awaitable -> list，验证引擎兼容非生成器的 start_requests
        return [Request(self.start_url, callback=self.parse_sync)]

    def parse_sync(self, response):
        # 同步回调 + 普通 list + dict 自动转 Item
        return [Item(ok=True), {"from": "dict"}]


def test_engine_supports_sync_callbacks_and_plain_lists(base_url):
    items = asyncio.run(Engine(SyncCallbackSpider(base_url)).crawl())

    assert len(items) == 2
    assert items[0]["ok"] is True
    assert items[1]["from"] == "dict"


class ParamSpider(Spider):
    name = "param"

    def __init__(self, start_url: str, keyword: str = "default") -> None:
        self.start_url = start_url
        self.keyword = keyword
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    async def parse(self, response):
        yield Item(keyword=self.keyword)


def test_run_spider_forwards_constructor_args(base_url):
    items = run_spider(
        ParamSpider,
        f"{base_url}/json",
        keyword="cc-crawl",
        settings={"concurrency": 2},
    )

    assert len(items) == 1
    assert items[0]["keyword"] == "cc-crawl"


class JsonPostSpider(Spider):
    name = "json-post"

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(
            self.start_url,
            method="POST",
            json={"hello": "world"},
            callback=self.parse,
        )

    async def parse(self, response):
        yield Item(data=response.json())


def test_engine_supports_post_json(base_url):
    items = asyncio.run(Engine(JsonPostSpider(f"{base_url}/json")).crawl())

    assert items[0]["data"]["body"] == '{"hello": "world"}'
    assert items[0]["data"]["ctype"] == "application/json"


class SlowSpider(Spider):
    name = "slow"
    custom_settings = {"concurrency": 4}

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        for index in range(4):
            yield Request(f"{self.start_url}/slow/{index}", callback=self.parse)

    async def parse(self, response):
        yield Item(url=response.url)


def test_engine_runs_requests_concurrently(base_url):
    started = time.perf_counter()
    items = asyncio.run(Engine(SlowSpider(base_url)).crawl())
    elapsed = time.perf_counter() - started

    assert len(items) == 4
    # 串行需要约 1s，并发 4 应该明显更快
    assert elapsed < 0.9


class BrokenSpider(Spider):
    name = "broken"

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    async def parse(self, response):
        yield Item(url=response.url)


def test_engine_records_download_errors_and_keeps_running(base_url):
    engine = Engine(BrokenSpider("http://127.0.0.1:1/unreachable"))
    items = asyncio.run(engine.crawl())

    assert items == []
    assert engine.stats.errors == 1
    assert engine.stats.responses == 0


class NoStartRequestsSpider(Spider):
    name = "no-start-requests"

    async def parse(self, response):
        yield Item(url=response.url)


def test_spider_without_start_requests_fails_clearly():
    with pytest.raises(NotImplementedError, match="start_requests"):
        asyncio.run(Engine(NoStartRequestsSpider()).crawl())


class HttpErrorSpider(Spider):
    name = "http-error"

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    async def parse(self, response):
        yield Item(status=response.status_code, ok=response.ok)


def test_engine_returns_response_for_http_error_status(base_url):
    items = asyncio.run(Engine(HttpErrorSpider(f"{base_url}/missing")).crawl())

    assert items[0]["status"] == 404
    assert items[0]["ok"] is False


class RetrySpider(Spider):
    name = "retry"
    custom_settings = {"retry_times": 2, "retry_backoff": 0.01}

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__()

    async def start_requests(self):
        yield Request(self.url, callback=self.parse)

    async def parse(self, response):
        yield Item(status=response.status_code, text=response.text)


def test_downloader_retries_failed_status_until_success(base_url):
    engine = Engine(RetrySpider(f"{base_url}/flaky/retry-ok"))
    items = asyncio.run(engine.crawl())

    assert items[0]["status"] == 200
    assert engine.stats.retries == 2
    assert engine.stats.responses == 1
    assert engine.stats.errors == 0


def test_downloader_gives_up_after_retrying_network_error():
    engine = Engine(RetrySpider("http://127.0.0.1:1/unreachable"))
    items = asyncio.run(engine.crawl())

    assert items == []
    assert engine.stats.retries == 2
    assert engine.stats.errors == 1


class NoStatusRetrySpider(RetrySpider):
    name = "no-status-retry"
    custom_settings = {"retry_times": 2, "retry_status_codes": ()}


def test_retry_status_codes_can_be_disabled(base_url):
    engine = Engine(NoStatusRetrySpider(f"{base_url}/status/503"))
    items = asyncio.run(engine.crawl())

    assert items[0]["status"] == 503
    assert engine.stats.retries == 0
    assert engine.stats.errors == 0


class IntervalSpider(Spider):
    name = "interval"
    custom_settings = {"concurrency": 4, "request_interval": 0.1}

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        for index in range(4):
            yield Request(f"{self.start_url}/slow/{index}", callback=self.parse)

    async def parse(self, response):
        yield Item(url=response.url)


def test_request_interval_spaces_requests(base_url):
    started = time.perf_counter()
    items = asyncio.run(Engine(IntervalSpider(base_url)).crawl())
    elapsed = time.perf_counter() - started

    assert len(items) == 4
    # 4 个请求、间隔 0.1s，至少需要 3 个间隔
    assert elapsed >= 0.3



class HeaderMiddleware(DownloaderMiddleware):
    async def process_request(self, request, spider):
        request.headers["X-Test"] = "cc-crawl"


class HeaderSpider(Spider):
    name = "header"
    downloader_middlewares = [HeaderMiddleware]

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    async def parse(self, response):
        yield Item(text=response.text)


def test_middleware_can_modify_request(base_url):
    items = asyncio.run(Engine(HeaderSpider(f"{base_url}/echo-header")).crawl())

    assert items[0]["text"] == "cc-crawl"


class BlockMiddleware(DownloaderMiddleware):
    async def process_request(self, request, spider):
        if request.url.endswith("/blocked"):
            return Response(
                url=request.url,
                status_code=200,
                headers={},
                content=b"blocked",
                text="blocked",
                request=request,
            )
        return None


class BlockSpider(Spider):
    name = "block"
    custom_settings = {"downloader_middlewares": [BlockMiddleware]}

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    async def parse(self, response):
        yield Item(text=response.text)


def test_middleware_can_short_circuit_download(base_url):
    engine = Engine(BlockSpider(f"{base_url}/blocked"))
    items = asyncio.run(engine.crawl())

    assert items[0]["text"] == "blocked"
    assert engine.stats.attempt_count == 0
    assert engine.stats.responses == 1


class RecoverMiddleware(DownloaderMiddleware):
    async def process_exception(self, request, exception, spider):
        return Response(
            url=request.url,
            status_code=200,
            headers={},
            content=b"recovered",
            text="recovered",
            request=request,
        )


class RecoverSpider(Spider):
    name = "recover"
    custom_settings = {"downloader_middlewares": [RecoverMiddleware], "retry_times": 0}

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    async def parse(self, response):
        yield Item(text=response.text)


def test_middleware_can_recover_from_download_error():
    engine = Engine(RecoverSpider("http://127.0.0.1:1/unreachable"))
    items = asyncio.run(engine.crawl())

    assert items[0]["text"] == "recovered"
    assert engine.stats.errors == 1
    assert engine.stats.responses == 1


class MarkMiddleware(DownloaderMiddleware):
    def __init__(self, mark: str) -> None:
        self.mark = mark

    async def process_response(self, request, response, spider):
        response.text = f"{response.text}-{self.mark}"
        return response


class OrderSpider(Spider):
    name = "order"
    downloader_middlewares = [MarkMiddleware("a"), MarkMiddleware("b")]

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    async def parse(self, response):
        yield Item(text=response.text)


def test_response_middlewares_run_in_reverse_order(base_url):
    items = asyncio.run(Engine(OrderSpider(f"{base_url}/json")).crawl())

    assert items[0]["text"].endswith("-b-a")


class MixedSpider(Spider):
    name = "mixed"

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(f"{self.start_url}/json", callback=self.parse)
        yield Request(f"{self.start_url}/missing", callback=self.parse)

    async def parse(self, response):
        yield Item(status=response.status_code)


def test_stats_tracks_success_failure_and_status(base_url):
    engine = Engine(MixedSpider(base_url))
    items = asyncio.run(engine.crawl())

    assert len(items) == 2
    assert engine.stats.responses == 2
    assert engine.stats.success_count == 1
    assert engine.stats.failure_count == 1
    assert engine.stats.status_counts == {200: 1, 404: 1}
    assert engine.stats.avg_latency > 0
    assert engine.stats.max_latency >= engine.stats.avg_latency


class PlainSpider(Spider):
    name = "plain"

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    async def parse(self, response):
        yield Item(text=response.text)


def test_engine_can_add_middleware_in_code(base_url):
    engine = Engine(PlainSpider(f"{base_url}/echo-header"))
    engine.add_middleware(HeaderMiddleware)

    items = asyncio.run(engine.crawl())

    assert items[0]["text"] == "cc-crawl"


class AliasSpider(PlainSpider):
    name = "alias"
    custom_settings = {"DOWNLOADER_MIDDLEWARES": [HeaderMiddleware]}


def test_custom_settings_accepts_scrapy_style_middleware_key(base_url):
    items = asyncio.run(Engine(AliasSpider(f"{base_url}/echo-header")).crawl())

    assert items[0]["text"] == "cc-crawl"


class HookSpider(Spider):
    name = "hook"

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    def download_midware(self, request):
        request.headers["X-Test"] = "cc-crawl"
        return request

    async def parse(self, response):
        yield Item(text=response.text)


def test_spider_download_midware_hook(base_url):
    items = asyncio.run(Engine(HookSpider(f"{base_url}/echo-header")).crawl())

    assert items[0]["text"] == "cc-crawl"


class RequestAwareSpider(Spider):
    name = "request-aware"

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        yield Request(self.start_url, callback=self.parse)

    async def parse(self, request, response):
        yield Item(
            url=request.url,
            status=response.status_code,
            same_request=response.request is request,
        )


def test_parse_can_receive_request_and_response(base_url):
    items = asyncio.run(Engine(RequestAwareSpider(f"{base_url}/json")).crawl())

    assert items[0]["url"] == f"{base_url}/json"
    assert items[0]["status"] == 200
    assert items[0]["same_request"] is True


class PrioritySpider(Spider):
    name = "priority"
    custom_settings = {"concurrency": 1}

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.order: list[str] = []
        super().__init__()

    async def start_requests(self):
        yield Request(f"{self.base_url}/page/1", callback=self.parse_root)

    async def parse_root(self, request, response):
        self.order.append(request.url)
        yield Request(f"{self.base_url}/page/2", callback=self.parse_leaf, priority=1)
        yield Request(f"{self.base_url}/page/3", callback=self.parse_leaf, priority=10)
        yield Request(f"{self.base_url}/page/4", callback=self.parse_leaf, priority=5)

    async def parse_leaf(self, request, response):
        self.order.append(request.url)
        yield Item(url=request.url)


def test_request_priority_orders_queue(base_url):
    spider = PrioritySpider(base_url)
    items = asyncio.run(Engine(spider).crawl())

    assert len(items) == 3
    assert spider.order == [
        f"{base_url}/page/1",
        f"{base_url}/page/3",
        f"{base_url}/page/4",
        f"{base_url}/page/2",
    ]


class AsyncHelperSpider(Spider):
    name = "async-helper"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        super().__init__()

    async def start_requests(self):
        # 故意 yield 协程，验证框架会自动 await
        yield self.make_request(0)

    async def make_request(self, depth: int):
        return Request(
            f"{self.base_url}/json",
            callback=self.parse,
            meta={"depth": depth},
            dont_filter=depth > 0,
        )

    async def parse(self, request, response):
        yield Item(status=response.status_code, depth=request.meta["depth"])
        if request.meta["depth"] < 1:
            yield self.make_request(request.meta["depth"] + 1)


def test_async_helper_returning_request_is_awaited(base_url):
    items = asyncio.run(Engine(AsyncHelperSpider(base_url)).crawl())

    assert [item["depth"] for item in items] == [0, 1]


def test_engine_stats_json_payload(base_url):
    engine = Engine(PlainSpider(f"{base_url}/json"))
    asyncio.run(engine.crawl())

    payload = json.loads(engine._stats_json("crawler_finished"))

    assert payload["event"] == "crawler_finished"
    assert payload["spider"] == "plain"
    assert payload["responses"] == 1
    assert payload["status_counts"] == {"200": 1}


class TagPipeline(ItemPipeline):
    async def process_item(self, item, spider):
        item["pipeline"] = "done"
        return item


class DropAllPipeline(ItemPipeline):
    async def process_item(self, item, spider):
        return None


class BoomPipeline(ItemPipeline):
    async def process_item(self, item, spider):
        raise RuntimeError("boom")


class PipedSpider(PlainSpider):
    name = "piped"
    item_pipelines = [TagPipeline]


class DroppedSpider(PlainSpider):
    name = "dropped"
    item_pipelines = [DropAllPipeline]


class BoomSpider(PlainSpider):
    name = "boom"
    item_pipelines = [BoomPipeline]


def test_engine_runs_item_pipelines(base_url):
    engine = Engine(PipedSpider(f"{base_url}/json"))
    items = asyncio.run(engine.crawl())

    assert items[0]["pipeline"] == "done"
    assert engine.stats.item_count == 1
    assert engine.stats.items_dropped == 0


def test_pipeline_can_drop_item(base_url):
    engine = Engine(DroppedSpider(f"{base_url}/json"))
    items = asyncio.run(engine.crawl())

    assert items == []
    assert engine.stats.item_count == 0
    assert engine.stats.items_dropped == 1


def test_pipeline_error_drops_item_and_keeps_crawling(base_url):
    engine = Engine(BoomSpider(f"{base_url}/json"))
    items = asyncio.run(engine.crawl())

    assert items == []
    assert engine.stats.pipeline_errors == 1
    assert engine.stats.items_dropped == 1


# ---------------------------------------------------------------------- 会话隔离


class SessionSpider(Spider):
    name = "session"
    custom_settings = {"concurrency": 4}

    def __init__(self, start_url: str) -> None:
        self.start_url = start_url
        super().__init__()

    async def start_requests(self):
        for key in ("a", "b"):
            yield Request(
                f"{self.start_url}/set-cookie/{key}",
                session=key,
                callback=self.parse_login,
            )

    async def parse_login(self, request, response):
        # follow 会保留 session 键，继续用同一个会话
        yield response.follow("/cookie", callback=self.parse_cookie)

    async def parse_cookie(self, request, response):
        yield Item(session=request.session, cookie=response.text)


def test_sessions_isolate_cookies(base_url):
    items = asyncio.run(Engine(SessionSpider(base_url)).crawl())

    cookies = {item["session"]: item["cookie"] for item in items}
    assert cookies == {"a": "sid=a", "b": "sid=b"}



# ---------------------------------------------------------------------- 去重


class DuplicateSpider(Spider):
    name = "duplicate"
    custom_settings = {"concurrency": 2}

    def __init__(self, start_url: str, allow: bool = False) -> None:
        self.start_url = start_url
        self.allow = allow
        super().__init__()

    async def start_requests(self):
        for _ in range(2):
            yield Request(
                f"{self.start_url}/json",
                callback=self.parse,
                dont_filter=self.allow,
            )

    async def parse(self, response):
        yield Item(ok=True)


def test_dupefilter_skips_repeated_requests(base_url):
    engine = Engine(DuplicateSpider(base_url))
    items = asyncio.run(engine.crawl())

    assert len(items) == 1
    assert engine.stats.requests == 1
    assert engine.stats.duplicates == 1
    assert engine.stats.responses == 1


def test_dont_filter_allows_repeated_requests(base_url):
    engine = Engine(DuplicateSpider(base_url, allow=True))
    items = asyncio.run(engine.crawl())

    assert len(items) == 2
    assert engine.stats.duplicates == 0


def test_dupefilter_can_be_disabled_by_settings(base_url):
    engine = Engine(DuplicateSpider(base_url), overrides={"dupefilter": False})
    items = asyncio.run(engine.crawl())

    assert len(items) == 2
    assert engine.stats.duplicates == 0


def test_collect_items_false_streams_to_sink_without_retaining(base_url):
    seen = []
    engine = Engine(
        PlainSpider(f"{base_url}/json"),
        overrides={"collect_items": False},
        item_sink=seen.append,
    )
    items = asyncio.run(engine.crawl())

    assert items == []
    assert engine.stats.item_count == 1
    assert len(seen) == 1
    assert "ok" in seen[0]["text"]
