"""异常波动提醒：短时间内价格上下摆动太大就提醒。

只盯着最近 N 分钟采集到的价格，算出这段时间的最高价与最低价；
两者差值达到阈值就发一次提醒。等波动收窄回去、并且过了冷却时间，
才会重新武装，避免同一波行情把邮件刷爆。
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .sources import Quote
from .store import StateStore
from .util import fmt_dt, now_cn

STATE_KEY = "volatility"
MIN_POINTS = 3  # 样本太少时最高/最低不代表什么，先不判断


@dataclass
class VolatilityAlert:
    """一次异常波动提醒。"""

    window_minutes: float
    threshold_usd: float
    high: float
    low: float
    start_price: float
    start_ts: float
    price: float
    when: datetime = field(default_factory=now_cn)

    @property
    def span(self) -> float:
        return self.high - self.low

    @property
    def delta(self) -> float:
        return self.price - self.start_price


def format_volatility_alert(alert: VolatilityAlert, quote: Quote) -> tuple[str, str, str]:
    """把异常波动整理成 ``(标题, 正文, 级别)``。"""
    title = f"⚡ 异常波动：{alert.window_minutes:g} 分钟内 {alert.span:,.2f} 美元"
    severity = "critical" if alert.span >= alert.threshold_usd * 2 else "warning"

    if abs(alert.delta) < alert.span * 0.3:
        direction = f"来回拉锯（净变动 {alert.delta:+,.2f}）"
    elif alert.delta > 0:
        direction = f"震荡上行（较窗口起点 {alert.delta:+,.2f}）"
    else:
        direction = f"震荡下行（较窗口起点 {alert.delta:+,.2f}）"

    lines = [
        f"现货黄金 {quote.symbol}",
        f"时间       {fmt_dt(alert.when)}",
        "",
        f"窗口       {alert.window_minutes:g} 分钟"
        f"（{fmt_dt(datetime.fromtimestamp(alert.start_ts, alert.when.tzinfo))[:16]} 起）",
        f"窗口最高   {alert.high:,.2f}",
        f"窗口最低   {alert.low:,.2f}",
        f"振幅       {alert.span:,.2f} 美元（阈值 {alert.threshold_usd:,.2f}）",
        f"现价       {alert.price:,.2f} 美元/盎司",
        f"形态       {direction}",
        f"数据源     {quote.source}",
    ]
    return title, "\n".join(lines), severity


class VolatilityWatcher:
    """滑动窗口内的最高/最低差是否超过阈值。"""

    def __init__(self, state: Optional[StateStore] = None,
                 logger: Optional[logging.Logger] = None) -> None:
        self.state = state
        self.logger = logger or logging.getLogger("goldalert.volatility")
        self.points: deque[tuple[float, float]] = deque()
        self.armed = True
        self.last_alert = 0.0
        saved = state.get(STATE_KEY) if state is not None else None
        if isinstance(saved, dict):
            self.armed = bool(saved.get("armed", True))
            try:
                self.last_alert = float(saved.get("last_alert") or 0.0)
            except (TypeError, ValueError):
                self.last_alert = 0.0

    # ------------------------------------------------------------------ 内部
    def _remember(self) -> None:
        if self.state is None:
            return
        self.state.set(STATE_KEY, {"armed": self.armed, "last_alert": self.last_alert})

    def _prune(self, now_ts: float, window_seconds: float) -> None:
        cutoff = now_ts - window_seconds
        while self.points and self.points[0][0] < cutoff:
            self.points.popleft()

    def reset(self) -> None:
        """清空窗口（用于测试或手动重新开始）。"""
        self.points.clear()

    # ------------------------------------------------------------------ 判断
    def check(self, quote: Quote, settings, now: Optional[datetime] = None):
        """喂一条行情；命中异常波动时返回 ``VolatilityAlert``，否则返回 None。"""
        now = now or now_cn()
        try:
            ts = float(quote.fetched_at)
        except (TypeError, ValueError):
            ts = now.timestamp()
        self.points.append((ts, float(quote.price)))

        window_seconds = max(0.0, float(settings.window_minutes)) * 60.0
        self._prune(ts, window_seconds)
        if not settings.enabled or len(self.points) < MIN_POINTS:
            return None

        prices = [price for _, price in self.points]
        high, low = max(prices), min(prices)
        span = high - low
        threshold = float(settings.threshold_usd)
        cooldown_seconds = max(0.0, float(settings.cooldown_minutes)) * 60.0

        if not self.armed:
            calm = threshold * float(settings.rearm_ratio)
            if span <= calm and (ts - self.last_alert) >= cooldown_seconds:
                self.armed = True
                self._remember()
                self.logger.info("波动已收窄到 %.2f 美元以内，重新开始盯异常波动", span)
            return None

        if span < threshold or (ts - self.last_alert) < cooldown_seconds:
            return None

        self.armed = False
        self.last_alert = ts
        self._remember()
        return VolatilityAlert(
            window_minutes=float(settings.window_minutes),
            threshold_usd=threshold,
            high=high,
            low=low,
            start_price=self.points[0][1],
            start_ts=self.points[0][0],
            price=float(quote.price),
            when=now,
        )


class VolatilityService:
    """把异常波动判断和通知串起来，供监控主循环调用。"""

    def __init__(self, settings, notifier, state: Optional[StateStore] = None,
                 logger: Optional[logging.Logger] = None) -> None:
        self.settings = settings
        self.notifier = notifier
        self.watcher = VolatilityWatcher(state, logger)
        self.logger = logger or logging.getLogger("goldalert.volatility")

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    def on_quote(self, quote: Quote, now: Optional[datetime] = None):
        alert = self.watcher.check(quote, self.settings, now)
        if alert is None:
            return None
        title, body, severity = format_volatility_alert(alert, quote)
        self.logger.info("%s | %s", title, body.replace("\n", " "))
        try:
            self.notifier.send(title, body, severity)
        except Exception as exc:  # 通知失败不该影响监控
            self.logger.warning("异常波动提醒发送失败: %s", exc)
        return alert