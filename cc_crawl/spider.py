"""Spider：用户编写爬虫的基类。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from .log import logger
from .request import Request
from .response import Response
from .settings import Settings, global_settings


class Spider:
    """爬虫基类。

    初始请求统一写在 ``start_requests()`` 里，最小用法::

        class DemoSpider(Spider):
            name = "demo"

            async def start_requests(self):
                yield Request("https://example.com", callback=self.parse)

            async def parse(self, request: Request, response: Response):
                yield Item(title=response.css("title::text").get())
    """

    # 爬虫唯一名称，子类必须定义
    name: str = ""
    # 覆盖 Settings 中的任意配置，键名大小写不敏感
    custom_settings: dict[str, Any] = {}
    # 下载中间件，支持类、实例或 "module:Class" 字符串
    downloader_middlewares: list[Any] = []
    # Item 管道，支持类、实例、"module:Class" 或带 kwargs 的 dict
    item_pipelines: list[Any] = []

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        if not getattr(self, "name", None):
            raise ValueError("Spider 必须定义非空的 name")
        self.settings: Settings = global_settings().merged(self.custom_settings)
        if self.downloader_middlewares:
            self.settings = self.settings.merged(
                {"downloader_middlewares": list(self.downloader_middlewares)}
            )
        if self.item_pipelines:
            self.settings = self.settings.merged(
                {"item_pipelines": list(self.item_pipelines)}
            )
        # 方便在爬虫里直接 self.logger.info(...)
        self.logger = logger

    async def start_requests(self) -> AsyncIterator[Request]:
        """生成初始请求，子类必须实现。

        支持三种写法：

        - 异步生成器：``async def start_requests(self): yield Request(...)``
        - 同步生成器或返回列表：``def start_requests(self): return [Request(...)]``
        - 返回协程：``async def start_requests(self): return [Request(...)]``
        """
        raise NotImplementedError(f"{type(self).__name__} 需要实现 start_requests() 方法")

    async def download_midware(self, request: Request) -> Request | None:
        """feapder 风格的下载前置钩子，在每个请求下载前调用。

        可以直接修改 request（如加请求头），也可以返回一个新的 Request；
        返回 None 表示保持不变。同步、异步写法都支持。
        """
        return request

    async def parse(self, request: Request, response: Response) -> Any:
        """默认解析入口，子类必须重写。

        支持两种签名，框架按参数个数自动传入：

        - ``async def parse(self, request, response)``（feapder 风格）
        - ``async def parse(self, response)``（Scrapy 风格）
        """
        raise NotImplementedError(
            f"{type(self).__name__} 需要实现 parse(request, response) 或 parse(response) 方法"
        )

    async def validate(self, response: Response) -> Any:
        """校验响应是否有效，在解析回调之前调用。参数只有 ``response``。

        - 返回 ``True`` / ``None``：进入解析；
        - 返回 ``False``：丢弃该请求，不进入解析，调用 :meth:`failed_request`；
        - 抛出异常：按可重试失败处理（最多 ``retry_times`` 次）。

        典型用法是拦反爬页 / 登录失效页::

            async def validate(self, response):
                if "请登录" in response.text:
                    return False
                if response.status_code == 403:
                    raise RuntimeError("被封禁")
        """
        return True

    async def failed_request(
        self,
        response: Response | None,
        exception: BaseException | None = None,
    ) -> Request | None:
        """请求最终失败时调用。参数是 ``response``（下载失败时为 None）和异常。

        触发时机：``validate`` 返回 False、下载重试耗尽、解析回调异常且重试耗尽。

        返回一个 ``Request`` 表示改投别的请求（会重新入队）；返回 ``None``
        表示放弃该请求（计入 ``failed_requests`` 统计）。下载失败时可以从
        ``exception.url``（``DownloadError``）拿到请求地址。
        """
        return None

    async def end_callback(self) -> None:
        """整轮爬取结束（队列清空、worker 全部停止）后调用一次。

        适合做最终上报、汇总落库、资源释放等；在 :meth:`closed` 之前调用。
        同步、异步写法都支持。
        """

    async def closed(self) -> None:
        """爬虫结束后的收尾钩子，可重写。"""
