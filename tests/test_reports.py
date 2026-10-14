"""定时播报测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

from goldalert.config import ReportConfig
from goldalert.notify import Notifier, NotifierHub
from goldalert.reports import PriceReporter, compute_next_fire, format_report
from goldalert.sources import Quote
from goldalert.store import HistoryStore, StateStore
from goldalert.util import CN_TZ


class Recorder(Notifier):
    name = "recorder"

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def send(self, title: str, body: str, level: str = "info") -> bool:
        self.events.append((title, body))
        return True


def make_quote(price: float = 4184.0, stamp: float = 0.0) -> Quote:
    return Quote(
        symbol="XAU", name="Gold", price=price, source="tradingview", fetched_at=stamp,
        prev_close=4171.65, high=4200.6, low=4150.8, bid=4183.9, ask=4184.1,
    )


def build(tmp_path, config, recorder):
    state = StateStore(str(tmp_path / "state.json"))
    history = HistoryStore(str(tmp_path / "h.jsonl"), max_points=100)
    return PriceReporter(config, state, NotifierHub([recorder]), history), history


def test_next_fire_aligns_to_hour():
    config = ReportConfig(every_minutes=60, align_to_clock=True)
    now = datetime(2026, 10, 13, 10, 23, 45, tzinfo=CN_TZ)
    assert compute_next_fire(config, now) == datetime(2026, 10, 13, 11, 0, 0, tzinfo=CN_TZ)


def test_next_fire_aligns_to_quarter_hour():
    config = ReportConfig(every_minutes=15, align_to_clock=True)
    now = datetime(2026, 10, 13, 10, 23, 45, tzinfo=CN_TZ)
    assert compute_next_fire(config, now) == datetime(2026, 10, 13, 10, 30, 0, tzinfo=CN_TZ)


def test_next_fire_without_alignment_offsets_from_now():
    config = ReportConfig(every_minutes=60, align_to_clock=False)
    now = datetime(2026, 10, 13, 10, 23, 45, tzinfo=CN_TZ)
    assert compute_next_fire(config, now) == now + timedelta(minutes=60)


def test_waits_until_due_then_fires_once(tmp_path):
    recorder = Recorder()
    reporter, _ = build(tmp_path, ReportConfig(every_minutes=60, align_to_clock=True), recorder)

    assert reporter.maybe_send(make_quote(), datetime(2026, 10, 13, 10, 23, 45, tzinfo=CN_TZ)) is False
    assert recorder.events == []

    assert reporter.maybe_send(make_quote(), datetime(2026, 10, 13, 11, 0, 5, tzinfo=CN_TZ)) is True
    assert len(recorder.events) == 1

    assert reporter.maybe_send(make_quote(), datetime(2026, 10, 13, 11, 0, 30, tzinfo=CN_TZ)) is False
    assert len(recorder.events) == 1


def test_schedule_survives_restart(tmp_path):
    config = ReportConfig(every_minutes=60, align_to_clock=True)
    history = HistoryStore(str(tmp_path / "h.jsonl"), max_points=100)
    state_path = str(tmp_path / "state.json")

    first = PriceReporter(config, StateStore(state_path), NotifierHub([Recorder()]), history)
    first.maybe_send(make_quote(), datetime(2026, 10, 13, 10, 23, 45, tzinfo=CN_TZ))

    recorder = Recorder()
    second = PriceReporter(config, StateStore(state_path), NotifierHub([recorder]), history)
    assert second.maybe_send(make_quote(), datetime(2026, 10, 13, 10, 30, 0, tzinfo=CN_TZ)) is False
    assert second.maybe_send(make_quote(), datetime(2026, 10, 13, 11, 0, 1, tzinfo=CN_TZ)) is True


def test_disabled_reporter_never_sends(tmp_path):
    recorder = Recorder()
    reporter, _ = build(tmp_path, ReportConfig(enabled=False), recorder)
    assert reporter.maybe_send(make_quote(), datetime(2030, 1, 1, 0, 0, tzinfo=CN_TZ)) is False
    assert recorder.events == []


def test_report_body_contains_price_and_hour_change():
    when = datetime(2026, 10, 13, 11, 0, 0, tzinfo=CN_TZ)
    body = format_report(make_quote(4200.0), when, previous=4100.0, window_minutes=60)
    assert "4,200.00" in body
    assert "较60分钟前" in body
    assert "+100.00" in body
    assert "tradingview" in body


def test_price_at_or_before(tmp_path):
    history = HistoryStore(str(tmp_path / "h.jsonl"), max_points=100)
    for index, price in enumerate([1000.0, 2000.0, 3000.0]):
        history.append(make_quote(price, stamp=float(index * 60)))
    assert history.price_at_or_before(90.0) == (2000.0, 60.0)
    assert history.price_at_or_before(130.0) == (3000.0, 120.0)
    assert history.price_at_or_before(-1) is None

class StubAnalyzer:
    """假的 AI 分析器，用来验证播报集成。"""

    def __init__(self, text, lookback_hours=6.0, boom=False):
        from goldalert.config import AIConfig

        self.config = AIConfig(lookback_hours=lookback_hours)
        self.text = text
        self.boom = boom
        self.calls = 0

    def analyze(self, quote, records):
        self.calls += 1
        if self.boom:
            raise RuntimeError("模型挂了")
        return self.text


def test_report_appends_ai_section(tmp_path):
    recorder = Recorder()
    history = HistoryStore(str(tmp_path / "h.jsonl"), max_points=100)
    reporter = PriceReporter(
        ReportConfig(every_minutes=60, align_to_clock=True),
        StateStore(str(tmp_path / "s.json")),
        NotifierHub([recorder]),
        history,
        analyzer=StubAnalyzer("金价在 4180–4200 区间震荡。"),
    )
    # 首次调用只负责排定下一次播报时间
    reporter.maybe_send(make_quote(), datetime(2026, 10, 13, 10, 23, 45, tzinfo=CN_TZ))
    due = datetime(2026, 10, 13, 11, 0, 5, tzinfo=CN_TZ)
    assert reporter.maybe_send(make_quote(), due) is True
    body = recorder.events[0][1]
    assert "AI 盘面小结" in body
    assert "4180–4200 区间震荡" in body


def test_report_survives_analyzer_failure(tmp_path):
    recorder = Recorder()
    history = HistoryStore(str(tmp_path / "h.jsonl"), max_points=100)
    reporter = PriceReporter(
        ReportConfig(every_minutes=60, align_to_clock=True),
        StateStore(str(tmp_path / "s.json")),
        NotifierHub([recorder]),
        history,
        analyzer=StubAnalyzer("", boom=True),
    )
    # 首次调用只负责排定下一次播报时间
    reporter.maybe_send(make_quote(), datetime(2026, 10, 13, 10, 23, 45, tzinfo=CN_TZ))
    due = datetime(2026, 10, 13, 11, 0, 5, tzinfo=CN_TZ)
    assert reporter.maybe_send(make_quote(), due) is True
    body = recorder.events[0][1]
    assert "AI 盘面小结" not in body
    assert "4,184.00" in body


def test_records_since_filters_by_time(tmp_path):
    history = HistoryStore(str(tmp_path / "h.jsonl"), max_points=100)
    for index in range(5):
        history.append(make_quote(4000.0 + index, stamp=float(index * 60)))
    assert len(history.records_since(120.0)) == 3
    assert len(history.records_since(-1.0)) == 5