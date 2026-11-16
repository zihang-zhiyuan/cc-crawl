"""validate / failed_request / end_callback 钩子测试（feapder 风格）。"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cc_crawl import Item, Request, Spider, SpiderRegistry, run_spider
from cc_crawl.exceptions import ValidationFailed
from cc_crawl.runner import run_task_async


class Handler(BaseHTTPRequestHandler):
    counts: dict[str, int] = {}
    lock = threading.Lock()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/page":
            self._write("ok")
        elif self.path == "/signin":
            self._write("请登录")
        elif self.path == "/forbidden":
            self._write("forbidden", status=403)
        elif self.path == "/flaky-parse":
            with Handler.lock:
                count = Handler.counts.get(self.path, 0) + 1
                Handler.counts[self.path] = count
            if count == 1:
                self._write("first", status=500)
            else:
                self._write("ok")
        else:
            self._write("not found", status=404)

    def _write(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def base_url():
    Handler.counts.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_validate_false_drops_request_and_calls_failed_request(base_url):
    events = []

    class ValidateSpider(Spider):
        name = "validate-false"

        def __init__(self, start_url):
            self.start_url = start_url
            super().__init__()

        async def start_requests(self):
            yield Request(self.start_url, callback=self.parse)

        def validate(self, response):
            return False

        async def parse(self, response):  # pragma: no cover - 不应执行
            events.append("parsed")
            yield Item(ok=True)

        def failed_request(self, response, exception):
            events.append(type(exception).__name__)

    items = run_spider(ValidateSpider, f"{base_url}/signin")

    assert items == []
    assert events == ["ValidationFailed"]


def test_validate_exception_retries_then_fails(base_url):
    events = []

    class ForbiddenSpider(Spider):
        name = "forbidden"

        def __init__(self, start_url):
            self.start_url = start_url
            super().__init__()

        async def start_requests(self):
            yield Request(self.start_url, callback=self.parse)

        async def validate(self, response):
            raise RuntimeError("blocked")

        async def parse(self, response):  # pragma: no cover
            yield Item(ok=True)

        async def failed_request(self, response, exception):
            events.append(str(exception))

    items = run_spider(
        ForbiddenSpider,
        f"{base_url}/forbidden",
        settings={"retry_times": 2},
    )

    assert items == []
    # 首次 + 2 次重试 = 3 次，最终只回调一次 failed_request
    assert events == ["blocked"]


def test_parse_exception_retries_and_succeeds(base_url):
    events = []

    class FlakyParseSpider(Spider):
        name = "flaky-parse"

        def __init__(self, start_url):
            self.start_url = start_url
            super().__init__()
            self.attempts = 0

        async def start_requests(self):
            yield Request(self.start_url, callback=self.parse)

        async def parse(self, response):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("parse boom")
            yield Item(attempts=self.attempts)

        async def failed_request(self, response, exception):
            events.append(str(exception))

    items = run_spider(
        FlakyParseSpider,
        f"{base_url}/page",
        settings={"retry_times": 2},
    )

    assert len(items) == 1
    assert items[0]["attempts"] == 2
    assert events == []


def test_failed_request_can_return_replacement_request(base_url):
    seen = []

    class ReplaceSpider(Spider):
        name = "replace"

        def __init__(self, start_url):
            self.start_url = start_url
            super().__init__()

        async def start_requests(self):
            yield Request(self.start_url, callback=self.parse)

        async def validate(self, response):
            return response.status_code == 200

        async def parse(self, response):
            seen.append(response.url)
            yield Item(url=response.url)

        async def failed_request(self, response, exception):
            assert isinstance(exception, ValidationFailed)
            return Request(
                f"{response.url.rsplit('/', 1)[0]}/page",
                callback=self.parse,
            )

    items = run_spider(ReplaceSpider, f"{base_url}/missing")

    assert [item["url"] for item in items] == [f"{base_url}/page"]
    assert seen == [f"{base_url}/page"]


def test_end_callback_runs_once_after_crawl(base_url):
    calls = []

    class EndSpider(Spider):
        name = "end"

        def __init__(self, start_url):
            self.start_url = start_url
            super().__init__()

        async def start_requests(self):
            yield Request(self.start_url, callback=self.parse)

        async def parse(self, response):
            yield Item(ok=True)

        async def end_callback(self):
            calls.append("end")

        async def closed(self):
            calls.append("closed")

    items = run_spider(EndSpider, f"{base_url}/page")

    assert len(items) == 1
    assert calls == ["end", "closed"]


def test_run_task_marks_failed_when_no_request_succeeds(base_url):
    import asyncio

    class AllFailSpider(Spider):
        name = "all-fail"

        async def start_requests(self):
            yield Request(f"{base_url}/signin", callback=self.parse)

        async def validate(self, response):
            return False

        async def parse(self, response):  # pragma: no cover
            yield Item(ok=True)

    result = asyncio.run(
        run_task_async(
            {"spider": "all-fail", "params": {}},
            SpiderRegistry([AllFailSpider]),
        )
    )

    assert result["status"] == "failed"
    assert result["stats"]["validation_failed"] == 1
    assert result["stats"]["failed_requests"] == 1


def test_legacy_two_arg_validate_and_three_arg_failed_request_still_work(base_url):
    calls = []

    class LegacySpider(Spider):
        name = "legacy-hooks"

        def __init__(self, start_url):
            self.start_url = start_url
            super().__init__()

        async def start_requests(self):
            yield Request(self.start_url, callback=self.parse)

        async def validate(self, request, response):
            calls.append(("validate", request.url, response.status_code))
            return False

        async def failed_request(self, request, response, exception):
            calls.append(("failed", request.url, type(exception).__name__))

        async def parse(self, response):  # pragma: no cover
            yield Item(ok=True)

    run_spider(LegacySpider, f"{base_url}/signin")

    assert calls == [
        ("validate", f"{base_url}/signin", 200),
        ("failed", f"{base_url}/signin", "ValidationFailed"),
    ]
