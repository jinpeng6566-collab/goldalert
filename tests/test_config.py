"""配置加载与校验测试。"""

from __future__ import annotations

from goldalert.config import Config, ReportConfig


def test_defaults_are_valid():
    config = Config()
    assert config.validate() == []
    assert config.symbol == "XAU"
    assert config.sources[0] == "tradingview"
    assert config.proxy_mode_for("tradingview") == "always"
    assert config.proxy_mode_for("sina") == "auto"


def test_from_dict_ignores_unknown_keys_and_normalises_sources():
    config = Config.from_dict(
        {"symbol": "XAU", "sources": ["sina"], "interval_seconds": 3, "谁在说话": 1}
    )
    assert config.symbol == "XAU"
    assert config.sources == ("sina",)
    assert config.interval_seconds == 3


def test_load_missing_file_returns_defaults(tmp_path):
    config = Config.load(tmp_path / "不存在.json")
    assert config.symbol == "XAU"


def test_validate_rejects_empty_sources():
    problems = Config(sources=()).validate()
    assert any("sources" in problem for problem in problems)


def test_validate_catches_interval_and_proxy_mode():
    config = Config(sources=("sina",), interval_seconds=0, proxy_modes={"sina": "乱七八糟"})
    problems = config.validate()
    assert any("interval_seconds" in problem for problem in problems)
    assert any("代理模式" in problem for problem in problems)
def test_report_config_defaults_and_validation():
    config = Config()
    assert config.report.enabled is True
    assert config.report.every_minutes == 60
    assert config.report.problems() == []
    assert Config(report=ReportConfig(every_minutes=0)).report.problems()
    assert Config(report=ReportConfig(every_minutes=5000)).report.problems()


def test_config_parses_nested_report():
    config = Config.from_dict({"report": {"every_minutes": 15, "align_to_clock": False}})
    assert config.report.every_minutes == 15
    assert config.report.align_to_clock is False