"""关键价位：接近 / 突破提醒。

价位来源有两处：

- 手动：存在 ``data/levels.json``，配套的图形界面（``goldalert.levels_ui``）负责增删改；
- AI：让大模型根据最近采集的数据给一组支撑 / 阻力，自动写回同一个文件（只覆盖 AI 自己那条，
  永远不会动你手动加的价位）。

触发规则（每个价位各自独立）：

- 接近：价格进入 ``proximity_usd`` 区间时提醒一次；价格离开
  ``proximity_usd + rearm_usd`` 之后才重新武装，避免在边界反复抖动刷屏；
- 突破：价格从一侧穿到另一侧时提醒一次；随后进入冷却，冷却期内同一价位不再重复提醒；
- 程序刚启动、还没建立基准时只记录当前状态，不补发提醒。

所有异常都只记日志，绝不影响监控主循环。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from .notify import NotifierHub
from .sources import Quote
from .store import HistoryStore, StateStore
from .util import ensure_parent, fmt_dt, fmt_ts, now_cn

LEVEL_STATE_KEY = "level_state"
AI_STATE_KEY = "ai_levels_ts"

NEAR = "near"
BREAK = "break"

MANUAL = "manual"
AI = "ai"

SOURCE_LABEL = {MANUAL: "手动", AI: "AI 建议"}


@dataclass
class Level:
    """一个关注价位。"""

    price: float
    label: str = ""
    source: str = MANUAL
    enabled: bool = True
    id: str = ""

    def __post_init__(self) -> None:
        self.price = float(self.price)
        if not self.id:
            self.id = f"{self.source[:1] or 'x'}-{uuid4().hex[:6]}"

    @classmethod
    def from_dict(cls, data: dict) -> Optional["Level"]:
        try:
            price = float(data["price"])
        except (KeyError, TypeError, ValueError):
            return None
        if price <= 0:
            return None
        source = str(data.get("source") or MANUAL)
        if source not in (MANUAL, AI):
            source = MANUAL
        return cls(
            price=price,
            label=str(data.get("label") or ""),
            source=source,
            enabled=bool(data.get("enabled", True)),
            id=str(data.get("id") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "price": round(self.price, 4),
            "label": self.label,
            "source": self.source,
            "enabled": self.enabled,
        }


@dataclass
class LevelSettings:
    """价位提醒的开关与灵敏度，和价位一起存在 levels.json 里。"""

    enabled: bool = True
    notify_near: bool = True
    notify_break: bool = True
    proximity_usd: float = 2.0
    rearm_usd: float = 3.0
    cooldown_minutes: float = 10.0
    notify_ai_update: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "LevelSettings":
        settings = cls()
        for name in ("enabled", "notify_near", "notify_break", "notify_ai_update"):
            if name in data:
                setattr(settings, name, bool(data[name]))
        for name in ("proximity_usd", "rearm_usd", "cooldown_minutes"):
            if name in data:
                try:
                    setattr(settings, name, float(data[name]))
                except (TypeError, ValueError):
                    pass
        return settings

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "notify_near": self.notify_near,
            "notify_break": self.notify_break,
            "proximity_usd": self.proximity_usd,
            "rearm_usd": self.rearm_usd,
            "cooldown_minutes": self.cooldown_minutes,
            "notify_ai_update": self.notify_ai_update,
        }

    def problems(self) -> list[str]:
        issues: list[str] = []
        if self.proximity_usd <= 0:
            issues.append("接近距离必须大于 0")
        if self.rearm_usd < 0:
            issues.append("重新武装距离不能为负")
        if self.cooldown_minutes < 0:
            issues.append("冷却时间不能为负")
        return issues


class LevelStore:
    """levels.json 的读写：手动价位 + AI 价位 + 提醒设置。"""

    def __init__(self, path, logger: Optional[logging.Logger] = None) -> None:
        self.path = Path(path)
        self.logger = logger or logging.getLogger("goldalert.levels")
        self.settings = LevelSettings()
        self.levels: list[Level] = []
        self._mtime: Optional[float] = None
        self.load(create=True)

    # ------------------------------------------------------------------ 读取
    def load(self, create: bool = False) -> None:
        if not self.path.exists():
            if create:
                self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("价位文件读取失败，沿用上一份内容: %s", exc)
            return
        if not isinstance(raw, dict):
            self.logger.warning("价位文件格式不对（应为 JSON 对象），已忽略")
            return

        settings = raw.get("settings")
        self.settings = LevelSettings.from_dict(settings) if isinstance(settings, dict) else LevelSettings()

        levels: list[Level] = []
        for item in raw.get("levels") or []:
            if not isinstance(item, dict):
                continue
            level = Level.from_dict(item)
            if level is not None:
                levels.append(level)
        self.levels = levels
        self._mtime = self._stat_mtime()

    def load_if_changed(self) -> bool:
        """文件被界面改过就重新读一次，返回是否真的重读了。"""
        mtime = self._stat_mtime()
        if mtime is None or mtime == self._mtime:
            return False
        self.load()
        self.logger.info("价位文件已更新：%d 个价位", len(self.levels))
        return True

    def _stat_mtime(self) -> Optional[float]:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    # ------------------------------------------------------------------ 写入
    def save(self) -> None:
        payload = {
            "settings": self.settings.to_dict(),
            "levels": [level.to_dict() for level in self.levels],
        }
        target = ensure_parent(self.path)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._mtime = self._stat_mtime()

    # ------------------------------------------------------------------ 查询
    def all_levels(self, enabled_only: bool = False) -> list[Level]:
        if not enabled_only:
            return list(self.levels)
        return [level for level in self.levels if level.enabled]

    def by_source(self, source: str) -> list[Level]:
        return [level for level in self.levels if level.source == source]

    def get(self, level_id: str) -> Optional[Level]:
        for level in self.levels:
            if level.id == level_id:
                return level
        return None

    def nearest(self, price: float) -> Optional[Level]:
        if not self.levels:
            return None
        return min(self.levels, key=lambda level: abs(level.price - price))

    def sorted_by_distance(self, price: float) -> list[Level]:
        return sorted(self.levels, key=lambda level: abs(level.price - price))

    # ------------------------------------------------------------------ 修改
    def find_by_label(self, label: str) -> Optional[Level]:
        """按名称找价位（忽略首尾空格）；名称为空时永远返回 None。"""
        key = (label or "").strip()
        if not key:
            return None
        for level in self.levels:
            if level.label.strip() == key:
                return level
        return None

    def upsert(self, price: float, label: str = "", source: str = MANUAL) -> tuple[Level, bool]:
        """加一个价位；已经有同名价位时就地覆盖它，而不是新增一条。

        返回 ``(价位, 是否新建)``：True 表示新加了一条，False 表示覆盖了原来那条。
        名称留空时不做合并，直接新增。
        """
        price = float(price)
        label = (label or "").strip()
        existing = self.find_by_label(label)
        if existing is not None:
            existing.price = price
            existing.source = source
            existing.enabled = True
            self.save()
            return existing, False
        level = Level(price=price, label=label, source=source)
        self.levels.append(level)
        self.save()
        return level, True

    def add(self, price: float, label: str = "", source: str = MANUAL) -> Level:
        """只管加完之后的那个价位，不关心是新加的还是覆盖的。"""
        return self.upsert(price, label, source)[0]

    def remove(self, level_id: str) -> bool:
        before = len(self.levels)
        self.levels = [level for level in self.levels if level.id != level_id]
        if len(self.levels) == before:
            return False
        self.save()
        return True

    def clear_source(self, source: str) -> int:
        before = len(self.levels)
        self.levels = [level for level in self.levels if level.source != source]
        removed = before - len(self.levels)
        if removed:
            self.save()
        return removed

    def update(self, level_id: str, **changes) -> bool:
        level = self.get(level_id)
        if level is None:
            return False
        for name, value in changes.items():
            if not hasattr(level, name) or name == "id":
                continue
            setattr(level, name, value)
        level.price = float(level.price)
        level.label = str(level.label or "")
        level.enabled = bool(level.enabled)
        self.save()
        return True

    def replace_ai_levels(self, levels: list[Level]) -> int:
        """替换 AI 给出的价位，手动添加的一律保留。

        和手动价位重名的那几条会被丢掉（你的价位优先），返回丢掉的条数。
        """
        taken = {
            level.label.strip()
            for level in self.levels
            if level.source != AI and level.label.strip()
        }
        kept = [level for level in levels if level.label.strip() not in taken]
        self.levels = [level for level in self.levels if level.source != AI]
        for level in kept:
            level.source = AI
            self.levels.append(level)
        self.save()
        return len(levels) - len(kept)

    def save_settings(self, settings: LevelSettings) -> None:
        self.settings = settings
        self.save()


@dataclass
class Alert:
    """一次价位提醒。"""

    level: Level
    kind: str
    price: float
    direction: str
    distance: float
    when: datetime = field(default_factory=now_cn)

    @property
    def is_break(self) -> bool:
        return self.kind == BREAK


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class LevelWatcher:
    """根据价格跳动判断该不该提醒；自身不存状态，状态放在 StateStore 里。"""

    def __init__(self, state: StateStore, logger: Optional[logging.Logger] = None) -> None:
        self.state = state
        self.logger = logger or logging.getLogger("goldalert.levels")

    def check(
        self,
        quote: Quote,
        settings: LevelSettings,
        levels: list[Level],
        now: Optional[datetime] = None,
    ) -> list[Alert]:
        if not settings.enabled or not levels:
            return []

        now = now or now_cn()
        now_ts = now.timestamp()
        price = float(quote.price)
        proximity = max(settings.proximity_usd, 0.0)
        rearm = max(settings.rearm_usd, 0.0)
        cooldown = max(settings.cooldown_minutes, 0.0) * 60.0

        stored = self.state.get(LEVEL_STATE_KEY)
        state = dict(stored) if isinstance(stored, dict) else {}
        alerts: list[Alert] = []
        dirty = False

        for level in levels:
            if not level.enabled:
                continue
            entry = state.get(level.id)
            if not isinstance(entry, dict):
                entry = {}
                state[level.id] = entry
                dirty = True

            distance = price - level.price
            side = 1 if distance > 0 else (-1 if distance < 0 else 0)

            # 接近和突破各自计时：先靠近再突破时，两封都要发
            legacy = _to_float(entry.get("last_alert"))
            if "last_near" not in entry:
                entry["last_near"] = legacy
                dirty = True
            if "last_break" not in entry:
                entry["last_break"] = legacy
                dirty = True

            def cooled(kind: str) -> bool:
                return cooldown <= 0 or (now_ts - _to_float(entry.get(kind))) >= cooldown

            if "side" not in entry:
                # 第一次见：只建立基准，不补发提醒
                entry["side"] = side
                entry["near_armed"] = 1 if abs(distance) > proximity else 0
                entry.setdefault("last_alert", 0.0)
                dirty = True
                continue

            previous = int(entry.get("side") or 0)
            if settings.notify_break and previous and side and side != previous:
                if cooled("last_break"):
                    alerts.append(
                        Alert(level, BREAK, price, "up" if side > 0 else "down", distance, now)
                    )
                    entry["last_break"] = now_ts
                    entry["last_alert"] = now_ts
                    # 刚刚穿越这个价位，"接近"就没必要再补一条了
                    entry["near_armed"] = 0
                    dirty = True
            if entry.get("side") != side:
                entry["side"] = side
                dirty = True

            if settings.notify_near:
                armed = bool(entry.get("near_armed", 1))
                if abs(distance) <= proximity:
                    if armed and cooled("last_near"):
                        alerts.append(
                            Alert(
                                level,
                                NEAR,
                                price,
                                "up" if distance > 0 else "down",
                                distance,
                                now,
                            )
                        )
                        entry["last_near"] = now_ts
                        entry["last_alert"] = now_ts
                        entry["near_armed"] = 0
                        dirty = True
                elif abs(distance) > proximity + rearm:
                    if not armed:
                        entry["near_armed"] = 1
                        dirty = True

        keep = {level.id for level in levels}
        for key in [key for key in state if key not in keep]:
            state.pop(key, None)
            dirty = True

        if dirty:
            try:
                self.state.set(LEVEL_STATE_KEY, state)
            except OSError as exc:  # 状态写不进去不该影响提醒
                self.logger.warning("价位状态保存失败: %s", exc)
        return alerts

    def snapshot(self) -> dict:
        stored = self.state.get(LEVEL_STATE_KEY)
        return dict(stored) if isinstance(stored, dict) else {}


def format_alert(alert: Alert, quote: Quote) -> tuple[str, str, str]:
    """把提醒整理成 ``(标题, 正文, 级别)``。"""
    level = alert.level
    name = level.label or "未命名价位"
    if alert.is_break:
        word = "上破" if alert.direction == "up" else "跌破"
        title = f"🚨 {word}关键价位 {level.price:,.2f}"
        trigger = f"{word} {level.price:,.2f}（{name}）"
        severity = "critical"
    else:
        title = f"⚠️ 靠近关键价位 {level.price:,.2f}"
        trigger = f"接近 {level.price:,.2f}（{name}）"
        severity = "warning"

    above = alert.distance >= 0
    lines = [
        f"现货黄金 {quote.symbol}",
        f"时间       {fmt_dt(alert.when)}",
        "",
        f"现价       {quote.price:,.2f} 美元/盎司",
        f"触发       {trigger}",
        f"位置       现价{'高于' if above else '低于'}该价位 {abs(alert.distance):,.2f} 美元",
        f"来源       {SOURCE_LABEL.get(level.source, level.source)}",
        f"数据源     {quote.source}",
    ]
    return title, "\n".join(lines), severity


def format_levels(levels: list[Level], price: Optional[float] = None) -> str:
    """把价位列表排成纯文本，供命令行与邮件使用。"""
    if not levels:
        return "（还没有设置关键价位）"
    rows = []
    for level in sorted(levels, key=lambda item: item.price):
        gap = "" if price is None else f"   距现价 {price - level.price:+,.2f}"
        rows.append(
            f"  {level.price:>10,.2f}  {level.label or '未命名':<12s}"
            f" {SOURCE_LABEL.get(level.source, level.source)}{gap}"
        )
    return "\n".join(rows)


class LevelService:
    """把价位文件、触发判断、AI 刷新和通知串起来，供监控主循环调用。"""

    def __init__(
        self,
        store: LevelStore,
        watcher: LevelWatcher,
        notifier: NotifierHub,
        history: Optional[HistoryStore] = None,
        analyzer=None,
        state: Optional[StateStore] = None,
        ai_refresh_minutes: float = 60.0,
        ai_max_levels: int = 4,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.store = store
        self.watcher = watcher
        self.notifier = notifier
        self.history = history
        self.analyzer = analyzer
        self.state = state
        self.ai_refresh_minutes = float(ai_refresh_minutes)
        self.ai_max_levels = int(ai_max_levels)
        self.logger = logger or logging.getLogger("goldalert.levels")

    # ------------------------------------------------------------------ 主入口
    def on_quote(self, quote: Quote, now: Optional[datetime] = None) -> list[Alert]:
        now = now or now_cn()
        try:
            self.store.load_if_changed()
        except Exception as exc:
            self.logger.warning("重新读取价位失败: %s", exc)

        try:
            self.refresh_ai_levels(quote, now)
        except Exception as exc:  # AI 出问题绝不能影响触发检查
            self.logger.warning("AI 关键价位刷新失败: %s", exc)

        alerts = self.watcher.check(quote, self.store.settings, self.store.all_levels(), now)
        for alert in alerts:
            title, body, severity = format_alert(alert, quote)
            self.logger.info("%s | %s", title, body.replace("\n", " "))
            try:
                self.notifier.send(title, body, severity)
            except Exception as exc:
                self.logger.warning("价位提醒发送失败: %s", exc)
        return alerts

    # ------------------------------------------------------------------ AI
    def ai_due(self, now: Optional[datetime] = None) -> bool:
        if self.analyzer is None or not getattr(self.analyzer, "available", False):
            return False
        if self.state is None:
            return False
        now = now or now_cn()
        last = self.state.get(AI_STATE_KEY)
        try:
            last_ts = float(last)
        except (TypeError, ValueError):
            return True
        return (now.timestamp() - last_ts) >= self.ai_refresh_minutes * 60.0

    def refresh_ai_levels(self, quote: Quote, now: Optional[datetime] = None) -> list[Level]:
        """到点就让 AI 重新给一组关键价位；返回本次写入的价位（可能为空）。"""
        if not self.ai_due(now):
            return []
        records = []
        if self.history is not None:
            lookback = getattr(self.analyzer.config, "lookback_hours", 6.0)
            records = self.history.records_since(quote.fetched_at - lookback * 3600.0)
        if len(records) < 10:
            # 数据还太少，等下一轮再说
            return []

        suggestions = self.analyzer.suggest_levels(quote, records, self.ai_max_levels)
        if self.state is not None:
            self.state.set(AI_STATE_KEY, (now or now_cn()).timestamp())
        if not suggestions:
            self.logger.info("AI 本次没有给出关键价位")
            return []

        levels = [
            Level(price=item["price"], label=item.get("label") or "AI 关键价位", source=AI)
            for item in suggestions
        ]
        before = sorted(round(level.price, 2) for level in self.store.by_source(AI))
        skipped = self.store.replace_ai_levels(levels)
        kept = self.store.by_source(AI)
        after = sorted(round(level.price, 2) for level in kept)
        if skipped:
            self.logger.info(
                "AI 给的 %d 个价位和你已有的同名价位重了，已跳过（你的价位优先）", skipped
            )
        if not kept:
            self.logger.info("AI 这次给的价位都被同名价位挡掉了，没有写入新的")
            return []
        self.logger.info(
            "AI 关键价位已更新: %s",
            ", ".join(f"{level.price:,.2f}" for level in kept),
        )

        changed = before != after
        if not changed:
            self.logger.info("AI 给出的价位与上一次相同（只在变动时才通知），跳过邮件")
        if changed and self.store.settings.notify_ai_update:
            previous = "、".join(f"{price:,.2f}" for price in before) if before else "（上次还没有 AI 价位）"
            body = (
                f"现货黄金 {quote.symbol}\n"
                f"时间       {fmt_dt(now or now_cn())}\n"
                f"现价       {quote.price:,.2f} 美元/盎司\n\n"
                "AI 根据最近采集的数据，更新了关键价位：\n"
                f"上次       {previous}\n"
                "本次\n"
                f"{format_levels(kept, quote.price)}\n\n"
                "要调整或删除，打开「设置目标价位」窗口即可。"
            )
            try:
                self.notifier.send("🤖 AI 已更新关键价位", body, "info")
            except Exception as exc:
                self.logger.warning("AI 价位通知发送失败: %s", exc)
        return kept