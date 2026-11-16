"""核心对象（Item / Request / Response / Settings / CLI 加载）的单元测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cc_crawl import (
    Item,
    Request,
    Response,
    Settings,
    Spider,
    StatsCollector,
    configure,
    global_settings,
    load_settings,
    logger,
)
from cc_crawl.cli import _build_overrides, _build_spider_kwargs, build_parser, load_spider
from cc_crawl.downloader import Downloader
from cc_crawl.middlewares import DownloaderMiddleware, load_middleware
from cc_crawl.settings import reset_global_settings
from cc_crawl.stats import _display_width


def test_item_supports_dict_and_attribute_access():
    item = Item(title="hello")
    item.score = 10
    item["url"] = "https://example.com"

    assert item["title"] == "hello"
    assert item.score == 10
    assert item.url == "https://example.com"
    assert "title='hello'" in repr(item)
    with pytest.raises(AttributeError):
        _ = item.missing


def test_request_replace_keeps_original_untouched():
    request = Request("https://example.com/a", meta={"page": 1})

    other = request.replace(url="https://example.com/b")

    assert request.url == "https://example.com/a"
    assert other.url == "https://example.com/b"
    assert other.meta == {"page": 1}
    assert "GET https://example.com/a" in repr(request)


def build_response(url="https://example.com/list/page.html") -> Response:
    request = Request(url, meta={"page": 2})
    return Response(
        url=url,
        status_code=200,
        headers={"Content-Type": "text/html"},
        content=b"<html><title>demo</title></html>",
        text="<html><title>demo</title></html>",
        request=request,
    )


def test_response_follow_resolves_relative_url_and_inherits_meta():
    response = build_response()
    callback = object()

    request = response.follow("../detail/1.html", callback=callback)

    assert request.url == "https://example.com/detail/1.html"
    assert request.meta == {"page": 2}
    assert request.callback is callback
    assert response.urljoin("/root") == "https://example.com/root"


def test_response_follow_supports_priority():
    request = build_response().follow("/next", priority=100)

    assert request.priority == 100


def test_response_json_and_ok():
    request = Request("https://api.example.com/data")
    response = Response(
        url=request.url,
        status_code=200,
        headers={},
        content=b'{"ok": true}',
        text='{"ok": true}',
        request=request,
    )

    assert response.ok is True
    assert response.json() == {"ok": True}


def test_response_css_and_xpath():
    pytest.importorskip("parsel")
    response = build_response()

    assert response.css("title::text").get() == "demo"
    assert response.xpath("//title/text()").get() == "demo"


def test_response_from_curl_honours_request_encoding():
    raw = SimpleNamespace(
        url="https://example.com/",
        status_code=200,
        headers={},
        content="中文".encode("gbk"),
        text="乱码",
    )
    request = Request("https://example.com/", encoding="gbk")

    response = Response.from_curl(raw, request)

    assert response.text == "中文"


def test_settings_merge_is_case_insensitive_and_ignores_unknown_keys():
    settings = Settings().merged({"CONCURRENCY": 3, "timeout": 5, "unknown": "x"})

    assert settings.concurrency == 3
    assert settings.timeout == 5
    assert settings.impersonate == "chrome"


def test_settings_retry_and_interval():
    settings = Settings().merged(
        {
            "RETRY_TIMES": 5,
            "retry_backoff": 0.2,
            "retry_status_codes": [500, 502],
            "request_interval": 1.5,
        }
    )

    assert settings.retry_times == 5
    assert settings.retry_backoff == 0.2
    assert settings.retry_status_codes == (500, 502)
    assert settings.request_interval == 1.5


def test_settings_clamps_negative_retry_and_interval():
    settings = Settings(retry_times=-1, request_interval=-2)

    assert settings.retry_times == 0
    assert settings.request_interval == 0.0


def test_settings_verify_defaults_to_false():
    assert Settings().verify is False


def test_cli_can_enable_verify():
    args = build_parser().parse_args(["run", "x:Y", "--verify"])

    assert _build_overrides(args)["verify"] is True


def test_cli_can_switch_stats_format():
    args = build_parser().parse_args(["run", "x:Y", "--stats-format", "text"])

    assert _build_overrides(args) == {"stats_format": "text"}


def test_spider_requires_name():
    class NamelessSpider(Spider):
        pass

    with pytest.raises(ValueError):
        NamelessSpider()


def test_spider_exposes_loguru_logger():
    class LogSpider(Spider):
        name = "log"

    assert LogSpider().logger is logger


def test_load_spider_from_file_path():
    path = Path(__file__).resolve().parents[1] / "examples" / "demo_spider.py"
    spider_cls = load_spider(f"{path}:QuotesSpider")

    assert issubclass(spider_cls, Spider)
    assert spider_cls.name == "quotes"


def test_cli_parses_spider_args():
    args = build_parser().parse_args(
        ["run", "x:Y", "-a", "keyword=python", "-a", "page=2", "-a", "flag=true"]
    )

    assert _build_spider_kwargs(args) == {"keyword": "python", "page": 2, "flag": True}

    with pytest.raises(ValueError):
        _build_spider_kwargs(build_parser().parse_args(["run", "x:Y", "-a", "oops"]))


def test_cli_builds_retry_and_interval_overrides():
    args = build_parser().parse_args(
        [
            "run",
            "x:Y",
            "--retry-times",
            "3",
            "--retry-backoff",
            "0.5",
            "--request-interval",
            "0.2",
            "--retry-status-codes",
            "429,500",
        ]
    )

    assert _build_overrides(args) == {
        "retry_times": 3,
        "retry_backoff": 0.5,
        "request_interval": 0.2,
        "retry_status_codes": (429, 500),
    }


def test_stats_collector_records_metrics_and_summary():
    stats = StatsCollector()
    stats.start()
    stats.inc_value("crawler/request_count", 2)
    stats.inc_value("downloader/attempt_count", 3)
    stats.inc_value("downloader/response_count", 2)
    stats.inc_value("downloader/response_status_count/200", 1)
    stats.inc_value("downloader/response_status_count/404", 1)
    stats.inc_value("downloader/retry_count", 1)
    stats.inc_value("downloader/exception_count", 1)
    stats.inc_value("item_scraped_count", 5)
    stats.inc_value("downloader/download_latency_total_seconds", 0.4)
    stats.max_value("downloader/download_latency_max_seconds", 0.3)
    stats.finish()

    assert stats.requests == 2
    assert stats.attempt_count == 3
    assert stats.responses == 2
    assert stats.item_count == 5
    assert stats.retries == 1
    assert stats.errors == 1
    assert stats.success_count == 1
    assert stats.failure_count == 2
    assert round(stats.avg_latency, 3) == 0.2

    summary = stats.summary()
    assert "运行时长" in summary
    assert "200=1" in summary
    assert "404=1" in summary

    payload = json.loads(stats.to_json())
    assert payload["requests"] == 2
    assert payload["success"] == 1
    assert payload["failure"] == 2
    assert payload["status_counts"] == {"200": 1, "404": 1}
    assert payload["items"] == 5
    assert payload["items_dropped"] == 0
    assert payload["pipeline_errors"] == 0


def test_stats_summary_box_is_aligned():
    stats = StatsCollector()
    stats.start()
    stats.inc_value("downloader/response_count", 2)
    stats.inc_value("downloader/response_status_count/200", 2)
    stats.finish()

    lines = stats.summary().splitlines()

    assert lines[0].startswith("┌")
    assert lines[-1].startswith("└")
    assert len({_display_width(line) for line in lines}) == 1


def test_load_middleware_accepts_instance_class_and_path():
    instance = DownloaderMiddleware()

    assert load_middleware(instance) is instance
    assert isinstance(load_middleware(DownloaderMiddleware), DownloaderMiddleware)
    assert isinstance(
        load_middleware("cc_crawl.middlewares:DownloaderMiddleware"), DownloaderMiddleware
    )


def test_settings_accepts_middleware_aliases():
    class Middleware:
        pass

    for key in ("DOWNLOADER_MIDDLEWARES", "download_middlewares", "download_middle"):
        settings = Settings().merged({key: [Middleware]})
        assert settings.downloader_middlewares == [Middleware]


def test_settings_accepts_pipeline_aliases():
    class Pipeline:
        pass

    for key in ("ITEM_PIPELINES", "item_pipelines", "pipelines", "pipeline"):
        settings = Settings().merged({key: [Pipeline]})
        assert settings.item_pipelines == [Pipeline]


def test_configure_sets_global_defaults():
    reset_global_settings()
    try:
        configure(retry_times=5, request_interval=0.3)

        class GlobalSpider(Spider):
            name = "global"
            custom_settings = {"concurrency": 2}

        spider = GlobalSpider()
        assert spider.settings.retry_times == 5
        assert spider.settings.request_interval == 0.3
        assert spider.settings.concurrency == 2
        assert global_settings().retry_times == 5

        class OverrideSpider(Spider):
            name = "override"
            custom_settings = {"retry_times": 1}

        assert OverrideSpider().settings.retry_times == 1
    finally:
        reset_global_settings()


def test_load_settings_from_toml():
    pytest.importorskip("tomllib")
    reset_global_settings()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cc-crawl.toml"
            path.write_text(
                "[cc-crawl]\nRETRY_TIMES = 4\nrequest_interval = 0.5\n",
                encoding="utf-8",
            )
            settings = load_settings(path)

        assert settings.retry_times == 4
        assert settings.request_interval == 0.5

        class TomlSpider(Spider):
            name = "toml"

        assert TomlSpider().settings.retry_times == 4
    finally:
        reset_global_settings()


def test_load_settings_from_python_file():
    reset_global_settings()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.py"
            path.write_text(
                "RETRY_TIMES = 6\nREQUEST_INTERVAL = 0.25\nCONCURRENCY = 3\n",
                encoding="utf-8",
            )
            settings = load_settings(path)

        assert settings.retry_times == 6
        assert settings.request_interval == 0.25
        assert settings.concurrency == 3
    finally:
        reset_global_settings()


def test_settings_precedence_global_custom_then_run():
    reset_global_settings()
    try:
        configure(retry_times=5, concurrency=1)

        class PrioritySpider(Spider):
            name = "priority"
            custom_settings = {"retry_times": 2}

        spider = PrioritySpider()
        assert spider.settings.retry_times == 2  # custom_settings 覆盖全局
        assert spider.settings.concurrency == 1  # 未设置时继承全局

        effective = spider.settings.merged({"retry_times": 1})
        assert effective.retry_times == 1  # 运行参数覆盖 custom_settings
    finally:
        reset_global_settings()


def test_request_level_values_override_settings():
    settings = Settings(
        timeout=10,
        proxy="http://proxy-a",
        verify=False,
        impersonate="chrome",
    )
    request = Request(
        "https://example.com",
        timeout=3,
        proxy="http://proxy-b",
        verify=True,
        impersonate="safari",
        headers={"X-Test": "1"},
    )

    kwargs = Downloader(settings)._request_kwargs(request)

    assert kwargs["timeout"] == 3
    assert kwargs["proxy"] == "http://proxy-b"
    assert kwargs["verify"] is True
    assert kwargs["impersonate"] == "safari"
    assert kwargs["headers"] == {"X-Test": "1"}


def test_request_fingerprint_is_stable_and_ignores_callback():
    first = Request(
        "https://example.com/list",
        params={"page": 2, "tag": ["a", "b"]},
        callback=print,
        session="one",
    )
    second = Request(
        "https://example.com/list",
        params={"tag": ["a", "b"], "page": 2},
        callback=str,
        session="one",
    )

    assert first.fingerprint() == second.fingerprint()
    assert (
        first.fingerprint()
        != Request(
            "https://example.com/list",
            params={"page": 2, "tag": ["a", "b"]},
            session="two",
        ).fingerprint()
    )
    assert first.fingerprint() != Request("https://example.com/list", params={"page": 3}).fingerprint()
    assert (
        Request("https://example.com", method="POST", json={"a": 1}).fingerprint()
        != Request("https://example.com", method="POST", json={"a": 2}).fingerprint()
    )


def test_callback_signature_detection():
    from cc_crawl.engine import _accepts_request

    class Handler:
        def feapder_style(self, request, response):
            pass

        def scrapy_style(self, response):
            pass

        def single_request_arg(self, request):
            pass

        def two_odd_names(self, foo, bar):
            pass

    handler = Handler()
    assert _accepts_request(handler.feapder_style) is True
    assert _accepts_request(handler.scrapy_style) is False
    # 单参数一律 Scrapy 风格，参数名不参与判断
    assert _accepts_request(handler.single_request_arg) is False
    # 两个参数一律按 (request, response)
    assert _accepts_request(handler.two_odd_names) is True


def test_response_follow_does_not_inherit_dont_filter():
    request = Request(
        "https://example.com/list",
        callback=print,
        meta={"depth": 1},
        priority=5,
        session="one",
        dont_filter=True,
    )
    response = Response(
        url=request.url,
        status_code=200,
        headers={},
        content=b"",
        text="",
        request=request,
    )

    followed = response.follow("/detail")

    assert followed.url == "https://example.com/detail"
    assert followed.dont_filter is False
    assert followed.meta == {"depth": 1}
    assert followed.priority == 5
    assert followed.session == "one"


def test_request_fingerprint_ignores_url_fragment():
    assert (
        Request("https://example.com/page#top").fingerprint()
        == Request("https://example.com/page").fingerprint()
    )
