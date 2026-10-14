"""历史与状态持久化测试。"""

from __future__ import annotations

from goldalert.sources import Quote
from goldalert.store import HistoryStore, StateStore


def make(index: int) -> Quote:
    return Quote(symbol="XAU", name="Gold", price=4000.0 + index, source="t", fetched_at=float(index))


def test_append_and_read_back(tmp_path):
    store = HistoryStore(str(tmp_path / "h.jsonl"), max_points=100)
    for index in range(5):
        store.append(make(index))
    assert len(store.tail(10)) == 5
    assert store.recent_prices(10) == [4000.0, 4001.0, 4002.0, 4003.0, 4004.0]


def test_trim_keeps_latest(tmp_path):
    store = HistoryStore(str(tmp_path / "h.jsonl"), max_points=3)
    for index in range(5):
        store.append(make(index))
    store.trim()
    assert len(store.tail(10)) == 3
    assert store.recent_prices(10) == [4002.0, 4003.0, 4004.0]


def test_auto_trim_caps_growth(tmp_path):
    store = HistoryStore(str(tmp_path / "h.jsonl"), max_points=3)
    for index in range(20):
        store.append(make(index))
    assert len(store.tail(100)) <= 4


def test_tail_skips_broken_lines(tmp_path):
    path = tmp_path / "h.jsonl"
    path.write_text('{"price": 1}\n这不是 JSON\n{"price": 2}\n', encoding="utf-8")
    store = HistoryStore(str(path))
    assert store.recent_prices(10) == [1.0, 2.0]


def test_state_store_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(str(path))
    store.set("sina", {"last_fired": 123})
    assert StateStore(str(path)).get("sina") == {"last_fired": 123}
    assert store.get("missing", "默认") == "默认"