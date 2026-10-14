"""监控主循环。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from .config import Config
from .display import Dashboard
from .notify import NotifierHub
from .router import SourceManager
from .sources import Quote, SourceError
from .params import sync_runtime
from .store import HistoryStore


class Monitor:
    """按固定间隔轮询金价，落盘、刷新面板、处理数据源事件与定时播报。"""

    def __init__(
        self,
        config: Config,
        manager: SourceManager,
        history: HistoryStore,
        dashboard: Optional[Dashboard] = None,
        notifier: Optional[NotifierHub] = None,
        reporter=None,
        level_service=None,
        volatility_service=None,
        config_path=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self.manager = manager
        self.history = history
        self.dashboard = dashboard
        self.notifier = notifier or NotifierHub()
        self.reporter = reporter
        self.level_service = level_service
        self.volatility_service = volatility_service
        self.logger = logger or logging.getLogger("goldalert.monitor")
        self.config_path = Path(config_path) if config_path else None
        self._config_mtime = self._stat_config()

    # ------------------------------------------------------------------ 运行
    def run(self, max_cycles: Optional[int] = None) -> int:
        """启动监控循环，返回实际执行的轮数。"""
        if self.dashboard is not None:
            self.dashboard.start()
        self._announce_start()

        cycles = 0
        stopped_by_user = False
        try:
            while max_cycles is None or cycles < max_cycles:
                self._cycle()
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    break
                self._wait(self.config.interval_seconds)
        except KeyboardInterrupt:
            stopped_by_user = True
            self.logger.info("收到停止指令，正在退出 …")
        finally:
            if self.dashboard is not None:
                self.dashboard.stop()
        if stopped_by_user:
            print("监控已停止。")
        return cycles

    def fetch_once(self) -> Quote:
        """只取一次行情（供 ``once`` 命令使用）。"""
        return self.manager.fetch(self.config.symbol)

    # ------------------------------------------------------------ 配置热更新
    def _stat_config(self):
        if self.config_path is None:
            return None
        try:
            return self.config_path.stat().st_mtime
        except OSError:
            return None

    def reload_config_if_changed(self) -> bool:
        """config.json 被设置页改过就重新读一次，就地更新运行参数。"""
        if self.config_path is None:
            return False
        mtime = self._stat_config()
        if mtime is None or mtime == self._config_mtime:
            return False
        self._config_mtime = mtime
        try:
            new = Config.load(self.config_path)
        except Exception as exc:
            self.logger.warning("重新读取 config.json 失败，继续用旧配置: %s", exc)
            return False
        try:
            sync_runtime(self.config, new, level_service=self.level_service, logger=self.logger)
        except Exception as exc:
            self.logger.warning("应用新配置失败，继续用旧配置: %s", exc)
            return False
        return True

    # ------------------------------------------------------------ 单轮逻辑
    def _cycle(self) -> None:
        self.reload_config_if_changed()
        self.manager.check_proxy()
        if self.dashboard is not None:
            self.dashboard.set_status(self.manager.status_line())

        try:
            quote = self.manager.fetch(self.config.symbol)
        except SourceError as exc:
            self.logger.warning("本轮取数失败: %s", exc)
            if self.dashboard is not None:
                self.dashboard.set_error(str(exc))
                self.dashboard.refresh()
            return

        self.history.append(quote)
        if self.dashboard is not None:
            self.dashboard.record(quote)
            self.dashboard.refresh()

        pct = f"{quote.change_pct:+.2f}%" if quote.change_pct is not None else "—"
        self.logger.info(
            "%s %.2f 美元/盎司  %s  数据源=%s", quote.symbol, quote.price, pct, quote.source
        )

        if self.reporter is not None:
            try:
                self.reporter.maybe_send(quote)
            except Exception as exc:  # 播报失败不应中断监控
                self.logger.warning("定时播报发送失败: %s", exc)

        if self.level_service is not None:
            try:
                self.level_service.on_quote(quote)
            except Exception as exc:  # 价位提醒失败同样不应中断监控
                self.logger.warning("关键价位检查失败: %s", exc)

        if self.volatility_service is not None:
            try:
                self.volatility_service.on_quote(quote)
            except Exception as exc:  # 异常波动提醒失败也不该中断监控
                self.logger.warning("异常波动检查失败: %s", exc)

    def _wait(self, seconds: float, tick: float = 0.5) -> None:
        """等待下一轮，期间刷新倒计时。"""
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self.dashboard is not None:
                self.dashboard.refresh(remaining)
            time.sleep(min(tick, remaining))

    def _announce_start(self) -> None:
        sources = ", ".join(s.describe() for s in self.manager.sources)
        self.logger.info(
            "监控启动：%s，间隔 %.0f 秒，数据源优先级 %s",
            self.config.symbol,
            self.config.interval_seconds,
            sources,
        )
        if self.reporter is not None and self.reporter.config.enabled:
            self.logger.info(
                "定时播报已开启：每 %d 分钟%s",
                self.reporter.config.every_minutes,
                "（整点对齐）" if self.reporter.config.align_to_clock else "",
            )
        if self.level_service is not None:
            settings = self.level_service.store.settings
            levels = self.level_service.store.all_levels()
            if settings.enabled and levels:
                self.logger.info(
                    "关键价位提醒已开启：%d 个价位，价格进入 %.1f 美元内提醒，突破也会提醒",
                    len(levels),
                    settings.proximity_usd,
                )
            else:
                self.logger.info("关键价位提醒未生效（没有价位或总开关关闭）")
        if self.volatility_service is not None:
            settings = self.volatility_service.settings
            if settings.enabled:
                self.logger.info(
                    "异常波动提醒已开启：%.0f 分钟内最高最低差 ≥ %.2f 美元就提醒",
                    settings.window_minutes,
                    settings.threshold_usd,
                )
            else:
                self.logger.info("异常波动提醒未开启")
