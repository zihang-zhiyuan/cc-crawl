"""全局配置。"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Mapping


def _default_headers() -> dict[str, str]:
    return {
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


# 兼容常见的中间件配置写法（Scrapy 风格 DOWNLOADER_MIDDLEWARES 会因小写化直接命中规范名）
_FIELD_ALIASES: dict[str, str] = {
    "download_middlewares": "downloader_middlewares",
    "download_middleware": "downloader_middlewares",
    "download_middle": "downloader_middlewares",
    "pipelines": "item_pipelines",
    "pipeline": "item_pipelines",
    "item_pipeline": "item_pipelines",
}


@dataclass
class Settings:
    """爬虫运行配置。

    可以在 Spider 里通过 ``custom_settings = {...}`` 覆盖，键名大小写不敏感
    （``concurrency`` 与 ``CONCURRENCY`` 等价）。
    """

    # 并发下载数（同时在途的请求数）
    concurrency: int = 8
    # 单请求超时（秒）
    timeout: float = 30.0
    # curl_cffi 浏览器指纹，如 chrome / chrome124 / safari / firefox
    impersonate: str = "chrome"
    # 全局默认请求头（与 impersonate 的默认头合并）
    headers: dict[str, str] = field(default_factory=_default_headers)
    # 代理地址，如 http://127.0.0.1:7890 或 socks5://...
    proxy: str | None = None
    # 是否校验 HTTPS 证书（默认关闭，避免自签/过期证书中断抓取）
    verify: bool = False
    # 下载失败时的重试次数（不含首次请求），0 表示不重试
    retry_times: int = 2
    # 两次重试之间的基础等待秒数，第 n 次重试等待 retry_backoff * n
    retry_backoff: float = 0.0
    # 命中这些 HTTP 状态码时也按失败重试
    retry_status_codes: tuple[int, ...] = (429, 500, 502, 503, 504)
    # 每次请求之间的最小间隔（秒），0 表示不限速
    request_interval: float = 0.0
    # 请求去重：同一方法 + URL + 参数 + 请求体只调度一次
    dupefilter: bool = True
    # 去重指纹最多保留多少条，超出按先进先出淘汰
    dupefilter_maxsize: int = 1_000_000
    # 是否在内存里保留全部 Item（run_spider 需要；任务级消费建议关闭，交给管道）
    collect_items: bool = True
    # 运行统计的周期日志间隔（秒），0 表示关闭周期日志
    stats_interval: float = 60.0
    # 运行统计的输出格式：json（单行 JSON，便于采集）或 text（方框表格）
    stats_format: str = "json"
    # 下载中间件列表：类、实例或 "module:Class" 字符串
    downloader_middlewares: list[Any] = field(default_factory=list)
    # Item 管道列表：类、实例、"module:Class" 或带 kwargs 的 dict
    item_pipelines: list[Any] = field(default_factory=list)
    # 日志级别：DEBUG / INFO / WARNING / ERROR
    log_level: str = "INFO"
    # 日志文件路径（loguru 输出，按 10MB 切分），None 表示只输出到控制台
    log_file: str | None = None

    def __post_init__(self) -> None:
        self.concurrency = max(1, int(self.concurrency))
        self.timeout = float(self.timeout)
        self.retry_times = max(0, int(self.retry_times))
        self.retry_backoff = max(0.0, float(self.retry_backoff))
        self.retry_status_codes = tuple(int(code) for code in self.retry_status_codes)
        self.request_interval = max(0.0, float(self.request_interval))
        self.dupefilter = bool(self.dupefilter)
        self.dupefilter_maxsize = max(1, int(self.dupefilter_maxsize))
        self.stats_interval = max(0.0, float(self.stats_interval))
        self.stats_format = str(self.stats_format).lower()
        if self.stats_format not in {"json", "text"}:
            raise ValueError("stats_format 只支持 json 或 text")
        self.downloader_middlewares = list(self.downloader_middlewares or [])
        self.item_pipelines = list(self.item_pipelines or [])

    def merged(self, custom: Mapping[str, Any] | "Settings" | None) -> "Settings":
        """返回合并 custom_settings 后的新配置，不修改自身。"""
        if custom is None:
            return replace(self, headers=dict(self.headers))
        if isinstance(custom, Settings):
            return replace(custom, headers=dict(custom.headers))

        values = {item.name: getattr(self, item.name) for item in fields(self)}
        canonical = set(values)
        # 先应用别名键，再应用规范键，保证同时出现时规范键优先
        for key, value in custom.items():
            name = key.lower()
            if name in canonical:
                continue
            target = _FIELD_ALIASES.get(name)
            if target is not None:
                values[target] = value
        for key, value in custom.items():
            name = key.lower()
            if name in canonical:
                values[name] = value
        if "concurrency" in values:
            values["concurrency"] = max(1, int(values["concurrency"]))
        if "headers" in values:
            values["headers"] = dict(values["headers"] or {})
        return Settings(**values)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Settings":
        return cls().merged(data)

    def as_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


# ---------------------------------------------------------------------- 全局默认配置
_GLOBAL_OVERRIDES: dict[str, Any] = {}


def configure(**defaults: Any) -> None:
    """设置进程级全局默认配置，对之后创建的 Spider / Engine 生效。

    ``cc_crawl.configure(retry_times=3, request_interval=1.0)``

    优先级：内置默认值 < 全局配置 < Spider.custom_settings < run_spider/CLI 参数。
    """
    _GLOBAL_OVERRIDES.update(defaults)


def global_settings() -> Settings:
    """返回叠加了全局默认配置的 Settings。"""
    return Settings().merged(_GLOBAL_OVERRIDES)


def reset_global_settings() -> None:
    """清空全局默认配置（主要用于测试）。"""
    _GLOBAL_OVERRIDES.clear()


def load_settings(source: str | os.PathLike[str]) -> Settings:
    """从 TOML 文件、Python 文件或模块加载全局配置。

    支持的写法：

    - ``cc-crawl.toml``：顶层键，或 ``[cc-crawl]`` 表
    - ``settings.py`` / 模块名：模块内的大写变量，如 ``RETRY_TIMES = 3``
    """
    text = str(source)
    path = Path(text).expanduser()
    if path.suffix in {".toml", ".py"}:
        if not path.is_file():
            raise ValueError(f"找不到配置文件：{path}")
        if path.suffix == ".toml":
            import tomllib

            data = tomllib.loads(path.read_text(encoding="utf-8"))
            data = data.get("cc-crawl", data)
        else:
            data = _module_settings(_import_file(path))
    else:
        try:
            module = importlib.import_module(text)
        except ImportError as exc:
            raise ValueError(f"无法导入配置模块 {text}: {exc}") from exc
        data = _module_settings(module)

    configure(**data)
    return global_settings()


def _import_file(path: Path) -> Any:
    module_name = f"_cc_crawl_settings_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载配置文件：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _module_settings(module: Any) -> dict[str, Any]:
    return {
        name: value
        for name, value in vars(module).items()
        if name.isupper() and not name.startswith("_")
    }


def _load_from_env() -> None:
    source = os.environ.get("CC_CRAWL_SETTINGS")
    if source:
        load_settings(source)


_load_from_env()
