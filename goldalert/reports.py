"""定时价格播报。

目前实现"每隔固定时间播报一次当前价"，并附带与上一时间窗的涨跌对比。
后续要加"到价 / 涨跌幅 / 短时异动"等提醒，在本模块内新增 Reporter 即可，
对外仍然只调用 :meth:`PriceReporter.maybe_send`。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from .notify import NotifierHub
from .sources import Quote
from .store import HistoryStore, StateStore
from .util import fmt_dt, now_cn

STATE_KEY = "next_report_ts"


def _arrow(diff: float) -> str:
    return "▲" if diff > 0 else ("▼" if diff < 0 else "■")


def compute_next_fire(config, now: Optional[datetime] = None) -> datetime:
    """计算下一次应当播报的时刻。

    ``align_to_clock`` 为真时对齐到整点（例如每 60 分钟 -> 每个整点），
    否则从当前时刻起顺延。
    """
    now = now or now_cn()
    if not config.align_to_clock:
        return now + timedelta(minutes=config.every_minutes)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_minutes = (now - midnight).total_seconds() / 60.0
    step = config.every_minutes
    return midnight + timedelta(minutes=(int(elapsed_minutes // step) + 1) * step)


def format_report(
    quote: Quote, when: datetime, previous: Optional[float], window_minutes: int
) -> str:
    """把一条行情格式化成纯文本播报正文。"""
    lines = [
        f"现货黄金 {quote.symbol}",
        f"时间       {fmt_dt(when)}",
        "",
        f"最新价     {quote.price:,.2f} 美元/盎司",
    ]
    if quote.change is not None and quote.change_pct is not None:
        lines.append(
            f"较昨收     {_arrow(quote.change)} {quote.change:+,.2f}  ({quote.change_pct:+.2f}%)"
        )
    if previous is not None and previous > 0:
        diff = quote.price - previous
        lines.append(
            f"较{window_minutes}分钟前 {_arrow(diff)} {diff:+,.2f}  ({diff / previous * 100:+.2f}%)"
        )
    if quote.high is not None and quote.low is not None:
        lines.append(f"今日高/低  {quote.high:,.2f} / {quote.low:,.2f}")
    if quote.bid is not None and quote.ask is not None:
        lines.append(f"买/卖      {quote.bid:,.2f} / {quote.ask:,.2f}")
    lines.append(f"数据源     {quote.source}")
    return "\n".join(lines)


class PriceReporter:
    """按固定节奏播报当前金价。"""

    def __init__(
        self,
        config,
        state: StateStore,
        notifier: NotifierHub,
        history: HistoryStore,
        analyzer=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self.state = state
        self.notifier = notifier
        self.history = history
        self.analyzer = analyzer
        self.logger = logger or logging.getLogger("goldalert.reports")

    def next_fire_time(self, now: Optional[datetime] = None) -> datetime:
        return compute_next_fire(self.config, now)

    def _read_next(self, now: datetime) -> float:
        stored = self.state.get(STATE_KEY)
        if stored is None:
            stored = self.next_fire_time(now).timestamp()
            self.state.set(STATE_KEY, stored)
        return float(stored)

    def maybe_send(self, quote: Quote, now: Optional[datetime] = None) -> bool:
        """到点就播报，返回本次是否真的发了。"""
        if not self.config.enabled:
            return False
        now = now or now_cn()
        scheduled = self._read_next(now)
        if now.timestamp() < scheduled:
            return False

        previous = None
        window = self.config.compare_window_minutes
        if window > 0:
            found = self.history.price_at_or_before(now.timestamp() - window * 60.0)
            if found is not None:
                previous = found[0]

        body = format_report(quote, now, previous, window)
        body += self._ai_section(quote, now)
        title = f"📊 金价播报 {quote.price:,.2f} 美元/盎司"
        self.logger.info("发送定时播报: %s", title)
        self.notifier.send(title, body, "info")

        self.state.set(STATE_KEY, self.next_fire_time(now).timestamp())
        return True

    def _ai_section(self, quote: Quote, now: datetime) -> str:
        """生成 AI 盘面小结段落；未启用或失败时返回空串。"""
        if self.analyzer is None:
            return ""
        try:
            cutoff = now.timestamp() - self.analyzer.config.lookback_hours * 3600.0
            records = self.history.records_since(cutoff)
            analysis = self.analyzer.analyze(quote, records)
        except Exception as exc:  # AI 失败绝不能影响播报
            self.logger.warning("AI 盘面小结生成失败: %s", exc)
            return ""
        if not analysis:
            return ""
        return "\n\n———— AI 盘面小结 ————\n" + analysis