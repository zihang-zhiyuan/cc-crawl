"""示例爬虫：抓取 quotes.toscrape.com 的名言与作者。

运行：

    uv run python -m cc_crawl run examples/demo_spider.py:QuotesSpider --concurrency 4

也可以直接：

    uv run python examples/demo_spider.py
"""
from typing import Any, Callable

from cc_crawl import Item, Request, Response, Spider, logger


class QuotesSpider(Spider):
    name = "测试爬虫2"
    custom_settings = {
        "concurrency": 10,
        "timeout": 20,
        "impersonate": "chrome",
    }

    async def start_requests(self):
        yield await self.send_requests(1, 10, priority=5, meta={'page':1, "page_size":10})

    async def send_requests(self, page, page_size, priority, meta:dict[str, Any]):
        url = 'https://pmos.sgcc.com.cn/px-settlement-infpubquery/ipDisclosureApply/getLatest'
        data = {
            "pageInfo": {
                "pageNum": page,
                "pageSize": page_size
            },
            "data": {
                "marketId": "PSGCC",
                "title": ""
            }
        }
        return Request(url, method="post", json=data, callback=self.parse, priority=priority, meta=meta)


    async def download_midware(self, request: Request) -> Request | None:

        request.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
            "ClientTag": "OUTNET_BROWSE",
            "Connection": "keep-alive",
            "Content-Type": "application/json;charset=UTF-8",
            "CurrentRoute": "/pxf-settlement-outnetpub/columnHomeLeftMenuNew",
            "Origin": "https://pmos.sgcc.com.cn",
            "Pragma": "no-cache",
            "Referer": "https://pmos.sgcc.com.cn/pxf-settlement-outnetpub/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
            "X-Token": "undefined",
            "sec-ch-ua": "\"Not-A.Brand\";v=\"99\", \"Google Chrome\";v=\"157\", \"Chromium\";v=\"157\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\""
        }
        request.cookies = {
            # "Gray-Tag": "686c62655f6b6169686566646332",
            # "ClientTag": "OUTNET_BROWSE",
            # "X-Token": "undefined",
            # "CurrentRoute": "/dashboard",
            # "X-Ticket": "f620b32d875813226985671f85697d64fe61134c9f3134b9a06431ade1e3dffc8b63c53c5a111926949ee88ae9b321b6598f94d11fef490154488c97e8214620.18591c895cbec806f415b694f4a50564e7365d0f"
        }
        return request

    async def parse(self, request: Request, response: Response):
        resp_data = response.json()
        self.logger.info(request.url)
        self.logger.info(request.headers)
        self.logger.info(response.text)
        self.logger.info(response.headers)
        yield Item(
            url=request.url,
            raw_response=response.json(),
        )
        if request.meta["page"] == 1:

            pages = resp_data["data"]["pages"]
            for page in range(2, pages + 1):
                yield self.send_requests(page, request.meta["page_size"], meta={'page': page, "page_size": request.meta["page_size"]}, priority=request.priority)


if __name__ == "__main__":
    from cc_crawl import run_spider

    results = run_spider(QuotesSpider)
    for item in results[:3]:
        print(item)
    print(f"共抓取 {len(results)} 条")
