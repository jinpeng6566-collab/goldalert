"""终端实时行情仪表盘（基于 rich）。"""

from __future__ import annotations

from typing import Iterable, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .sources import Quote
from .util import fmt_dt, now_cn

BARS = "▁▂▃▄▅▆▇█"

UP_STYLE = "bold red"      # 国内习惯：红涨
DOWN_STYLE = "bold green"  # 绿跌
FLAT_STYLE = "bold white"

LEVEL_COLOR = {"info": "cyan", "warning": "yellow", "critical": "bold red"}


def _num(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def sparkline(values: Iterable[float], width: int = 60) -> str:
    """把一串价格画成单行走势图。"""
    data = [v for v in values if v is not None]
    if not data:
        return ""
    data = data[-width:]
    low, high = min(data), max(data)
    span = high - low
    if span <= 0:
        return BARS[0] * len(data)
    return "".join(BARS[int((v - low) / span * (len(BARS) - 1))] for v in data)


def _short_duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


class Dashboard:
    """富文本实时面板：价格、涨跌、今日高低、买卖价、走势、采集统计、事件提示。"""

    def __init__(
        self,
        symbol: str,
        interval: float,
        sparkline_points: int = 60,
        console: Optional[Console] = None,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.sparkline_points = sparkline_points
        self.console = console or Console()
        self.live: Optional[Live] = None
        self.last_quote: Optional[Quote] = None
        self.prices: list[float] = []
        self.count = 0
        self.started_at = now_cn()
        self.last_error: Optional[str] = None
        self.status_text: str = ""
        self.last_event: Optional[tuple[str, str, str]] = None

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        self.live = Live(self.render(), console=self.console, refresh_per_second=10)
        self.live.start()

    def stop(self) -> None:
        if self.live is not None:
            self.live.update(self.render(countdown=0))
            self.live.stop()
            self.live = None

    def refresh(self, countdown: Optional[float] = None) -> None:
        if self.live is not None:
            self.live.update(self.render(countdown))

    # -- 数据 -------------------------------------------------------------
    def record(self, quote: Quote) -> None:
        self.last_quote = quote
        self.prices.append(quote.price)
        self.count += 1
        self.last_error = None
        max_keep = max(self.sparkline_points, 240)
        if len(self.prices) > max_keep:
            self.prices = self.prices[-max_keep:]

    def set_error(self, message: str) -> None:
        self.last_error = message

    def set_status(self, text: str) -> None:
        self.status_text = text

    def push_event(self, title: str, body: str, level: str = "info") -> None:
        self.last_event = (level, title, body)
        self.refresh()

    # -- 渲染 -------------------------------------------------------------
    def render(self, countdown: Optional[float] = None) -> Panel:
        blocks: list[object] = []
        quote = self.last_quote

        if quote is None:
            blocks.append(Text("正在获取首个报价 …", style="yellow"))
            if self.last_error:
                blocks.append(Text(f"取数失败：{self.last_error}", style="red"))
        else:
            change = quote.change
            pct = quote.change_pct
            if change is None:
                style, change_text = FLAT_STYLE, "—"
            elif change > 0:
                style = UP_STYLE
                change_text = f"▲ {change:+.2f}  ({pct:+.2f}%)"
            elif change < 0:
                style = DOWN_STYLE
                change_text = f"▼ {change:+.2f}  ({pct:+.2f}%)"
            else:
                style, change_text = FLAT_STYLE, "■ 0.00  (0.00%)"

            price_text = Text()
            price_text.append(f"{quote.price:,.2f}", style=style)
            price_text.append("  美元/盎司", style="dim")

            table = Table.grid(padding=(0, 2))
            table.add_column(justify="right", style="dim", no_wrap=True)
            table.add_column(justify="left")
            table.add_row("最新价", price_text)
            table.add_row("涨　跌", Text(change_text, style=style))
            table.add_row("买/卖", Text(f"{_num(quote.bid)} / {_num(quote.ask)}"))
            table.add_row("今日高/低", Text(f"{_num(quote.high)} / {_num(quote.low)}"))
            table.add_row("今开/昨收", Text(f"{_num(quote.open)} / {_num(quote.prev_close)}"))
            table.add_row("数据源", Text(quote.source, style="cyan"))
            table.add_row("报价时间", Text(str(quote.quote_time or "—")))
            table.add_row("本地时间", Text(fmt_dt(now_cn())))
            blocks.append(table)

            width = max(20, min(self.sparkline_points, self.console.width - 12))
            blocks.extend(
                [Text(""), Text(sparkline(self.prices, width), style=style.split()[-1])]
            )

        elapsed = (now_cn() - self.started_at).total_seconds()
        footer = [f"采集 {self.count} 点", f"运行 {_short_duration(elapsed)}", f"间隔 {self.interval:g}s"]
        if countdown is not None:
            footer.append(f"下次刷新 {max(countdown, 0):.0f}s")
        blocks.extend([Text(""), Text("  ·  ".join(footer), style="dim")])

        if self.status_text:
            blocks.append(Text(self.status_text, style="dim"))

        if self.last_error:
            blocks.append(Text(f"最近一次取数异常：{self.last_error}", style="yellow"))

        if self.last_event:
            level, title, body = self.last_event
            color = LEVEL_COLOR.get(level, "cyan")
            blocks.append(Text(""))
            blocks.append(Text(f"{title}  {body.splitlines()[0]}", style=color))

        blocks.append(Text("按 Ctrl+C 停止监控", style="dim"))

        return Panel(
            Group(*blocks),
            title="[bold cyan]现货黄金 XAU · 实时监控[/bold cyan]",
            border_style="cyan",
        )