"""Response：下载完成后的响应对象，提供文本 / JSON / CSS / XPath 访问。"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urljoin

from .request import Request


class Response:
    """一次 HTTP 请求的结果。

    ``css()`` / ``xpath()`` 需要安装可选依赖 ``parsel``（``pip install cc-crawl[parse]``）。
    """

    def __init__(
        self,
        url: str,
        status_code: int,
        headers: Any,
        content: bytes,
        text: str,
        request: Request,
        *,
        encoding: str | None = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.text = text
        self.request = request
        self.encoding = encoding
        self.meta = request.meta
        self._selector: Any = None

    @classmethod
    def from_curl(cls, raw: Any, request: Request) -> "Response":
        """把 curl_cffi 的响应包装成框架的 Response。"""
        encoding = request.encoding
        if encoding:
            text = raw.content.decode(encoding, errors="replace")
        else:
            text = raw.text
        return cls(
            url=str(raw.url) or request.url,
            status_code=int(raw.status_code),
            headers=raw.headers,
            content=raw.content,
            text=text,
            request=request,
            encoding=encoding,
        )

    def json(self) -> Any:
        """把响应体解析为 JSON。"""
        import json as _json

        return _json.loads(self.text)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def css(self, query: str) -> Any:
        """用 CSS 选择器解析 HTML（依赖 parsel）。"""
        return self._get_selector().css(query)

    def xpath(self, query: str, namespaces: dict[str, str] | None = None) -> Any:
        """用 XPath 解析 HTML（依赖 parsel）。"""
        return self._get_selector().xpath(query, namespaces=namespaces)

    def _get_selector(self) -> Any:
        if self._selector is None:
            try:
                from parsel import Selector
            except ImportError as exc:  # pragma: no cover - 取决于安装环境
                raise ImportError(
                    "使用 css()/xpath() 需要安装 parsel：pip install cc-crawl[parse]"
                ) from exc
            self._selector = Selector(text=self.text)
        return self._selector

    def urljoin(self, url: str) -> str:
        """把相对地址拼成绝对地址。"""
        return urljoin(self.url, url)

    def follow(
        self,
        url: str,
        callback: Callable[..., Any] | None = None,
        **kwargs: Any,
    ) -> Request:
        """基于当前响应的 URL 拼出绝对地址，生成新请求。"""
        return self.request.replace(
            url=self.urljoin(url),
            callback=callback if callback is not None else self.request.callback,
            dont_filter=kwargs.pop("dont_filter", False),
            **kwargs,
        )

    def __repr__(self) -> str:
        return f"<Response {self.status_code} {self.url}>"
