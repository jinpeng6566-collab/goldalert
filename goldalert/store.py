"""行情历史与运行状态的本地持久化（JSONL + JSON，无需数据库）。"""

from __future__ import annotations

import json
from typing import Optional

from .sources import Quote
from .util import ensure_parent


class HistoryStore:
    """把每条行情快照追加写入 JSONL，并读取最近若干条。"""

    def __init__(self, path: str, max_points: int = 20000) -> None:
        self.path = ensure_parent(path)
        self.max_points = max_points
        self._count = self._count_lines()

    def _count_lines(self) -> int:
        if not self.path.exists():
            return 0
        with self.path.open("r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)

    def append(self, quote: Quote) -> None:
        """追加一条行情记录，必要时清理旧数据。"""
        record = {
            "t": round(quote.fetched_at, 3),
            "symbol": quote.symbol,
            "price": quote.price,
            "source": quote.source,
            "prev_close": quote.prev_close,
            "high": quote.high,
            "low": quote.low,
            "quote_time": quote.quote_time,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._count += 1
        if self._count > self.max_points * 1.2:
            self.trim()

    def trim(self) -> None:
        """只保留最近 ``max_points`` 条记录。"""
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if len(lines) > self.max_points:
            lines = lines[-self.max_points :]
            with self.path.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")
        self._count = len(lines)

    def tail(self, count: int) -> list[dict]:
        """返回最近 ``count`` 条记录（按时间正序）。"""
        if not self.path.exists() or count <= 0:
            return []
        lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        records: list[dict] = []
        for line in lines[-count:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def recent_prices(self, count: int) -> list[float]:
        """返回最近 ``count`` 个价格（用于画走势）。"""
        prices: list[float] = []
        for record in self.tail(count):
            try:
                prices.append(float(record["price"]))
            except (KeyError, TypeError, ValueError):
                continue
        return prices

    def records_since(self, epoch: float, lookback: int = 20000) -> list[dict]:
        """返回时间戳不早于 ``epoch`` 的记录。"""
        cutoff = float(epoch)
        return [
            record
            for record in self.tail(lookback)
            if isinstance(record.get("t"), (int, float)) and record["t"] >= cutoff
        ]
    def price_at_or_before(self, epoch: float, lookback: int = 20000):
        """返回不晚于 ``epoch`` 的最近一条历史 (price, t)，找不到返回 None。"""
        best = None
        for record in self.tail(lookback):
            stamp = record.get("t")
            price = record.get("price")
            if stamp is None or price is None or stamp > epoch:
                continue
            if best is None or stamp > best[1]:
                best = (float(price), float(stamp))
        return best


class StateStore:
    """保存运行期状态（如定时播报的下次触发时间）的小型 JSON 文件。"""

    def __init__(self, path: str) -> None:
        self.path = ensure_parent(path)
        self.data: dict = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data = loaded
            except json.JSONDecodeError:
                self.data = {}

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        with self.path.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)