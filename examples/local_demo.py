"""离线示例：启动本地 HTTP 服务并抓取，不依赖外网。

运行：

    uv run python examples/local_demo.py
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cc_crawl import Item, Request, Response, Spider, crawl


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            body = '<h1>home</h1><a href="/page/2">next</a>'
        elif self.path == "/page/2":
            body = "<h1>page 2</h1>"
        else:
            body = "not found"
        data = body.encode()
        self.send_response(404 if self.path == "/missing" else 200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


class LocalSpider(Spider):
    name = "local-demo"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        super().__init__()

    async def start_requests(self):
        yield Request(f"{self.base_url}/", callback=self.parse)

    async def parse(self, response: Response):
        yield Item(url=response.url, status=response.status_code, length=len(response.text))
        if response.url.endswith("/"):
            yield response.follow("/page/2", callback=self.parse)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        items = asyncio.run(crawl(LocalSpider(base_url)))
        for item in items:
            print(item)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
