"""配置加载与校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_PROXY_MODES = {
    "tradingview": "always",
    "sina": "auto",
    "tencent": "auto",
    "gold_api": "auto",
}

# 代理模式：always=只走代理，never=只直连，auto=先直连失败再走代理
VALID_PROXY_MODES = ("always", "never", "auto")


@dataclass
class EmailConfig:
    """邮件通知配置。密码不写在这里，而是从环境变量读取。"""

    enabled: bool = False
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    use_ssl: bool = True
    use_starttls: bool = False
    username: str = ""
    sender: str = ""
    recipients: tuple[str, ...] = ()
    password_env: str = "GOLDALERT_SMTP_PASSWORD"
    subject_prefix: str = "[GoldAlert 金价提醒]"
    timeout: float = 20.0

    @classmethod
    def from_dict(cls, data: dict) -> "EmailConfig":
        known = {f.name for f in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known}
        if isinstance(kwargs.get("recipients"), list):
            kwargs["recipients"] = tuple(kwargs["recipients"])
        return cls(**kwargs)

    def problems(self) -> list[str]:
        issues: list[str] = []
        if not self.username:
            issues.append("email.username 未填写")
        if not self.recipients:
            issues.append("email.recipients 未填写")
        if self.use_starttls and self.use_ssl:
            issues.append("email 不能同时开启 SSL 和 STARTTLS")
        return issues


@dataclass
class ReportConfig:
    """定时价格播报配置。"""

    enabled: bool = True
    every_minutes: int = 60
    align_to_clock: bool = True
    compare_window_minutes: int = 60

    @classmethod
    def from_dict(cls, data: dict) -> "ReportConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def problems(self) -> list[str]:
        issues: list[str] = []
        if self.every_minutes < 1:
            issues.append("report.every_minutes 至少为 1")
        if self.every_minutes > 1440:
            issues.append("report.every_minutes 不能超过 1440（一天）")
        if self.compare_window_minutes < 0:
            issues.append("report.compare_window_minutes 不能为负")
        return issues


@dataclass
class AIConfig:
    """AI 盘面解读配置（DeepSeek，OpenAI 兼容接口，国内直连）。"""

    enabled: bool = True
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    api_key_env: str = "GOLDALERT_DEEPSEEK_API_KEY"
    lookback_hours: float = 4.0
    max_points: int = 48
    max_words: int = 150
    temperature: float = 0.3
    max_tokens: int = 512
    timeout: float = 30.0
    proxy: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "AIConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def problems(self) -> list[str]:
        issues: list[str] = []
        if not self.base_url:
            issues.append("ai.base_url 不能为空")
        if not self.model:
            issues.append("ai.model 不能为空")
        if self.lookback_hours <= 0:
            issues.append("ai.lookback_hours 必须大于 0")
        if self.max_points < 2:
            issues.append("ai.max_points 至少为 2")
        return issues


@dataclass
class LevelsConfig:
    """关键价位提醒的运行参数（价位本身存在 levels.json，由界面维护）。"""

    enabled: bool = True
    file: str = "data/levels.json"
    ai_enabled: bool = True
    ai_refresh_minutes: float = 60.0
    ai_max_levels: int = 4

    @classmethod
    def from_dict(cls, data: dict) -> "LevelsConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def problems(self) -> list[str]:
        issues: list[str] = []
        if not self.file:
            issues.append("levels.file 不能为空")
        if self.ai_refresh_minutes <= 0:
            issues.append("levels.ai_refresh_minutes 必须大于 0")
        if self.ai_max_levels < 1:
            issues.append("levels.ai_max_levels 至少为 1")
        return issues


@dataclass
class VolatilityConfig:
    """异常波动提醒：窗口内最高价与最低价差得太多就提醒。"""

    enabled: bool = True
    window_minutes: float = 5.0
    threshold_usd: float = 10.0
    cooldown_minutes: float = 10.0
    rearm_ratio: float = 0.6

    @classmethod
    def from_dict(cls, data: dict) -> "VolatilityConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def problems(self) -> list[str]:
        issues: list[str] = []
        if self.window_minutes <= 0:
            issues.append("volatility.window_minutes 必须大于 0")
        if self.threshold_usd <= 0:
            issues.append("volatility.threshold_usd 必须大于 0")
        if self.cooldown_minutes < 0:
            issues.append("volatility.cooldown_minutes 不能为负")
        if not 0 <= self.rearm_ratio < 1:
            issues.append("volatility.rearm_ratio 要在 0 和 1 之间")
        return issues


@dataclass
class Config:
    """运行配置，可从 ``config.json`` 覆盖默认值。"""

    symbol: str = "XAU"
    sources: tuple[str, ...] = ("tradingview", "sina", "tencent")
    proxy: str = "http://127.0.0.1:7890"
    proxy_modes: dict = field(default_factory=lambda: dict(DEFAULT_PROXY_MODES))
    tradingview_symbol: str = "OANDA:XAUUSD"

    interval_seconds: float = 5.0
    request_timeout: float = 10.0
    failure_threshold: int = 2
    proxy_check_interval: float = 15.0

    history_file: str = "data/history.jsonl"
    state_file: str = "data/state.json"
    log_file: str = "logs/monitor.log"
    max_history_points: int = 20000
    live_display: bool = True
    sparkline_points: int = 60

    email: EmailConfig = field(default_factory=EmailConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    levels: LevelsConfig = field(default_factory=LevelsConfig)
    volatility: VolatilityConfig = field(default_factory=VolatilityConfig)

    @classmethod
    def load(cls, path: str | Path = "config.json") -> "Config":
        """从 JSON 文件加载配置；文件不存在时返回默认配置。"""
        file = Path(path)
        if not file.exists():
            return cls()
        raw = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"配置文件格式错误，应为 JSON 对象: {file}")
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """按字段名过滤后再构造，未知键直接忽略。"""
        known = {f.name for f in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known}
        if isinstance(kwargs.get("sources"), list):
            kwargs["sources"] = tuple(kwargs["sources"])
        for key, parser in (
            ("email", EmailConfig),
            ("report", ReportConfig),
            ("ai", AIConfig),
            ("levels", LevelsConfig),
            ("volatility", VolatilityConfig),
        ):
            raw = data.get(key)
            if isinstance(raw, dict):
                kwargs[key] = parser.from_dict(raw)
        return cls(**kwargs)

    def proxy_mode_for(self, name: str) -> str:
        """返回某个数据源的代理模式。"""
        mode = self.proxy_modes.get(name, "auto")
        return mode if mode in VALID_PROXY_MODES else "auto"

    def validate(self) -> list[str]:
        """返回配置问题列表，空列表表示没问题。"""
        problems: list[str] = []
        if not self.sources:
            problems.append("sources 不能为空")
        if self.interval_seconds <= 0:
            problems.append("interval_seconds 必须大于 0")
        if self.failure_threshold < 1:
            problems.append("failure_threshold 至少为 1")
        for name in self.sources:
            mode = self.proxy_modes.get(name, "auto")
            if mode not in VALID_PROXY_MODES:
                problems.append(f"数据源 {name} 的代理模式 {mode!r} 无效")
        if self.email.enabled:
            problems.extend(self.email.problems())
        problems.extend(self.report.problems())
        if self.ai.enabled:
            problems.extend(self.ai.problems())
        if self.levels.enabled:
            problems.extend(self.levels.problems())
        if self.volatility.enabled:
            problems.extend(self.volatility.problems())
        return problems