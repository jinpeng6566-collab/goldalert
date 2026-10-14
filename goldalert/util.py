"""通用工具：北京时间、日志、路径处理、网络探测。"""

from __future__ import annotations

import logging
import logging.handlers
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

CN_TZ = timezone(timedelta(hours=8), "CST")


def now_cn() -> datetime:
    """返回北京时间。"""
    return datetime.now(CN_TZ)


def fmt_dt(value: datetime) -> str:
    """格式化为 ``YYYY-MM-DD HH:MM:SS``。"""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def fmt_ts(epoch: float) -> str:
    """把 Unix 时间戳格式化为北京时间字符串。"""
    return fmt_dt(datetime.fromtimestamp(epoch, CN_TZ))


def ensure_parent(path: str | os.PathLike[str]) -> Path:
    """确保文件所在目录存在，并返回 Path。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def parse_proxy(url: str | None) -> tuple[str, int] | None:
    """把 ``http://127.0.0.1:7890`` 解析成 ``("127.0.0.1", 7890)``。"""
    if not url:
        return None
    parts = urlsplit(url if "://" in url else f"http://{url}")
    host = parts.hostname
    if not host:
        return None
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return host, port


def probe_tcp(host: str, port: int, timeout: float = 3.0) -> bool:
    """探测 TCP 端口是否可连接（用于判断代理是否还活着）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_utf8_streams() -> None:
    """把标准输出/标准错误切到 UTF-8，避免在 GBK 控制台下打印符号时报错。

    后台静默运行（pythonw）时两个流都是 None，这里直接跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def setup_logging(
    log_file: str | None,
    level: int = logging.INFO,
    quiet: bool = False,
) -> logging.Logger:
    """配置并返回 goldalert 的根 logger。

    参数:
        log_file: 日志文件路径；为 ``None`` 时只输出到控制台。
        quiet: 为 ``True`` 时不输出到控制台（实时仪表盘独占终端时使用）。
    """
    logger = logging.getLogger("goldalert")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    if log_file:
        handler = logging.handlers.RotatingFileHandler(
            ensure_parent(log_file),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # 后台静默运行时（pythonw）没有控制台，stdout/stderr 为 None，需跳过
    if not quiet and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        logger.addHandler(stream)

    return logger