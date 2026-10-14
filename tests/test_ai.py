"""AI 盘面解读测试（不联网）。"""

from __future__ import annotations

import requests

from goldalert.ai import MarketAnalyzer, build_prompt, downsample
from goldalert.config import AIConfig
from goldalert.sources import Quote

KEY_ENV = "GOLDALERT_DEEPSEEK_API_KEY"


class FakeResponse:
    def __init__(self, data=None, status: int = 200) -> None:
        self._data = data if data is not None else {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[tuple] = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_quote(price: float = 4200.0) -> Quote:
    return Quote(
        symbol="XAU", name="Gold", price=price, source="tradingview", fetched_at=0.0,
        prev_close=4171.65, high=4200.6, low=4150.8, bid=4199.9, ask=4200.1,
    )


def test_downsample_picks_evenly():
    records = [{"t": float(i), "price": 4000.0 + i} for i in range(10)]
    assert [r["price"] for r in downsample(records, 5)] == [4001.0, 4003.0, 4005.0, 4007.0, 4009.0]


def test_downsample_keeps_short_series():
    records = [{"t": 0.0, "price": 1.0}]
    assert downsample(records, 5) == records


def test_prompt_carries_price_and_rules():
    prompt = build_prompt(make_quote(4200.0), [{"t": 0.0, "price": 4190.0}], AIConfig())
    assert "4,200.00" in prompt
    assert "不要预测" in prompt
    assert "4190.00" in prompt


def test_unavailable_without_key(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    analyzer = MarketAnalyzer(AIConfig(), FakeSession(FakeResponse()))
    assert analyzer.available is False
    assert analyzer.analyze(make_quote(), []) is None


def test_disabled_even_with_key(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-test")
    analyzer = MarketAnalyzer(AIConfig(enabled=False), FakeSession(FakeResponse()))
    assert analyzer.available is False


def test_parses_successful_response(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-test")
    session = FakeSession(FakeResponse({"choices": [{"message": {"content": "  金价区间震荡。  "}}]}))
    analyzer = MarketAnalyzer(AIConfig(), session)

    assert analyzer.available is True
    assert analyzer.analyze(make_quote(), []) == "金价区间震荡。"

    url, kwargs = session.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert kwargs["json"]["model"] == "deepseek-chat"
    assert kwargs["json"]["stream"] is False


def test_returns_none_on_http_error(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-test")
    analyzer = MarketAnalyzer(AIConfig(), FakeSession(FakeResponse(status=401)))
    assert analyzer.analyze(make_quote(), []) is None


def test_returns_none_on_network_error(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-test")
    analyzer = MarketAnalyzer(AIConfig(), FakeSession(requests.ConnectionError("断了")))
    assert analyzer.analyze(make_quote(), []) is None


def test_returns_none_on_unexpected_shape(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-test")
    analyzer = MarketAnalyzer(AIConfig(), FakeSession(FakeResponse({"unexpected": 1})))
    assert analyzer.analyze(make_quote(), []) is None


def test_custom_proxy_is_passed(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-test")
    session = FakeSession(FakeResponse({"choices": [{"message": {"content": "ok"}}]}))
    analyzer = MarketAnalyzer(AIConfig(proxy="http://127.0.0.1:7890"), session)
    analyzer.analyze(make_quote(), [])
    assert session.calls[0][1]["proxies"] == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }

# ------------------------------------------------------------ 关键价位解析
from goldalert.ai import build_levels_prompt, parse_levels  # noqa: E402
from goldalert.sources import Quote  # noqa: E402


def make_quote(price: float = 4185.0) -> Quote:
    return Quote(symbol="XAU", name="Gold", price=price, source="t",
                 fetched_at=0.0, high=4200.62, low=4150.78)


def test_parse_levels_basic():
    text = '{"levels": [{"price": 4200.62, "label": "今日高点"}]}'
    assert parse_levels(text, 4185.0, 4) == [{"price": 4200.62, "label": "今日高点"}]


def test_parse_levels_accepts_code_fence_and_plain_list():
    assert parse_levels("```json\n[{\"price\": 4150.78}]\n```", 4185.0)[0]["price"] == 4150.78


def test_parse_levels_drops_absurd_and_duplicate():
    text = '{"levels": [{"price": 9999}, {"price": 4200.5, "label": "a"}, {"price": 4200.5, "label": "b"}]}'
    assert parse_levels(text, 4185.0, 4) == [{"price": 4200.5, "label": "a"}]


def test_parse_levels_handles_garbage():
    assert parse_levels("今天行情很安静", 4185.0) == []
    assert parse_levels("", 4185.0) == []
    assert parse_levels('{"levels": "不是列表"}', 4185.0) == []


def test_parse_levels_respects_max_levels():
    text = '{"levels": [{"price": 4180}, {"price": 4182}, {"price": 4184}, {"price": 4186}]}'
    assert len(parse_levels(text, 4185.0, 2)) == 2


def test_levels_prompt_mentions_json_and_bounds():
    config = type("C", (), {"lookback_hours": 6.0})()
    prompt = build_levels_prompt(make_quote(), [{"t": 1791000000, "price": 4184}], config, 4)
    assert "json" in prompt
    assert "4,200.62" in prompt and "4,150.78" in prompt


def test_parse_levels_drops_current_price_label():
    """AI 有时会把「当前报价」当关键价位报上来，这不是值得盯的位置，要丢掉。"""
    text = ('{"levels": [{"price": 4185.05, "label": "当前报价"}, '
            '{"price": 4200.62, "label": "今日高点"}]}')
    assert parse_levels(text, 4185.0, 4) == [{"price": 4200.62, "label": "今日高点"}]


def test_parse_levels_drops_all_price_style_labels():
    for label in ["现价", "最新报价", "当前价位", "当前价", "现价支撑"]:
        payload = '{"levels": [{"price": 4180.0, "label": "%s"}]}' % label
        assert parse_levels(payload, 4185.0, 4) == [], label


def test_parse_levels_keeps_normal_labels():
    for label in ["近期高点", "反复停留位", "下方支撑", "上方压力"]:
        payload = '{"levels": [{"price": 4180.0, "label": "%s"}]}' % label
        assert parse_levels(payload, 4185.0, 4) == [{"price": 4180.0, "label": label}], label


def test_levels_prompt_tells_model_to_skip_current_price():
    config = type("C", (), {"lookback_hours": 6.0})()
    prompt = build_levels_prompt(make_quote(), [{"t": 1791000000, "price": 4184}], config, 4)
    assert "现价不是关键价位" in prompt
    assert "当前报价" in prompt


# ------------------------------------------------------------ AI 提问
from goldalert.ai import ASK_SYSTEM_PROMPT, build_ask_prompt  # noqa: E402


def test_ask_builds_prompt_and_parses(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-test")
    session = FakeSession(FakeResponse({"choices": [{"message": {"content": "  目前是区间震荡。  "}}]}))
    analyzer = MarketAnalyzer(AIConfig(), session)

    answer = analyzer.ask(make_quote(), [{"t": 0.0, "price": 4190.0}], "现在是震荡还是单边?")
    assert answer == "目前是区间震荡。"

    url, kwargs = session.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert kwargs["json"]["messages"][0]["content"] == ASK_SYSTEM_PROMPT
    assert "现在是震荡还是单边?" in kwargs["json"]["messages"][1]["content"]
    assert kwargs["json"].get("response_format") is None


def test_ask_requires_question(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-test")
    session = FakeSession(FakeResponse())
    analyzer = MarketAnalyzer(AIConfig(), session)
    assert analyzer.ask(make_quote(), [], "   ") is None
    assert session.calls == []


def test_ask_unavailable_without_key(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    analyzer = MarketAnalyzer(AIConfig(), FakeSession(FakeResponse()))
    assert analyzer.ask(make_quote(), [], "现在多少?") is None


def test_ask_prompt_states_limits():
    prompt = build_ask_prompt(make_quote(), [{"t": 0.0, "price": 4190.0}], AIConfig(), "现在是震荡吗")
    assert "现在是震荡吗" in prompt
    assert "4190.00" in prompt
    assert "无法依据现有数据回答" in prompt