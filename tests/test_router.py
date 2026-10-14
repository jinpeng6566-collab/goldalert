"""数据源调度、切换与代理告警测试。"""

from __future__ import annotations

import pytest

from goldalert.notify import Notifier, NotifierHub
from goldalert.router import SourceManager
from goldalert.sources import ProxyError, SourceError

from conftest_helpers import fake_source, quote


class Recorder(Notifier):
    name = "recorder"

    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def send(self, title: str, body: str, level: str = "info") -> bool:
        self.events.append((level, title, body))
        return True

    @property
    def titles(self) -> list[str]:
        return [title for _, title, _ in self.events]


def test_uses_primary_when_healthy():
    primary = fake_source("tradingview", [quote("tradingview")])
    backup = fake_source("sina", [quote("sina")])
    manager = SourceManager([primary, backup], NotifierHub([Recorder()]))
    assert manager.fetch("XAU").source == "tradingview"
    assert manager.active == "tradingview"
    assert backup.calls == 0


def test_failover_to_backup_notifies_including_at_startup():
    primary = fake_source("tradingview", [ProxyError("代理断开")])
    backup = fake_source("sina", [quote("sina", 4100.0)])
    recorder = Recorder()
    manager = SourceManager([primary, backup], NotifierHub([recorder]), failure_threshold=1)

    assert manager.fetch("XAU").source == "sina"
    assert manager.active == "sina"
    assert any("备用数据源" in title for title in recorder.titles)


def test_switch_notifies_when_primary_drops_mid_run():
    primary = fake_source("tradingview", [quote("tradingview"), SourceError("挂了")])
    backup = fake_source("sina", [quote("sina")])
    recorder = Recorder()
    manager = SourceManager([primary, backup], NotifierHub([recorder]))

    manager.fetch("XAU")          # 主源正常
    assert recorder.events == []

    manager.fetch("XAU")          # 主源失败，切备用
    assert manager.active == "sina"
    assert any("切换到备用数据源" in title for title in recorder.titles)


def test_recovers_to_primary_and_notifies():
    primary = fake_source("tradingview", [SourceError("挂了"), quote("tradingview")])
    backup = fake_source("sina", [quote("sina"), quote("sina")])
    recorder = Recorder()
    manager = SourceManager([primary, backup], NotifierHub([recorder]))

    manager.fetch("XAU")
    assert manager.active == "sina"
    manager.fetch("XAU")
    assert manager.active == "tradingview"
    assert any("已恢复主数据源" in title for title in recorder.titles)


def test_all_failed_notifies_only_at_threshold():
    a = fake_source("tradingview", [SourceError("a")] * 5)
    b = fake_source("sina", [SourceError("b")] * 5)
    recorder = Recorder()
    manager = SourceManager([a, b], NotifierHub([recorder]), failure_threshold=2)

    for _ in range(2):
        with pytest.raises(SourceError):
            manager.fetch("XAU")

    hits = [t for t in recorder.titles if "所有数据源" in t]
    assert len(hits) == 1


def test_proxy_down_then_recovered_notifies(monkeypatch):
    recorder = Recorder()
    manager = SourceManager(
        [fake_source("sina", [quote("sina")])],
        NotifierHub([recorder]),
        failure_threshold=2,
        proxy="http://127.0.0.1:7890",
        proxy_check_interval=0.0,
    )

    monkeypatch.setattr("goldalert.router.probe_tcp", lambda *a, **k: False)
    manager.check_proxy(force=True)
    manager.check_proxy(force=True)
    assert manager.proxy_alive is False

    monkeypatch.setattr("goldalert.router.probe_tcp", lambda *a, **k: True)
    manager.check_proxy(force=True)
    assert manager.proxy_alive is True

    assert any("代理已断开" in t for t in recorder.titles)
    assert any("代理已恢复" in t for t in recorder.titles)


def test_status_line_reports_active_source_and_proxy():
    recorder = Recorder()
    manager = SourceManager(
        [fake_source("sina", [quote("sina")])], NotifierHub([recorder]), proxy=None
    )
    manager.fetch("XAU")
    line = manager.status_line()
    assert "sina" in line
    assert "未检测" in line