"""行情源解析与代理降级测试。"""

from __future__ import annotations

import pytest
import requests

from goldalert.sources import (
    GoldApiSource,
    ProxyError,
    SinaSource,
    SourceError,
    TencentSource,
    TradingViewSource,
)

from conftest_helpers import FakeResponse, FakeSession, SeqSession

SINA_FIELDS = [
    "4188.17", "4171.480", "4188.17", "4188.52", "4200.40", "4150.72",
    "17:07:00", "4171.48", "4172.45", "0", "0", "0", "2026-10-13", "伦敦金现货报价",
]
SINA_TEXT = 'var hq_str_hf_XAU="' + ",".join(SINA_FIELDS) + '";'
TENCENT_TEXT = 'v_hf_XAU="' + ",".join(SINA_FIELDS) + '";'


def test_sina_parse_fields():
    result = SinaSource.parse("XAU", SINA_TEXT)
    assert result.price == 4188.17
    assert result.prev_close == 4171.48
    assert result.high == 4200.40
    assert result.low == 4150.72
    assert result.open == 4172.45
    assert result.source == "sina"
    assert result.change_pct == pytest.approx(0.4001, abs=1e-3)


def test_tencent_parse_fields():
    result = TencentSource.parse("XAU", TENCENT_TEXT)
    assert result.price == 4188.17
    assert result.prev_close == 4171.48


def test_empty_payload_raises():
    with pytest.raises(SourceError):
        SinaSource.parse("XAU", 'var hq_str_hf_XAU="";')


def test_tradingview_maps_change_abs_to_prev_close():
    payload = {
        "close": 4186.79, "change_abs": 15.14, "high": 4200.6, "low": 4150.8,
        "open": 4174.4, "bid": 4186.47, "ask": 4186.66, "volume": 228844,
        "description": "Gold", "currency": "USD",
    }
    source = TradingViewSource(
        FakeSession(FakeResponse(json_data=payload)),
        timeout=5.0, proxy="http://127.0.0.1:7890", proxy_mode="always",
        tv_symbol="OANDA:XAUUSD",
    )
    result = source.fetch("XAU")
    assert result.price == 4186.79
    assert round(result.prev_close, 2) == round(4186.79 - 15.14, 2)
    assert result.bid == 4186.47
    assert result.symbol == "OANDA:XAUUSD"


def test_tradingview_unknown_symbol_raises_source_error():
    source = TradingViewSource(
        FakeSession(FakeResponse(text='{"code":"symbol_not_exists"}', status=404)),
        timeout=5.0, proxy="http://127.0.0.1:7890", proxy_mode="always",
    )
    with pytest.raises(SourceError):
        source.fetch("XAU")


def test_proxy_failure_is_classified_as_proxy_error():
    source = TradingViewSource(
        FakeSession(requests.exceptions.ProxyError("cannot connect")),
        timeout=5.0, proxy="http://127.0.0.1:9999", proxy_mode="always",
    )
    with pytest.raises(ProxyError):
        source.fetch("XAU")


def test_auto_mode_falls_back_to_proxy():
    session = SeqSession([
        requests.exceptions.ConnectionError("直连被拒"),
        FakeResponse(text=SINA_TEXT),
    ])
    source = SinaSource(session, timeout=5.0,
                        proxy="http://127.0.0.1:7890", proxy_mode="auto")
    result = source.fetch("XAU")
    assert result.price == 4188.17
    assert session.proxies_seen == [
        None,
        {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
    ]


def test_never_mode_does_not_touch_proxy():
    session = FakeSession(FakeResponse(json_data={"price": 4180.0, "symbol": "XAU", "name": "Gold"}))
    source = GoldApiSource(session, timeout=5.0,
                           proxy="http://127.0.0.1:7890", proxy_mode="never")
    result = source.fetch("XAU")
    assert result.price == 4180.0
    assert session.calls[0]["proxies"] is None