"""日志：默认使用 loguru。"""

from __future__ import annotations

import sys

from loguru import logger

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{file.name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

_configured = False


def _has_user_handlers() -> bool:
    """判断用户是否已经自行给 loguru 添加过 sink（排除 loguru 自带的 0 号 handler）。"""
    core = getattr(logger, "_core", None)
    handlers = getattr(core, "handlers", None) or {}
    return any(handler_id != 0 for handler_id in handlers)


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    rotation: str = "10 MB",
    force: bool = False,
) -> None:
    """配置 loguru 日志。

    默认输出到 stderr；传 ``log_file`` 时同时写文件并按 ``rotation`` 切分。

    - 重复调用不会覆盖已有配置
    - 如果调用前已经用 ``logger.add(...)`` 自定义过 sink，则尊重用户配置，不再改动
    - 传 ``force=True`` 会清空所有 sink 后重新配置
    """
    global _configured
    if _configured and not force:
        return
    if not force and _has_user_handlers():
        _configured = True
        return

    logger.remove()
    logger.add(sys.stderr, level=level.upper(), format=_FORMAT, colorize=True)
    if log_file:
        logger.add(
            log_file,
            level=level.upper(),
            format=_FORMAT,
            rotation=rotation,
            encoding="utf-8",
            enqueue=True,
        )
    _configured = True


__all__ = ["logger", "setup_logging"]
