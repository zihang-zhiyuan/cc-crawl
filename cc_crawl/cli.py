"""命令行入口：``python -m cc_crawl run 模块:Spider类``。"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
import signal
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .engine import run_spider
from .exceptions import SpiderNotFound
from .registry import SpiderRegistry
from .settings import load_settings
from .spider import Spider


def load_spider(target: str) -> type[Spider]:
    """支持 ``package.module:SpiderClass`` 和 ``path/to/file.py:SpiderClass`` 两种写法。"""
    module_ref, separator, class_name = target.rpartition(":")
    if not separator or not module_ref or not class_name:
        raise SpiderNotFound(f"目标格式应为 module:Class 或 path/to/file.py:Class，收到 {target!r}")

    if module_ref.endswith(".py") or "/" in module_ref or "\\" in module_ref:
        path = Path(module_ref).expanduser().resolve()
        if not path.is_file():
            raise SpiderNotFound(f"找不到爬虫文件：{path}")
        if str(path.parent) not in sys.path:
            sys.path.insert(0, str(path.parent))
        module_name = f"_cc_crawl_spider_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise SpiderNotFound(f"无法加载爬虫文件：{path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(module_ref)
        except ImportError as exc:
            raise SpiderNotFound(f"无法导入模块 {module_ref}: {exc}") from exc

    spider_cls = getattr(module, class_name, None)
    if spider_cls is None:
        raise SpiderNotFound(f"模块 {module_ref} 中没有 {class_name}")
    if not (isinstance(spider_cls, type) and issubclass(spider_cls, Spider)):
        raise SpiderNotFound(f"{class_name} 不是 Spider 的子类")
    return spider_cls


def _build_overrides(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if args.concurrency is not None:
        overrides["concurrency"] = args.concurrency
    if args.timeout is not None:
        overrides["timeout"] = args.timeout
    if args.impersonate:
        overrides["impersonate"] = args.impersonate
    if args.proxy:
        overrides["proxy"] = args.proxy
    if args.no_verify:
        overrides["verify"] = False
    elif args.verify:
        overrides["verify"] = True
    if args.retry_times is not None:
        overrides["retry_times"] = args.retry_times
    if args.retry_backoff is not None:
        overrides["retry_backoff"] = args.retry_backoff
    if args.request_interval is not None:
        overrides["request_interval"] = args.request_interval
    if args.no_dedupe:
        overrides["dupefilter"] = False
    if args.retry_status_codes is not None:
        overrides["retry_status_codes"] = tuple(
            int(code) for code in args.retry_status_codes.split(",") if code.strip()
        )
    if args.stats_interval is not None:
        overrides["stats_interval"] = args.stats_interval
    if args.stats_format:
        overrides["stats_format"] = args.stats_format
    if args.log_level:
        overrides["log_level"] = args.log_level
    if args.log_file:
        overrides["log_file"] = args.log_file
    return overrides


def _parse_arg_value(raw: str) -> Any:
    """把命令行里的值转成 Python 值：数字/布尔/JSON 走 json，其余按字符串。"""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _build_spider_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """解析 -a KEY=VALUE，原样传给 Spider 构造函数。"""
    kwargs: dict[str, Any] = {}
    for item in args.spider_arg or []:
        key, separator, value = item.partition("=")
        if not separator or not key:
            raise ValueError(f"参数格式应为 KEY=VALUE，收到 {item!r}")
        kwargs[key] = _parse_arg_value(value)
    return kwargs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cc-crawl", description="curl_cffi 异步爬虫框架")
    parser.add_argument("--version", action="version", version=f"cc-crawl {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="运行一个爬虫")
    run_parser.add_argument("target", help="module:Class 或 path/to/file.py:Class")
    run_parser.add_argument("-c", "--concurrency", type=int, help="并发数，默认 8")
    run_parser.add_argument("-t", "--timeout", type=float, help="单请求超时秒数")
    run_parser.add_argument("--impersonate", help="浏览器指纹，如 chrome / chrome124 / safari")
    run_parser.add_argument("--proxy", help="代理地址，如 http://127.0.0.1:7890")
    verify_group = run_parser.add_mutually_exclusive_group()
    verify_group.add_argument(
        "--verify", action="store_true", help="开启 HTTPS 证书校验（框架默认关闭）"
    )
    verify_group.add_argument(
        "--no-verify", action="store_true", help="跳过 HTTPS 证书校验（默认即关闭）"
    )
    run_parser.add_argument(
        "--retry-times", type=int, help="下载失败重试次数（不含首次），0 表示不重试"
    )
    run_parser.add_argument("--retry-backoff", type=float, help="重试基础等待秒数")
    run_parser.add_argument("--request-interval", type=float, help="每次请求之间的最小间隔秒数")
    run_parser.add_argument(
        "--no-dedupe", action="store_true", help="关闭请求去重（默认开启）"
    )
    run_parser.add_argument(
        "--retry-status-codes",
        help="命中这些状态码时重试，逗号分隔，如 429,500,503；传空字符串表示不按状态码重试",
    )
    run_parser.add_argument("--log-level", help="DEBUG / INFO / WARNING / ERROR")
    run_parser.add_argument("--log-file", help="日志文件路径，按 10MB 切分")
    run_parser.add_argument(
        "--stats-interval", type=float, help="运行统计的周期日志间隔秒数，0 表示关闭"
    )
    run_parser.add_argument(
        "--stats-format",
        choices=("json", "text"),
        help="运行统计输出格式：json（默认，单行 JSON）或 text（方框表格）",
    )
    run_parser.add_argument("--json", action="store_true", help="以 JSON Lines 打印抓取结果")
    run_parser.add_argument(
        "--settings",
        metavar="PATH_OR_MODULE",
        help="全局配置文件（.toml / .py）或模块名，如 settings.py",
    )
    run_parser.add_argument(
        "-a",
        "--spider-arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="传给 Spider 构造函数的参数，可重复，如 -a keyword=python -a page=2",
    )

    list_parser = subparsers.add_parser("list", help="列出目录或包里的爬虫")
    list_parser.add_argument("target", help="目录 / .py 文件 / 包名")
    list_parser.add_argument("--no-recursive", action="store_true", help="只扫描当前目录")
    return parser


def _list_spiders(args: argparse.Namespace) -> int:
    try:
        registry = SpiderRegistry.discover(
            args.target, recursive=not args.no_recursive
        )
    except (SpiderNotFound, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    for name, spider_cls in registry.items():
        source = inspect.getsourcefile(spider_cls) or spider_cls.__module__
        print(f"{name}\t{source}")
    print(f"共 {len(registry)} 个爬虫", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "list":
        return _list_spiders(args)

    # 容器/进程管理器通常发 SIGTERM：转成 KeyboardInterrupt，确保 Engine 的 finally 收尾
    try:
        signal.signal(signal.SIGTERM, signal.default_int_handler)
    except (ValueError, OSError):  # pragma: no cover - 平台差异
        pass

    try:
        spider_cls = load_spider(args.target)
    except SpiderNotFound as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if args.settings:
        try:
            load_settings(args.settings)
        except ValueError as exc:
            parser.error(str(exc))

    try:
        spider_kwargs = _build_spider_kwargs(args)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        items = run_spider(spider_cls, settings=_build_overrides(args), **spider_kwargs)
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130

    if args.json:
        for item in items:
            print(json.dumps(item, ensure_ascii=False, default=str))
    else:
        for item in items:
            print(item)
    print(f"共抓取 {len(items)} 条", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
