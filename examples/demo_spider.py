"""示例爬虫：抓取 quotes.toscrape.com 的名言与作者。

运行：

    uv run python -m cc_crawl run examples/demo_spider.py:QuotesSpider --concurrency 4 -a start_page=1

也可以直接：

    uv run python examples/demo_spider.py
"""

from cc_crawl import Item, Request, Response, Spider


class QuotesSpider(Spider):
    name = "quotes"
    base_url = "https://quotes.toscrape.com"
    custom_settings = {
        "concurrency": 4,
        "timeout": 20,
        "impersonate": "chrome",
    }

    def __init__(self, start_page: int = 1) -> None:
        self.start_page = int(start_page)
        super().__init__()

    async def start_requests(self):
        yield Request(f"{self.base_url}/page/{self.start_page}/", callback=self.parse)

    async def parse(self, request: Request, response: Response):
        for quote in response.css("div.quote"):
            yield Item(
                text=quote.css("span.text::text").get(),
                author=quote.css("small.author::text").get(),
                tags=quote.css("div.tags a.tag::text").getall(),
                url=request.url,
            )

        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            yield response.follow(next_page, callback=self.parse)


if __name__ == "__main__":
    from cc_crawl import run_spider

    results = run_spider(QuotesSpider)
    for item in results[:3]:
        print(item)
    print(f"共抓取 {len(results)} 条")
