"""运行监控：Scrapy 风格的统计收集器。"""

from __future__ import annotations

import json
import time
import unicodedata
from typing import Any


class StatsCollector(dict):
    """收集一次爬取的运行数据。

    键名沿用 Scrapy 的习惯，例如 ``crawler/request_count``、
    ``downloader/response_status_count/200``、``item_scraped_count``，
    同时提供 ``requests`` / ``responses`` / ``item_count`` / ``retries`` /
    ``errors`` / ``duration`` 等便捷属性。
    """

    def __init__(self) -> None:
        super().__init__()
        self.start_time: float | None = None
        self.finish_time: float | None = None

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self.start_time = time.time()
        self.finish_time = None
        self.set_value("crawler/start_time", self.start_time)

    def finish(self) -> None:
        self.finish_time = time.time()
        self.set_value("crawler/finish_time", self.finish_time)
        self.set_value("crawler/elapsed_time_seconds", self.elapsed_time)

    @property
    def elapsed_time(self) -> float:
        if self.start_time is None:
            return 0.0
        return (self.finish_time or time.time()) - self.start_time

    # ------------------------------------------------------------------ 基础操作
    def get_value(self, key: str, default: Any = 0) -> Any:
        return self.get(key, default)

    def set_value(self, key: str, value: Any) -> None:
        self[key] = value

    def inc_value(self, key: str, count: float = 1, start: float = 0) -> None:
        self[key] = self.get(key, start) + count

    def max_value(self, key: str, value: float) -> None:
        self[key] = max(self.get(key, value), value)

    def min_value(self, key: str, value: float) -> None:
        self[key] = min(self.get(key, value), value)

    # ------------------------------------------------------------------ 便捷属性
    @property
    def requests(self) -> int:
        """调度入队的请求数。"""
        return int(self.get_value("crawler/request_count"))

    @property
    def duplicates(self) -> int:
        """被去重规则过滤掉的请求数。"""
        return int(self.get_value("dupefilter/filtered"))

    @property
    def attempt_count(self) -> int:
        """实际发起的下载次数（含重试）。"""
        return int(self.get_value("downloader/attempt_count"))

    @property
    def responses(self) -> int:
        """最终交给回调的响应数（重试成功的只算一次）。"""
        return int(self.get_value("downloader/response_count"))

    @property
    def item_count(self) -> int:
        return int(self.get_value("item_scraped_count"))

    @property
    def items_dropped(self) -> int:
        return int(self.get_value("item_dropped_count"))

    @property
    def pipeline_errors(self) -> int:
        return int(self.get_value("pipeline/error_count"))

    @property
    def retries(self) -> int:
        return int(self.get_value("downloader/retry_count"))

    @property
    def errors(self) -> int:
        """重试耗尽后仍失败的请求数。"""
        return int(self.get_value("downloader/exception_count"))

    @property
    def duration(self) -> float:
        return float(self.get_value("crawler/elapsed_time_seconds", self.elapsed_time))

    @property
    def status_counts(self) -> dict[int, int]:
        result: dict[int, int] = {}
        for key, value in dict.items(self):
            prefix = "downloader/response_status_count/"
            if key.startswith(prefix):
                result[int(key[len(prefix) :])] = int(value)
        return dict(sorted(result.items()))

    @property
    def success_count(self) -> int:
        return sum(count for code, count in self.status_counts.items() if 200 <= code < 300)

    @property
    def failure_count(self) -> int:
        """失败数 = 下载异常 + 非 2xx 响应。"""
        non_2xx = sum(count for code, count in self.status_counts.items() if not 200 <= code < 300)
        return self.errors + non_2xx

    @property
    def avg_latency(self) -> float:
        total = float(self.get_value("downloader/download_latency_total_seconds"))
        return total / self.responses if self.responses else 0.0

    @property
    def max_latency(self) -> float:
        return float(self.get_value("downloader/download_latency_max_seconds"))

    @property
    def responses_per_minute(self) -> float:
        minutes = self.elapsed_time / 60
        return self.responses / minutes if minutes > 0 else 0.0

    @property
    def items_per_minute(self) -> float:
        minutes = self.elapsed_time / 60
        return self.item_count / minutes if minutes > 0 else 0.0

    # ------------------------------------------------------------------ 展示
    def to_dict(self) -> dict[str, Any]:
        """结构化统计，便于 JSON 日志或写入监控系统。"""
        return {
            "elapsed_seconds": round(self.elapsed_time, 3),
            "requests": self.requests,
            "duplicates": self.duplicates,
            "attempts": self.attempt_count,
            "responses": self.responses,
            "success": self.success_count,
            "failure": self.failure_count,
            "success_rate": round(self.success_rate, 2),
            "status_counts": {
                str(code): count for code, count in self.status_counts.items()
            },
            "retries": self.retries,
            "errors": self.errors,
            "items": self.item_count,
            "items_dropped": self.items_dropped,
            "pipeline_errors": self.pipeline_errors,
            "validation_failed": int(self.get_value("spider/validation_failed")),
            "failed_requests": int(self.get_value("spider/failed_requests")),
            "parse_errors": int(self.get_value("spider/parse_error_count")),
            "spider_retries": int(self.get_value("spider/retry_count")),
            "avg_latency_seconds": round(self.avg_latency, 4),
            "max_latency_seconds": round(self.max_latency, 4),
            "responses_per_minute": round(self.responses_per_minute, 2),
            "items_per_minute": round(self.items_per_minute, 2),
        }

    def to_json(self, **kwargs: Any) -> str:
        """把统计输出为 JSON 字符串（默认单行）。"""
        kwargs.setdefault("ensure_ascii", False)
        return json.dumps(self.to_dict(), **kwargs)

    def brief(self) -> str:
        """周期日志用的一行摘要。"""
        return (
            f"运行中 {_fmt_duration(self.elapsed_time)} │ "
            f"请求 {self.requests} │ "
            f"响应 {self.responses}（成功 {self.success_count} / 失败 {self.failure_count}） │ "
            f"Item {self.item_count} │ 重试 {self.retries} │ "
            f"{self.responses_per_minute / 60:.1f} 响应/秒"
        )

    def summary(self, title: str = "运行统计") -> str:
        """爬取结束时的对齐统计表格。"""
        rows = [
            ("运行时长", _fmt_duration(self.elapsed_time)),
            (
                "请求",
                f"{self.requests} 个（实际尝试 {self.attempt_count} 次"
                + (f"，去重 {self.duplicates} 个" if self.duplicates else "")
                + "）",
            ),
            ("响应", f"{self.responses} 个（成功 {self.success_count} / 失败 {self.failure_count}）"),
            ("成功率", f"{self.success_rate:.1f}%"),
        ]
        if self.status_counts:
            rows.append(
                (
                    "状态码",
                    "  ".join(f"{code}={count}" for code, count in self.status_counts.items()),
                )
            )
        rows.extend(
            [
                ("重试", f"{self.retries} 次"),
                ("下载错误", f"{self.errors} 次"),
                ("Item", f"{self.item_count} 条"),
            ]
        )
        if self.items_dropped:
            rows.append(("丢弃 Item", f"{self.items_dropped} 条"))
        if self.pipeline_errors:
            rows.append(("管道错误", f"{self.pipeline_errors} 次"))
        rows.extend(
            [
                ("下载耗时", f"平均 {self.avg_latency:.3f}s / 最长 {self.max_latency:.3f}s"),
                ("速率", f"{self.responses_per_minute:.1f} 响应/分钟 · "
                         f"{self.items_per_minute:.1f} Item/分钟"),
            ]
        )
        return _render_box(title, rows)

    @property
    def success_rate(self) -> float:
        """成功率（百分比），以最终响应数为分母。"""
        return self.success_count / self.responses * 100 if self.responses else 0.0


def _display_width(text: str) -> int:
    """按终端显示宽度计算字符串长度（中文等宽字符按 2 列）。"""
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m{rest:04.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h{int(minutes):02d}m{rest:04.1f}s"


def _render_box(title: str, rows: list[tuple[str, str]]) -> str:
    """渲染一个等宽对齐的方框表格，兼容中文宽字符。"""
    label_width = max(_display_width(label) for label, _ in rows)
    value_width = max(_display_width(value) for _, value in rows)
    inner_width = label_width + value_width + 5

    remaining = max(0, inner_width - _display_width(title) - 3)
    lines = [f"┌─ {title} " + "─" * remaining + "┐"]
    for label, value in rows:
        lines.append(f"│ {_pad(label, label_width)} │ {_pad(value, value_width)} │")
    lines.append("└" + "─" * inner_width + "┘")
    return "\n".join(lines)
