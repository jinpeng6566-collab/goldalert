"""数据源调度：主源失败自动回退，并对"代理断开 / 数据源切换"发出通知。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from .notify import NotifierHub
from .sources import PriceSource, Quote, SourceError
from .util import parse_proxy, probe_tcp


@dataclass
class SourceStat:
    """单个数据源的运行统计。"""

    name: str
    ok_count: int = 0
    fail_count: int = 0
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    last_ok_at: Optional[float] = None


class SourceManager:
    """按优先级调度多个数据源，并在状态变化时通知。"""

    def __init__(
        self,
        sources: list[PriceSource],
        notifier: Optional[NotifierHub] = None,
        failure_threshold: int = 2,
        proxy: Optional[str] = None,
        proxy_check_interval: float = 15.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.sources = list(sources)
        self.notifier = notifier or NotifierHub()
        self.failure_threshold = max(1, failure_threshold)
        self.proxy = proxy
        self.proxy_check_interval = proxy_check_interval
        self.logger = logger or logging.getLogger("goldalert.router")

        self.primary: Optional[str] = self.sources[0].name if self.sources else None
        self.active: Optional[str] = None
        self.stats: dict[str, SourceStat] = {s.name: SourceStat(s.name) for s in self.sources}

        self.proxy_alive: Optional[bool] = None
        self._proxy_fail_streak = 0
        self._proxy_ok_streak = 0
        self._last_proxy_check = 0.0
        self._all_failed_streak = 0

    # ------------------------------------------------------------------ 取数
    def fetch(self, symbol: str) -> Quote:
        """依次尝试各数据源，返回第一个成功的结果。"""
        errors: list[tuple[str, Exception]] = []
        for source in self.sources:
            try:
                quote = source.fetch(symbol)
            except SourceError as exc:
                self._record_failure(source.name, exc)
                errors.append((source.name, exc))
                continue
            self._record_success(source.name)
            self._switch_to(source.name)
            self._all_failed_streak = 0
            return quote

        self._on_all_failed(errors)
        detail = "; ".join(f"{name}: {exc}" for name, exc in errors)
        raise SourceError(f"所有数据源均失败 -> {detail}")

    def _record_success(self, name: str) -> None:
        stat = self.stats[name]
        stat.ok_count += 1
        stat.consecutive_failures = 0
        stat.last_error = None
        stat.last_ok_at = time.time()

    def _record_failure(self, name: str, exc: Exception) -> None:
        stat = self.stats[name]
        stat.fail_count += 1
        stat.consecutive_failures += 1
        stat.last_error = str(exc)
        self.logger.debug(
            "数据源 %s 失败（连续 %d 次）: %s", name, stat.consecutive_failures, exc
        )

    # ------------------------------------------------------------ 状态切换
    def _switch_to(self, name: str) -> None:
        previous = self.active
        if previous == name:
            return
        self.active = name

        if previous is None:
            # 首次取数：若没能用上主源，也要明确告知
            self.logger.info("已启用数据源: %s", name)
            if name != self.primary:
                reason = self.stats[self.primary].last_error if self.primary in self.stats else None
                body = f"主数据源 {self.primary} 当前不可用，监控已改用 {name}，报价继续。"
                if reason:
                    body += f"\n原因: {reason}"
                self._notify("🔀 已使用备用数据源", body, "warning")
            return

        if name == self.primary:
            self._notify(
                "✅ 已恢复主数据源",
                f"已从 {previous} 切回主数据源 {name}，报价通道恢复正常。",
                "info",
            )
            return

        reason = self.stats[previous].last_error if previous in self.stats else None
        body = f"数据源 {previous} 不可用，已自动切换到 {name}，监控未中断。"
        if reason:
            body += f"\n原因: {reason}"
        self._notify("🔀 已切换到备用数据源", body, "warning")

    def _on_all_failed(self, errors: list[tuple[str, Exception]]) -> None:
        self._all_failed_streak += 1
        detail = "; ".join(f"{name}: {exc}" for name, exc in errors) or "无可用数据源"
        self.logger.error("全部数据源取数失败: %s", detail)
        streak = self._all_failed_streak
        if streak == self.failure_threshold or streak % 30 == 0:
            self._notify(
                "🚨 所有数据源均不可用",
                f"已连续 {streak} 次取不到金价，实时监控暂时中断。\n{detail}",
                "critical",
            )

    # ------------------------------------------------------------ 代理检查
    def check_proxy(self, force: bool = False) -> Optional[bool]:
        """定期探测代理端口；返回当前是否可用（未配置代理时返回 None）。"""
        if not self.proxy:
            return None
        now = time.time()
        if not force and (now - self._last_proxy_check) < self.proxy_check_interval:
            return self.proxy_alive
        self._last_proxy_check = now

        target = parse_proxy(self.proxy)
        alive = bool(target and probe_tcp(target[0], target[1], timeout=3.0))
        self._update_proxy_state(alive)
        return self.proxy_alive

    def _update_proxy_state(self, alive: bool) -> None:
        if alive:
            self._proxy_ok_streak += 1
            self._proxy_fail_streak = 0
        else:
            self._proxy_fail_streak += 1
            self._proxy_ok_streak = 0

        previous = self.proxy_alive
        if not alive and self._proxy_fail_streak >= self.failure_threshold:
            if previous is not False:
                self.proxy_alive = False
                self._notify(
                    "🚨 代理已断开",
                    f"探测不到代理 {self.proxy}（连续 {self._proxy_fail_streak} 次）。"
                    f"依赖代理的数据源会失效，已尽量切换到直连源。",
                    "critical",
                )
        elif alive and self._proxy_ok_streak >= 1:
            if previous is False:
                self._notify("✅ 代理已恢复", f"代理 {self.proxy} 连接已恢复。", "info")
            self.proxy_alive = True

    # ---------------------------------------------------------------- 辅助
    def status_line(self) -> str:
        """一行状态摘要，供仪表盘展示。"""
        proxy_text = {None: "未检测", True: "正常", False: "断开"}[self.proxy_alive]
        sources = " ".join(
            f"{'●' if self.stats[s.name].consecutive_failures == 0 else '○'}{s.name}"
            for s in self.sources
        )
        return f"{sources}  |  代理 {proxy_text}"

    def _notify(self, title: str, body: str, level: str) -> None:
        self.logger.log(
            logging.CRITICAL if level == "critical" else logging.WARNING,
            "%s | %s",
            title,
            body.replace("\n", " "),
        )
        self.notifier.send(title, body, level)