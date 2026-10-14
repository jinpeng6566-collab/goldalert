"""关键价位：存取、触发规则、通知。"""

from __future__ import annotations

import os
import time
from datetime import timedelta

from goldalert.levels import (
    AI,
    BREAK,
    MANUAL,
    NEAR,
    Alert,
    LEVEL_STATE_KEY,
    Level,
    LevelService,
    LevelSettings,
    LevelStore,
    LevelWatcher,
    format_alert,
    format_levels,
)
from goldalert.sources import Quote
from goldalert.store import HistoryStore, StateStore
from goldalert.util import now_cn

BASE = now_cn()


def quote(price: float, when=None) -> Quote:
    when = when or BASE
    return Quote(symbol="XAU", name="Gold", price=price, source="test", fetched_at=when.timestamp())


def build(tmp_path, levels, **settings):
    state = StateStore(str(tmp_path / "state.json"))
    return LevelWatcher(state), LevelSettings(**settings), levels


# ------------------------------------------------------------------ 存取
def test_store_roundtrip(tmp_path):
    path = tmp_path / "levels.json"
    store = LevelStore(str(path))
    manual = store.add(4250.0, "我的上限")
    store.replace_ai_levels([Level(price=4200.0, label="近期高点", source=AI)])
    store.save_settings(LevelSettings(proximity_usd=2.5))

    again = LevelStore(str(path))
    assert len(again.all_levels()) == 2
    assert again.by_source(MANUAL)[0].label == "我的上限"
    assert again.by_source(AI)[0].price == 4200.0
    assert again.settings.proximity_usd == 2.5

    assert again.remove(manual.id) is True
    assert len(again.all_levels()) == 1
    assert again.remove(manual.id) is False


def test_replace_ai_keeps_manual(tmp_path):
    store = LevelStore(str(tmp_path / "levels.json"))
    store.add(4250.0, "手动")
    store.replace_ai_levels([Level(price=4200.0, label="AI 一")])
    store.replace_ai_levels([Level(price=4100.0, label="AI 二")])
    assert [level.price for level in store.by_source(MANUAL)] == [4250.0]
    assert [level.price for level in store.by_source(AI)] == [4100.0]


def test_broken_file_is_tolerated(tmp_path):
    path = tmp_path / "levels.json"
    path.write_text("这不是 JSON", encoding="utf-8")
    store = LevelStore(str(path))
    assert store.all_levels() == []
    assert store.settings.proximity_usd == 2.0


def test_update_and_toggle(tmp_path):
    store = LevelStore(str(tmp_path / "levels.json"))
    level = store.add(4200.0, "旧")
    assert store.update(level.id, price=4210.0, label="新") is True
    assert store.get(level.id).price == 4210.0
    assert store.get(level.id).label == "新"
    assert store.update("不存在", price=1.0) is False


def test_add_same_label_overwrites(tmp_path):
    """同一个名称只留一条：再填一次就覆盖它的价格，不新增重复条目。"""
    store = LevelStore(str(tmp_path / "levels.json"))
    first = store.add(4200.0, "今日最高")
    again, created = store.upsert(4195.0, "今日最高")

    assert created is False
    assert again.id == first.id
    assert len(store.all_levels()) == 1
    assert store.all_levels()[0].price == 4195.0


def test_blank_label_never_merges(tmp_path):
    """没写名称的价位不做合并，免得互相吃掉。"""
    store = LevelStore(str(tmp_path / "levels.json"))
    store.add(4200.0, "")
    store.add(4195.0, "")
    assert len(store.all_levels()) == 2


def test_adopting_ai_level_does_not_duplicate(tmp_path):
    """采纳 AI 已经给出的价位：就地接管，不产生第二条同名的。"""
    store = LevelStore(str(tmp_path / "levels.json"))
    store.replace_ai_levels([
        Level(price=4200.0, label="今日最高", source=AI),
        Level(price=4150.0, label="今日最低", source=AI),
    ])

    store.upsert(4200.0, "今日最高")

    assert len(store.all_levels()) == 2
    assert sorted(level.label for level in store.all_levels()) == ["今日最低", "今日最高"]
    assert [level.source for level in store.by_source(MANUAL)] == [MANUAL]


def test_ai_skips_labels_the_user_already_has(tmp_path):
    """AI 给的价位和你已有的同名时直接跳过，不会多出一条重复的。"""
    store = LevelStore(str(tmp_path / "levels.json"))
    store.add(4200.0, "今日最高")

    skipped = store.replace_ai_levels([
        Level(price=4201.0, label="今日最高", source=AI),
        Level(price=4150.0, label="今日最低", source=AI),
    ])

    assert skipped == 1
    assert len(store.all_levels()) == 2
    assert sorted(level.label for level in store.all_levels()) == ["今日最低", "今日最高"]
    assert store.find_by_label("今日最高").price == 4200.0, "你自己的价位不该被 AI 改掉"
    assert store.find_by_label("今日最高").source == MANUAL


def test_ai_reports_zero_when_no_same_name(tmp_path):
    store = LevelStore(str(tmp_path / "levels.json"))
    store.add(4200.0, "我的上限")
    assert store.replace_ai_levels([Level(price=4150.0, label="今日最低", source=AI)]) == 0


# ------------------------------------------------------------------ 触发
def test_first_tick_only_baselines(tmp_path):
    watcher, settings, levels = build(tmp_path, [Level(price=4200.0)], proximity_usd=2.0)
    assert watcher.check(quote(4200.5), settings, levels) == []


def test_near_fires_once_until_rearm(tmp_path):
    watcher, settings, levels = build(
        tmp_path, [Level(price=4200.0)], proximity_usd=2.0, rearm_usd=3.0, cooldown_minutes=0
    )
    watcher.check(quote(4190.0), settings, levels)

    alerts = watcher.check(quote(4199.0), settings, levels)
    assert [alert.kind for alert in alerts] == [NEAR]
    assert alerts[0].distance < 0

    assert watcher.check(quote(4198.5), settings, levels) == []

    watcher.check(quote(4194.0), settings, levels)  # 离开 2+3 美元以上，重新武装
    assert len(watcher.check(quote(4199.0), settings, levels)) == 1


def test_break_up_and_down(tmp_path):
    watcher, settings, levels = build(
        tmp_path, [Level(price=4200.0, label="关口")], proximity_usd=1.0, rearm_usd=1.0, cooldown_minutes=0
    )
    watcher.check(quote(4198.0), settings, levels)

    alerts = watcher.check(quote(4201.0), settings, levels)
    assert [alert.kind for alert in alerts] == [BREAK]
    assert alerts[0].direction == "up"
    assert alerts[0].distance == 1.0

    watcher.check(quote(4199.0), settings, levels)
    alerts = watcher.check(quote(4201.0), settings, levels)
    assert alerts and alerts[0].direction == "up"

    watcher.check(quote(4201.5), settings, levels)
    alerts = watcher.check(quote(4195.0), settings, levels)
    assert alerts and alerts[0].direction == "down"


def test_cooldown_suppresses_repeat(tmp_path):
    watcher, settings, levels = build(
        tmp_path, [Level(price=4200.0)], proximity_usd=2.0, rearm_usd=3.0, cooldown_minutes=10
    )
    watcher.check(quote(4190.0), settings, levels, now=BASE)

    assert len(watcher.check(quote(4199.0), settings, levels, now=BASE)) == 1
    watcher.check(quote(4190.0), settings, levels, now=BASE + timedelta(seconds=1))
    assert watcher.check(quote(4199.0), settings, levels, now=BASE + timedelta(seconds=2)) == []

    watcher.check(quote(4190.0), settings, levels, now=BASE + timedelta(minutes=11))
    assert len(watcher.check(quote(4199.0), settings, levels, now=BASE + timedelta(minutes=12))) == 1


def test_disabled_switch_and_level(tmp_path):
    watcher, settings, levels = build(tmp_path, [Level(price=4200.0)], cooldown_minutes=0)
    settings.enabled = False
    watcher.check(quote(4190.0), settings, levels)
    assert watcher.check(quote(4199.0), settings, levels) == []

    settings.enabled = True
    levels[0].enabled = False
    assert watcher.check(quote(4199.0), settings, levels) == []


def test_kinds_can_be_turned_off(tmp_path):
    watcher, settings, levels = build(
        tmp_path, [Level(price=4200.0)], proximity_usd=2.0, rearm_usd=3.0, cooldown_minutes=0
    )
    settings.notify_near = False
    watcher.check(quote(4190.0), settings, levels)
    assert watcher.check(quote(4199.0), settings, levels) == []


def test_removed_level_state_is_pruned(tmp_path):
    watcher, settings, levels = build(tmp_path, [Level(price=4200.0), Level(price=4300.0)])
    watcher.check(quote(4190.0), settings, levels)
    assert len(watcher.snapshot()) == 2
    watcher.check(quote(4190.0), settings, levels[:1])
    assert len(watcher.snapshot()) == 1


# ------------------------------------------------------------------ 文案与通知
def test_format_alert_text():
    level = Level(price=4200.0, label="今日高点")
    alert = Alert(level, BREAK, 4201.5, "up", 1.5, BASE)
    title, body, severity = format_alert(alert, quote(4201.5))
    assert "上破" in title and "4,200.00" in title
    assert severity == "critical"
    assert "4,201.50" in body and "今日高点" in body


def test_format_levels_text():
    text = format_levels([Level(price=4200.0, label="上限")], 4185.0)
    assert "4,200.00" in text and "上限" in text and "-15.00" in text


class FakeHub:
    def __init__(self):
        self.sent = []

    def send(self, title, body, level="info"):
        self.sent.append((title, body, level))
        return {"fake": True}


def test_service_sends_notification(tmp_path):
    store = LevelStore(str(tmp_path / "levels.json"))
    store.add(4200.0, "上限")
    hub = FakeHub()
    service = LevelService(store, LevelWatcher(StateStore(str(tmp_path / "state.json"))), hub)

    service.on_quote(quote(4190.0))
    assert hub.sent == []
    service.on_quote(quote(4199.0))
    assert len(hub.sent) == 1
    assert "靠近关键价位" in hub.sent[0][0]
    assert hub.sent[0][2] == "warning"


def test_service_reloads_edited_file(tmp_path):
    """界面在旁边改了 levels.json，跑着的监控要能自己读到。"""
    path = tmp_path / "levels.json"
    store = LevelStore(str(path))
    service = LevelService(
        store, LevelWatcher(StateStore(str(tmp_path / "state.json"))), FakeHub()
    )
    service.on_quote(quote(4190.0))
    assert store.all_levels() == []

    other = LevelStore(str(path))
    other.add(4191.0, "界面加的")
    os.utime(path, (time.time() + 5, time.time() + 5))

    service.on_quote(quote(4190.0))
    assert [level.label for level in service.store.all_levels()] == ["界面加的"]


def test_ai_levels_not_written_when_too_little_data(tmp_path):
    store = LevelStore(str(tmp_path / "levels.json"))

    class FakeAnalyzer:
        available = True
        config = type("C", (), {"lookback_hours": 6.0})()

        def suggest_levels(self, quote, records, max_levels):
            raise AssertionError("数据不足时不该调用 AI")

    service = LevelService(
        store,
        LevelWatcher(StateStore(str(tmp_path / "state.json"))),
        FakeHub(),
        analyzer=FakeAnalyzer(),
        state=StateStore(str(tmp_path / "state2.json")),
    )
    assert service.refresh_ai_levels(quote(4185.0)) == []

def test_ai_update_notifies_only_when_prices_change(tmp_path):
    """AI 定时刷新价位；价位没变就不该再发一封邮件，变了才通知。"""
    store = LevelStore(str(tmp_path / "levels.json"))
    settings = store.settings
    settings.notify_ai_update = True
    store.save_settings(settings)

    history = HistoryStore(str(tmp_path / "h.jsonl"), max_points=100)
    for index in range(12):
        history.append(quote(4185.0 + index * 0.1, when=BASE - timedelta(seconds=index * 5)))

    class FakeAnalyzer:
        available = True
        config = type("C", (), {"lookback_hours": 4.0})()
        prices = [4200.0, 4150.0]

        def suggest_levels(self, quote, records, max_levels):
            return [{"price": price, "label": "AI 价位"} for price in self.prices]

    analyzer = FakeAnalyzer()
    hub = FakeHub()
    service = LevelService(
        store,
        LevelWatcher(StateStore(str(tmp_path / "state.json"))),
        hub,
        history=history,
        analyzer=analyzer,
        state=StateStore(str(tmp_path / "state2.json")),
    )

    service.refresh_ai_levels(quote(4185.0))
    assert len(hub.sent) == 1
    assert "AI 已更新关键价位" in hub.sent[0][0]

    service.refresh_ai_levels(quote(4185.0), now=BASE + timedelta(hours=2))
    assert len(hub.sent) == 1, "价位没变时不该重复发邮件"
    assert sorted(level.price for level in store.by_source(AI)) == [4150.0, 4200.0]

    analyzer.prices = [4210.0, 4150.0]
    service.refresh_ai_levels(quote(4185.0), now=BASE + timedelta(hours=4))
    assert len(hub.sent) == 2
    assert "上次" in hub.sent[1][1] and "4,200.00" in hub.sent[1][1]


def test_ai_refresh_blocked_by_same_name_is_silent(tmp_path):
    """AI 给的价位全都和你已有的同名：不写新价位、也不发邮件。"""
    store = LevelStore(str(tmp_path / "levels.json"))
    settings = store.settings
    settings.notify_ai_update = True
    store.save_settings(settings)
    store.add(4200.0, "今日最高")

    history = HistoryStore(str(tmp_path / "h.jsonl"), max_points=100)
    for index in range(12):
        history.append(quote(4185.0 + index * 0.1, when=BASE - timedelta(seconds=index * 5)))

    class FakeAnalyzer:
        available = True
        config = type("C", (), {"lookback_hours": 4.0})()

        def suggest_levels(self, quote, records, max_levels):
            return [{"price": 4201.0, "label": "今日最高"}]

    hub = FakeHub()
    service = LevelService(
        store,
        LevelWatcher(StateStore(str(tmp_path / "state.json"))),
        hub,
        history=history,
        analyzer=FakeAnalyzer(),
        state=StateStore(str(tmp_path / "state2.json")),
    )

    assert service.refresh_ai_levels(quote(4185.0)) == []
    assert hub.sent == []
    assert len(store.all_levels()) == 1
    assert store.all_levels()[0].price == 4200.0


def test_near_then_break_both_alerted(tmp_path):
    """先靠近、几分钟后又突破：两类提醒各发一次，互不占用对方的冷却。"""
    watcher, settings, levels = build(
        tmp_path, [Level(price=4200.0, label="关口")],
        proximity_usd=2.0, rearm_usd=3.0, cooldown_minutes=10,
    )
    watcher.check(quote(4190.0), settings, levels, now=BASE)

    alerts = watcher.check(quote(4199.0), settings, levels, now=BASE + timedelta(seconds=5))
    assert [alert.kind for alert in alerts] == [NEAR]

    alerts = watcher.check(quote(4201.0), settings, levels, now=BASE + timedelta(seconds=10))
    assert [alert.kind for alert in alerts] == [BREAK], "突破不该被几分钟前的「接近」冷却掉"
    assert alerts[0].direction == "up"


def test_break_whipsaw_damped_by_its_own_cooldown(tmp_path):
    """来回穿越同一个价位时，突破仍然按自己的冷却限流，不会每穿一次发一封。"""
    watcher, settings, levels = build(
        tmp_path, [Level(price=4200.0)],
        proximity_usd=1.0, rearm_usd=1.0, cooldown_minutes=10,
    )
    watcher.check(quote(4198.0), settings, levels, now=BASE)

    assert [a.kind for a in watcher.check(
        quote(4201.0), settings, levels, now=BASE + timedelta(seconds=10))] == [BREAK]

    # 40 秒内又跌回去、再涨上来：还在突破冷却里，一封都不发
    assert watcher.check(quote(4198.0), settings, levels, now=BASE + timedelta(seconds=20)) == []
    assert watcher.check(quote(4201.0), settings, levels, now=BASE + timedelta(seconds=30)) == []


def test_legacy_state_damps_each_kind_once(tmp_path):
    """老状态里只有一个 last_alert：升级成双计时后不能马上补发一封。"""
    level = Level(price=4200.0)
    state = StateStore(str(tmp_path / "state.json"))
    state.set(LEVEL_STATE_KEY, {
        level.id: {"side": -1, "near_armed": 1, "last_alert": BASE.timestamp()}
    })
    watcher = LevelWatcher(state)
    settings = LevelSettings(proximity_usd=2.0, rearm_usd=3.0, cooldown_minutes=10)

    assert watcher.check(quote(4201.0), settings, [level], now=BASE + timedelta(seconds=5)) == []
    assert watcher.check(quote(4199.0), settings, [level], now=BASE + timedelta(seconds=8)) == []

    entry = watcher.snapshot()[level.id]
    assert entry["last_near"] == BASE.timestamp()
    assert entry["last_break"] == BASE.timestamp()
