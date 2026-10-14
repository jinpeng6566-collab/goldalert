"""异常波动提醒：窗口内最高最低差超过阈值时触发。"""

from __future__ import annotations

from goldalert.config import VolatilityConfig
from goldalert.sources import Quote
from goldalert.store import StateStore
from goldalert.util import now_cn
from goldalert.volatility import (
    MIN_POINTS,
    VolatilityAlert,
    VolatilityService,
    VolatilityWatcher,
    format_volatility_alert,
)

BASE = now_cn()


def quote(price: float, seconds: float = 0.0) -> Quote:
    return Quote(
        symbol="XAU",
        name="Gold",
        price=price,
        source="test",
        fetched_at=BASE.timestamp() + seconds,
    )


def settings(**changes) -> VolatilityConfig:
    config = VolatilityConfig()
    for name, value in changes.items():
        setattr(config, name, value)
    return config


def feed(watcher, config, prices, start=0.0, step=5.0):
    """按固定间隔喂一串价格，返回中途触发的提醒列表。"""
    alerts = []
    for index, price in enumerate(prices):
        alert = watcher.check(quote(price, start + index * step), config)
        if alert is not None:
            alerts.append(alert)
    return alerts


# ------------------------------------------------------------------ 触发
def test_calm_market_does_not_alert():
    watcher = VolatilityWatcher()
    alerts = feed(watcher, settings(), [4185.0, 4185.4, 4184.8, 4185.2, 4185.0, 4184.9] * 5)
    assert alerts == []


def test_warns_when_window_range_hits_threshold():
    watcher = VolatilityWatcher()
    prices = [4180.0, 4180.5, 4181.0, 4183.0, 4187.0, 4191.0]
    alerts = feed(watcher, settings(), prices)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.low == 4180.0
    assert alert.high == 4191.0
    assert round(alert.span, 2) == 11.0
    assert alert.window_minutes == 5.0
    assert alert.threshold_usd == 10.0


def test_threshold_is_configurable():
    watcher = VolatilityWatcher()
    alerts = feed(watcher, settings(threshold_usd=3.0), [4180.0, 4180.5, 4184.0])
    assert len(alerts) == 1 and round(alerts[0].span, 2) == 4.0


def test_too_few_points_does_not_alert():
    watcher = VolatilityWatcher()
    assert feed(watcher, settings(), [4180.0, 4195.0]) == []
    assert len(watcher.points) == 2
    assert MIN_POINTS == 3


def test_disabled_never_alerts():
    watcher = VolatilityWatcher()
    assert feed(watcher, settings(enabled=False), [4180.0, 4190.0, 4200.0, 4210.0]) == []


# ------------------------------------------------------------------ 防刷屏
def test_same_wave_alerts_only_once():
    watcher = VolatilityWatcher()
    spike = [4180.0, 4180.0, 4180.0, 4191.0, 4192.0, 4193.0, 4194.0]
    alerts = feed(watcher, settings(), spike)
    assert len(alerts) == 1


def test_rearms_after_calm_and_cooldown():
    watcher = VolatilityWatcher()
    config = settings()
    assert len(feed(watcher, config, [4180.0, 4180.0, 4180.0, 4191.0], start=0.0)) == 1

    # 之后价格回到 4185 附近并待够冷却时间，波动收窄 -> 重新武装
    calm = [4185.0] * 130  # 5 秒一条，覆盖约 10.8 分钟
    assert feed(watcher, config, calm, start=25.0) == []
    assert watcher.armed is True

    assert len(feed(watcher, config, [4196.0], start=700.0)) == 1


def test_cooldown_blocks_second_alert():
    watcher = VolatilityWatcher()
    config = settings(cooldown_minutes=10.0)
    assert len(feed(watcher, config, [4180.0, 4180.0, 4180.0, 4191.0], start=0.0)) == 1

    # 波动刚收窄又立刻来一波，还在冷却里 -> 不提醒
    assert feed(watcher, config, [4185.0, 4185.0, 4185.0], start=30.0) == []
    assert feed(watcher, config, [4196.0], start=60.0) == []


def test_old_spike_leaves_the_window():
    watcher = VolatilityWatcher()
    config = settings(threshold_usd=10.0)
    assert len(feed(watcher, config, [4180.0, 4180.0, 4180.0, 4191.0], start=0.0)) == 1
    # 6 分钟后，最初的低点已经滑出 5 分钟窗口
    watcher.check(quote(4185.0, 400.0), config)
    assert all(ts >= 100.0 for ts, _ in watcher.points)
    assert round(max(p for _, p in watcher.points) - min(p for _, p in watcher.points), 2) == 0.0


# ------------------------------------------------------------------ 状态与通知
def test_state_survives_restart(tmp_path):
    path = str(tmp_path / "state.json")
    first = VolatilityWatcher(StateStore(path))
    assert len(feed(first, settings(), [4180.0, 4180.0, 4180.0, 4191.0])) == 1
    assert first.armed is False

    reborn = VolatilityWatcher(StateStore(path))
    assert reborn.armed is False
    assert reborn.last_alert == first.last_alert
    assert feed(reborn, settings(), [4192.0, 4193.0], start=10.0) == []


class FakeHub:
    def __init__(self):
        self.sent = []

    def send(self, title, body, level="info"):
        self.sent.append((title, body, level))
        return {"fake": True}


def test_service_sends_one_email(tmp_path):
    hub = FakeHub()
    service = VolatilityService(
        settings(), hub, state=StateStore(str(tmp_path / "state.json"))
    )
    for index, price in enumerate([4180.0, 4180.0, 4180.0, 4191.0, 4192.0]):
        service.on_quote(quote(price, index * 5.0), now=BASE)

    assert len(hub.sent) == 1
    title, body, severity = hub.sent[0]
    assert title == "⚡ 异常波动：5 分钟内 11.00 美元"
    assert "4,191.00" in body and "4,180.00" in body
    assert severity in ("warning", "critical")
    assert "窗口最高" in body and "窗口最低" in body and "振幅" in body


def test_format_alert_text():
    alert = VolatilityAlert(
        window_minutes=5.0,
        threshold_usd=10.0,
        high=4191.0,
        low=4180.0,
        start_price=4180.0,
        start_ts=BASE.timestamp(),
        price=4190.0,
    )
    title, body, severity = format_volatility_alert(alert, quote(4190.0))
    assert "异常波动" in title and "11.00" in title
    assert "4,191.00" in body and "4,180.00" in body
    assert "震荡上行" in body
    assert severity == "warning"


def test_settings_validation():
    assert VolatilityConfig().problems() == []
    assert settings(threshold_usd=0).problems()
    assert settings(window_minutes=0).problems()
    assert settings(rearm_ratio=1.5).problems()