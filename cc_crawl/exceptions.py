"""cc-crawl 异常定义。"""


class CrawlxError(Exception):
    """框架内所有异常的基类。"""


class DownloadError(CrawlxError):
    """下载请求失败（网络错误、超时、TLS 错误等）。"""

    def __init__(self, url: str, message: str, *, status_code: int | None = None) -> None:
        self.url = url
        self.message = message
        self.status_code = status_code
        super().__init__(f"下载失败 {url}: {message}")


class SpiderNotFound(CrawlxError):
    """无法定位到指定的 Spider 类。"""
