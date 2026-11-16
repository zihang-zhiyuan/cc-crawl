"""离线示例：爬虫 + JSON Lines 管道，不依赖外网和数据库。

运行：

    uv run python examples/pipeline_demo.py
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cc_crawl import Item, Request, Response, Spider, crawl
from cc_crawl.pipelines import JsonLinesPipeline


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = f"<h1>{self.path}</h1>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


class PipelineSpider(Spider):
    name = "pipeline-demo"
    item_pipelines = [
        JsonLinesPipeline(Path(__file__).resolve().parents[1] / "output" / "items.jsonl")
    ]

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        super().__init__()

    async def start_requests(self):
        for path in ("/page/1", "/page/2", "/page/3"):
            yield Request(f"{self.base_url}{path}", callback=self.parse)

    async def parse(self, request: Request, response: Response):
        yield Item(url=request.url, status=response.status_code, body=response.text)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        items = asyncio.run(crawl(PipelineSpider(base_url)))
        for item in items:
            print(item)
        print(f"共 {len(items)} 条，已写入 output/items.jsonl")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
