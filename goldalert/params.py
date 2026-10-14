"""设置页上能改的核心参数：编目、读取、校验、保存。

参数分两类存放：
- `levels`：关键价位提醒的灵敏度，存在 `data/levels.json`（和价位放一起）；
- `config`：其它三类（异常波动 / 定时播报 / AI），写回 `config.json`。

页面只认这里的编目，改了编目页面上就自动多一项，说明也在这一处维护。
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Optional

from .config import Config

PARAM_GROUPS = [
    {
        "id": "levels",
        "source": "levels",
        "title": "关键价位提醒",
        "note": "价格靠近或突破你设的价位时提醒。要设的价位在上面那张表里增删改。",
        "params": [
            {
                "key": "enabled", "label": "总开关", "type": "bool",
                "meaning": "关掉后完全不再做关键价位提醒（你设的价位仍然保留）。",
            },
            {
                "key": "notify_near", "label": "接近提醒", "type": "bool",
                "meaning": "价格进入某个价位附近时，提醒一次。",
            },
            {
                "key": "notify_break", "label": "突破提醒", "type": "bool",
                "meaning": "价格从一侧穿到另一侧（上破或跌破）时，提醒一次。",
            },
            {
                "key": "proximity_usd", "label": "接近距离", "type": "number",
                "unit": "美元", "step": 0.1, "min": 0.1,
                "meaning": "价格离价位多近算「接近」。默认 2：进入 2 美元以内就提醒。",
            },
            {
                "key": "cooldown_minutes", "label": "冷却时间", "type": "number",
                "unit": "分钟", "step": 1, "min": 0,
                "meaning": "同一个价位的同一类提醒（接近、突破各算一份）在这段时间内只提醒一次，"
                           "防止反复刷屏。默认 10 分钟。",
            },
            {
                "key": "rearm_usd", "label": "重新武装距离", "type": "number",
                "unit": "美元", "step": 0.1, "min": 0,
                "meaning": "价格要离开「接近距离 + 这个值」之后才算离开，之后才会再提醒。默认 3 美元。",
            },
            {
                "key": "notify_ai_update", "label": "AI 换价位时发邮件", "type": "bool",
                "meaning": "AI 重新给出关键价位、且和上一轮不一样时，单独发一封邮件通知你。",
            },
        ],
    },
    {
        "id": "volatility",
        "source": "config",
        "title": "异常波动提醒",
        "note": "短时间上下摆得太多就提醒，抓的是「这一波明显在动」。",
        "params": [
            {
                "key": "volatility.enabled", "label": "总开关", "type": "bool",
                "meaning": "关掉后不再检查异常波动。",
            },
            {
                "key": "volatility.window_minutes", "label": "观察窗口", "type": "number",
                "unit": "分钟", "step": 1, "min": 1,
                "meaning": "只看最近这段时间收到的价格，更早的自动滑出窗口。默认 5 分钟。",
            },
            {
                "key": "volatility.threshold_usd", "label": "波动阈值", "type": "number",
                "unit": "美元", "step": 0.5, "min": 0.5,
                "meaning": "窗口内的最高价与最低价相差这么多就算异常，发一封提醒。默认 10 美元。",
            },
            {
                "key": "volatility.cooldown_minutes", "label": "冷却时间", "type": "number",
                "unit": "分钟", "step": 1, "min": 0,
                "meaning": "两次波动提醒之间至少隔这么久，防止连发。默认 10 分钟。",
            },
            {
                "key": "volatility.rearm_ratio", "label": "重新武装比例", "type": "number",
                "step": 0.05, "min": 0.05, "max": 0.95,
                "meaning": "波动要收窄到「阈值 × 这个比例」以内，才算平静下来、可以盯下一波。"
                           "默认 0.6（10 美元 × 0.6 = 6 美元）。",
            },
        ],
    },
    {
        "id": "report",
        "source": "config",
        "title": "定时播报",
        "note": "到点自动发一封行情邮件，末尾附 AI 盘面小结。",
        "params": [
            {
                "key": "report.enabled", "label": "总开关", "type": "bool",
                "meaning": "关掉后不再定时发播报邮件。",
            },
            {
                "key": "report.every_minutes", "label": "播报间隔", "type": "int",
                "unit": "分钟", "step": 5, "min": 1,
                "meaning": "每隔多少分钟发一封。默认 60，也就是一小时一封。",
            },
            {
                "key": "report.align_to_clock", "label": "对齐整点", "type": "bool",
                "meaning": "打开时按整点发（10:00、11:00……）；关掉就从启动时刻往后顺延。",
            },
            {
                "key": "report.compare_window_minutes", "label": "对比窗口", "type": "int",
                "unit": "分钟", "step": 5, "min": 0,
                "meaning": "邮件里那句「较 xx 分钟前」是跟多久之前的价格做对比。默认 60。",
            },
        ],
    },
    {
        "id": "ai",
        "source": "config",
        "title": "AI 分析（DeepSeek）",
        "note": "让 AI 读最近的价格序列，写盘面小结、给关键价位。API Key 仍在 .env 里，网页不改它。",
        "params": [
            {
                "key": "ai.enabled", "label": "总开关", "type": "bool",
                "meaning": "关掉后不再调用 DeepSeek，播报邮件里就没有 AI 小结了。",
            },
            {
                "key": "ai.lookback_hours", "label": "回看时长", "type": "number",
                "unit": "小时", "step": 1, "min": 0.5,
                "meaning": "把最近这段时间的价格交给 AI 看。默认 4 小时。",
            },
            {
                "key": "ai.max_points", "label": "采样点数", "type": "int",
                "unit": "个", "step": 8, "min": 2,
                "meaning": "这段时间的数据要压缩成多少个点喂给 AI。默认 48 个，"
                           "也就是 4 小时内大约每 5 分钟一个点。",
            },
            {
                "key": "levels.ai_enabled", "label": "自动给关键价位", "type": "bool",
                "meaning": "让 AI 定时自动给你一组关键价位（你手动加的和采纳进来的不受影响）。",
            },
            {
                "key": "levels.ai_refresh_minutes", "label": "AI 刷新间隔", "type": "number",
                "unit": "分钟", "step": 10, "min": 1,
                "meaning": "AI 每隔多久重新给一次关键价位。默认 60 分钟。",
            },
            {
                "key": "levels.ai_max_levels", "label": "AI 最多给几个", "type": "int",
                "unit": "个", "step": 1, "min": 1, "max": 10,
                "meaning": "AI 一次最多给出几个关键价位。默认 4 个。",
            },
        ],
    },
]

PARAM_INDEX = {
    param["key"]: (group["source"], param)
    for group in PARAM_GROUPS
    for param in group["params"]
}


# --------------------------------------------------------------------- 读取
def _value_of(key: str, config: Config, level_settings):
    """按 ``分组.字段`` 取值；不带点的键从 levels.json 的设置里取。"""
    if "." not in key:
        return getattr(level_settings, key, None)
    section, _, name = key.partition(".")
    holder = getattr(config, section, None)
    return getattr(holder, name, None) if holder is not None else None


def collect(config: Config, level_settings) -> list[dict]:
    """把编目和当前值拼成页面要的结构。"""
    groups: list[dict] = []
    for group in PARAM_GROUPS:
        params = []
        for param in group["params"]:
            item = dict(param)
            item["value"] = _value_of(param["key"], config, level_settings)
            params.append(item)
        groups.append(
            {
                "id": group["id"],
                "source": group["source"],
                "title": group["title"],
                "note": group["note"],
                "params": params,
            }
        )
    return groups


# --------------------------------------------------------------------- 校验
def _coerce(param: dict, raw):
    if param["type"] == "bool":
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "on", "yes", "是")
        return bool(raw)
    value = float(raw)
    if param["type"] == "int":
        return int(round(value))
    return value


def _range_problems(param: dict, value) -> list[str]:
    problems: list[str] = []
    unit = param.get("unit", "")
    if param.get("min") is not None and value < param["min"]:
        problems.append(f"{param['label']}不能小于 {param['min']:g}{unit}")
    if param.get("max") is not None and value > param["max"]:
        problems.append(f"{param['label']}不能大于 {param['max']:g}{unit}")
    return problems


def normalize(values: dict) -> tuple[dict, list[str]]:
    """把页面传来的值转成正确类型；返回 ``(转换后的值, 问题列表)``。"""
    cleaned: dict = {}
    problems: list[str] = []
    for key, raw in values.items():
        entry = PARAM_INDEX.get(key)
        if entry is None:
            problems.append(f"不认识的参数：{key}")
            continue
        _, param = entry
        try:
            value = _coerce(param, raw)
        except (TypeError, ValueError):
            problems.append(f"{param['label']}要填数字")
            continue
        problems.extend(_range_problems(param, value))
        cleaned[key] = value
    return cleaned, problems


# --------------------------------------------------------------------- 保存
def _dump_config(path: Path, data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _section_problems(config: Config, sections: set[str]) -> list[str]:
    problems: list[str] = []
    for section in sorted(sections):
        holder = getattr(config, section, None)
        if holder is not None and hasattr(holder, "problems"):
            problems.extend(holder.problems())
    return problems


def apply(config_path, level_store, values: dict) -> list[str]:
    """校验并保存；返回问题列表，空列表表示保存成功。"""
    cleaned, problems = normalize(values)
    if problems:
        return problems
    if not cleaned:
        return []

    config_path = Path(config_path)
    config_updates = {
        key: value for key, value in cleaned.items() if PARAM_INDEX[key][0] == "config"
    }
    levels_updates = {
        key: value for key, value in cleaned.items() if PARAM_INDEX[key][0] == "levels"
    }

    if config_updates:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        candidate = json.loads(json.dumps(raw))  # 先动副本，校验通过才落盘
        for key, value in config_updates.items():
            section, _, name = key.partition(".")
            block = candidate.setdefault(section, {})
            if not isinstance(block, dict):
                return [f"配置文件里的 {section} 段格式不对"]
            block[name] = value
        problems = _section_problems(
            Config.from_dict(candidate), {key.split(".")[0] for key in config_updates}
        )
        if problems:
            return problems
        _dump_config(config_path, candidate)

    if levels_updates:
        current = level_store.settings.to_dict()
        current.update(levels_updates)
        from .levels import LevelSettings

        settings = LevelSettings.from_dict(current)
        problems = settings.problems()
        if problems:
            return problems
        level_store.save_settings(settings)

    return []


# ------------------------------------------------------------- 运行中生效
def _copy_fields(target, source) -> None:
    for item in fields(source):
        setattr(target, item.name, getattr(source, item.name))


def sync_runtime(config: Config, new: Config, level_service=None, logger=None) -> None:
    """把新读到的配置就地刷进正在运行的对象，避免重启监控。"""
    _copy_fields(config.report, new.report)
    _copy_fields(config.ai, new.ai)
    _copy_fields(config.volatility, new.volatility)
    _copy_fields(config.levels, new.levels)

    if level_service is not None:
        level_service.ai_refresh_minutes = float(new.levels.ai_refresh_minutes)
        level_service.ai_max_levels = int(new.levels.ai_max_levels)
        if not new.levels.ai_enabled:
            level_service.analyzer = None
        elif level_service.analyzer is None and new.ai.enabled:
            from .ai import MarketAnalyzer
            from .sources import create_session

            level_service.analyzer = MarketAnalyzer(config.ai, create_session(), logger)
    if logger is not None:
        logger.info("检测到 config.json 改动，参数已就地更新（无需重启）")