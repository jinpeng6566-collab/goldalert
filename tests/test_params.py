"""设置页可调参数：编目、校验、保存、热更新。"""

from __future__ import annotations

import json

from goldalert.config import Config
from goldalert.levels import LevelStore
from goldalert.params import (
    PARAM_GROUPS,
    PARAM_INDEX,
    apply,
    collect,
    normalize,
    sync_runtime,
)


def sample_config() -> dict:
    return {
        "symbol": "XAU",
        "sources": ["tradingview", "sina", "tencent"],
        "interval_seconds": 5,
        "report": {"enabled": True, "every_minutes": 60, "align_to_clock": True,
                   "compare_window_minutes": 60},
        "ai": {"enabled": True, "base_url": "https://api.deepseek.com",
               "model": "deepseek-chat", "lookback_hours": 4, "max_points": 48,
               "api_key_env": "GOLDALERT_DEEPSEEK_API_KEY"},
        "levels": {"enabled": True, "file": "levels.json", "ai_enabled": True,
                   "ai_refresh_minutes": 60, "ai_max_levels": 4},
        "volatility": {"enabled": True, "window_minutes": 5, "threshold_usd": 10,
                       "cooldown_minutes": 10, "rearm_ratio": 0.6},
        "email": {"enabled": True, "username": "me@qq.com",
                  "recipients": ["me@qq.com"]},
    }


def write_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(sample_config(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


# ------------------------------------------------------------------ 编目
def test_catalog_is_well_formed():
    assert PARAM_GROUPS, "至少要有一组参数"
    keys = [p["key"] for g in PARAM_GROUPS for p in g["params"]]
    assert len(keys) == len(set(keys)), "参数键不能重复"
    for group in PARAM_GROUPS:
        assert group["source"] in ("config", "levels")
        assert group["title"] and group["note"]
        for param in group["params"]:
            assert param["type"] in ("bool", "int", "number")
            assert param["label"] and param["meaning"], f"{param['key']} 缺少说明"
    assert "volatility.threshold_usd" in PARAM_INDEX
    assert "proximity_usd" in PARAM_INDEX


def test_collect_returns_current_values(tmp_path):
    store = LevelStore(str(tmp_path / "levels.json"))
    config = Config.from_dict(sample_config())
    groups = collect(config, store.settings)

    by_title = {g["title"]: g for g in groups}
    assert set(by_title) == {"关键价位提醒", "异常波动提醒", "定时播报", "AI 分析（DeepSeek）"}

    def value_of(group, label):
        return next(p["value"] for p in by_title[group]["params"] if p["label"] == label)

    assert value_of("异常波动提醒", "波动阈值") == 10
    assert value_of("定时播报", "播报间隔") == 60
    assert value_of("AI 分析（DeepSeek）", "回看时长") == 4
    assert value_of("关键价位提醒", "接近距离") == store.settings.proximity_usd


# ------------------------------------------------------------------ 校验
def test_normalize_coerces_and_reports():
    cleaned, problems = normalize({
        "volatility.threshold_usd": "8",
        "report.every_minutes": "30",
        "volatility.enabled": False,
        "levels.ai_max_levels": 3.4,
    })
    assert problems == []
    assert cleaned["volatility.threshold_usd"] == 8.0
    assert cleaned["report.every_minutes"] == 30 and isinstance(cleaned["report.every_minutes"], int)
    assert cleaned["volatility.enabled"] is False
    assert cleaned["levels.ai_max_levels"] == 3

    _, problems = normalize({"volatility.threshold_usd": "abc"})
    assert problems and "数字" in problems[0]

    _, problems = normalize({"volatility.rearm_ratio": 1.5})
    assert problems and "不能大于" in problems[0]

    _, problems = normalize({"volatility.window_minutes": 0})
    assert problems and "不能小于" in problems[0]

    _, problems = normalize({"没这个参数": 1})
    assert problems and "不认识" in problems[0]


# ------------------------------------------------------------------ 保存
def test_apply_writes_config_and_keeps_the_rest(tmp_path):
    path = write_config(tmp_path)
    store = LevelStore(str(tmp_path / "levels.json"))

    problems = apply(path, store, {
        "volatility.threshold_usd": 8,
        "volatility.window_minutes": 3,
        "report.every_minutes": 30,
        "ai.enabled": False,
    })
    assert problems == []

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["volatility"]["threshold_usd"] == 8
    assert saved["volatility"]["window_minutes"] == 3
    assert saved["volatility"]["rearm_ratio"] == 0.6, "没改的字段要原样保留"
    assert saved["report"]["every_minutes"] == 30
    assert saved["report"]["align_to_clock"] is True
    assert saved["ai"]["enabled"] is False
    assert saved["symbol"] == "XAU" and saved["sources"] == ["tradingview", "sina", "tencent"]
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_apply_rejects_bad_values_without_touching_file(tmp_path):
    path = write_config(tmp_path)
    store = LevelStore(str(tmp_path / "levels.json"))
    before = path.read_text(encoding="utf-8")

    problems = apply(path, store, {"volatility.rearm_ratio": 2.0})
    assert problems
    assert path.read_text(encoding="utf-8") == before

    problems = apply(path, store, {"ai.lookback_hours": 0})
    assert problems
    assert path.read_text(encoding="utf-8") == before


def test_apply_writes_levels_settings(tmp_path):
    path = write_config(tmp_path)
    store = LevelStore(str(tmp_path / "levels.json"))

    problems = apply(path, store, {"proximity_usd": 3.5, "notify_break": False})
    assert problems == []
    assert store.settings.proximity_usd == 3.5
    assert store.settings.notify_break is False
    # 重新从文件读一遍，确认真的落盘了
    assert LevelStore(str(tmp_path / "levels.json")).settings.proximity_usd == 3.5
    # levels 组的参数不该写进 config.json
    assert "proximity_usd" not in json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------- 运行中生效
def test_sync_runtime_updates_objects_in_place(tmp_path):
    config = Config.from_dict(sample_config())
    new = Config.from_dict(sample_config())
    new.volatility.threshold_usd = 7
    new.report.every_minutes = 15
    new.ai.max_points = 96
    new.levels.ai_max_levels = 6
    new.levels.ai_enabled = False          # 顺带把 AI 自动给价位关掉
    new.ai.enabled = True

    class FakeService:
        def __init__(self):
            self.ai_refresh_minutes = 60.0
            self.ai_max_levels = 4
            self.analyzer = "旧的"

    service = FakeService()
    sync_runtime(config, new, level_service=service)

    # 就地改：拿旧引用的组件也能看到新值
    assert config.volatility.threshold_usd == 7
    assert config.report.every_minutes == 15
    assert config.ai.max_points == 96
    assert service.ai_max_levels == 6
    assert service.analyzer is None, "关掉 AI 自动给价位后要停用分析器"

    newer = Config.from_dict(sample_config())   # 又打开 AI 自动给价位
    sync_runtime(config, newer, level_service=service)
    assert service.analyzer is not None, "重新打开后要能把分析器装回去"