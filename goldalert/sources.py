"""实时金价行情源。

主源 TradingView（经代理访问），失败时自动回退到国内免密钥源：

- ``tradingview`` : scanner.tradingview.com，OANDA:XAUUSD，通常需要代理
- ``sina``        : 新浪财经 hq.sinajs.cn，伦敦金现货报价，需要 Referer 头
- ``tencent``     : 腾讯财经 qt.gtimg.cn，伦敦金现货报价
- ``gold_api``    : api.gold-api.com，JSON 接口，免代理

每个源支持三种代理模式：``always`` 只走代理、``never`` 只直连、
``auto`` 先直连失败再走代理。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class SourceError(RuntimeError):
    """行情源取数失败（网络异常或返回数据不可解析）。"""


class ProxyError(SourceError):
    """代理不可用——区别于普通取数失败，用于触发"代理掉了"提醒。"""


@dataclass
class Quote:
    """一条金价行情快照。"""

    symbol: str
    name: str
    price: float
    source: str
    fetched_at: float
    currency: str = "USD"
    unit: str = "oz"
    prev_close: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: Optional[float] = None
    quote_time: Optional[str] = None

    @property
    def change(self) -> Optional[float]:
        """相对昨收的涨跌额。"""
        if self.prev_close:
            return self.price - self.prev_close
        return None

    @property
    def change_pct(self) -> Optional[float]:
        """相对昨收的涨跌幅（百分比）。"""
        if self.prev_close:
            return (self.price - self.prev_close) / self.prev_close * 100.0
        return None


def create_session() -> requests.Session:
    """创建一个带自动重试、且忽略系统环境代理的 HTTP 会话。"""
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class PriceSource:
    """行情源基类。"""

    name = "base"

    def __init__(
        self,
        session: requests.Session,
        timeout: float = 10.0,
        proxy: Optional[str] = None,
        proxy_mode: str = "never",
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.proxy = proxy
        self.proxy_mode = proxy_mode

    def describe(self) -> str:
        return f"{self.name}（{self.proxy_mode}）"

    def _attempts(self) -> list[Optional[str]]:
        """返回本次要依次尝试的通道：``None`` 表示直连。"""
        if self.proxy_mode == "always":
            return [self.proxy] if self.proxy else [None]
        if self.proxy_mode == "never":
            return [None]
        return [None, self.proxy] if self.proxy else [None]

    @staticmethod
    def _proxies(proxy: Optional[str]) -> Optional[dict]:
        return {"http": proxy, "https": proxy} if proxy else None

    def _get(
        self,
        url: str,
        headers: Optional[dict] = None,
        proxy: Optional[str] = None,
        params: Optional[dict] = None,
    ) -> requests.Response:
        try:
            return self.session.get(
                url,
                headers=headers or {},
                params=params,
                timeout=self.timeout,
                proxies=self._proxies(proxy),
            )
        except requests.exceptions.ProxyError as exc:
            raise ProxyError(f"代理不可用（{proxy}）: {exc}") from exc
        except requests.RequestException as exc:
            raise SourceError(f"{self.name} 请求失败: {exc}") from exc

    def _fetch_via(self, symbol: str, proxy: Optional[str]) -> Quote:  # pragma: no cover
        raise NotImplementedError

    def fetch(self, symbol: str) -> Quote:
        """按代理策略依次尝试，任一成功即返回。"""
        last_error: Optional[Exception] = None
        for proxy in self._attempts():
            try:
                return self._fetch_via(symbol, proxy)
            except (ProxyError, SourceError) as exc:
                last_error = exc
        if last_error is None:
            last_error = SourceError(f"{self.name} 没有可用通道")
        raise last_error

    @staticmethod
    def _to_float(value) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _from_parts(parts: list[str], index: int) -> Optional[float]:
        try:
            return float(parts[index])
        except (IndexError, ValueError):
            return None


class TradingViewSource(PriceSource):
    """TradingView 报价接口，默认取 ``OANDA:XAUUSD``。"""

    name = "tradingview"
    url = "https://scanner.tradingview.com/symbol"
    fields = (
        "close,change,change_abs,high,low,open,bid,ask,"
        "volume,update_mode,description,currency"
    )
    default_tv_symbol = "OANDA:XAUUSD"

    def __init__(
        self,
        session: requests.Session,
        timeout: float = 10.0,
        proxy: Optional[str] = None,
        proxy_mode: str = "always",
        tv_symbol: Optional[str] = None,
    ) -> None:
        super().__init__(session, timeout, proxy, proxy_mode)
        self.tv_symbol = tv_symbol or self.default_tv_symbol

    def _fetch_via(self, symbol: str, proxy: Optional[str]) -> Quote:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/",
        }
        resp = self._get(
            self.url,
            headers=headers,
            proxy=proxy,
            params={"symbol": self.tv_symbol, "fields": self.fields},
        )
        if resp.status_code == 404 or "symbol_not_exists" in resp.text[:200]:
            raise SourceError(f"TradingView 不认识该代码: {self.tv_symbol}")
        if resp.status_code >= 400:
            raise SourceError(f"tradingview HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceError(f"tradingview 返回非 JSON: {exc}") from exc

        price = self._to_float(data.get("close"))
        if price is None:
            raise SourceError(f"tradingview 未返回价格: {str(data)[:120]}")

        change_abs = self._to_float(data.get("change_abs"))
        prev_close = price - change_abs if change_abs is not None else None

        return Quote(
            symbol=self.tv_symbol,
            name=str(data.get("description") or "Gold"),
            price=price,
            source=self.name,
            fetched_at=time.time(),
            currency=str(data.get("currency") or "USD"),
            unit="oz",
            prev_close=prev_close,
            open=self._to_float(data.get("open")),
            high=self._to_float(data.get("high")),
            low=self._to_float(data.get("low")),
            bid=self._to_float(data.get("bid")),
            ask=self._to_float(data.get("ask")),
            volume=self._to_float(data.get("volume")),
        )


class _CsvSource(PriceSource):
    """新浪 / 腾讯共用的一套 ``var xxx="a,b,c,..."`` 解析逻辑。"""

    codes: dict[str, str] = {}

    def code_for(self, symbol: str) -> str:
        upper = symbol.upper()
        return self.codes.get(upper, f"hf_{upper}")

    @staticmethod
    def _fields_of(text: str) -> list[str]:
        match = re.search(r'="([^"]*)"', text)
        if not match:
            raise SourceError(f"返回格式异常: {text[:100]!r}")
        return match.group(1).split(",")


class SinaSource(_CsvSource):
    """新浪财经伦敦金现货报价。

    ``hf_`` 系列字段::

        0 当前价 / 4 最高 / 5 最低 / 6 时间 / 7 昨收 / 8 开盘 / 12 日期 / 13 名称
    """

    name = "sina"
    url_template = "https://hq.sinajs.cn/list={code}"
    codes = {"XAU": "hf_XAU", "GC": "hf_GC", "XAG": "hf_XAG"}

    def _fetch_via(self, symbol: str, proxy: Optional[str]) -> Quote:
        code = self.code_for(symbol)
        resp = self._get(
            self.url_template.format(code=code),
            headers={"Referer": "https://finance.sina.com.cn"},
            proxy=proxy,
        )
        if resp.status_code >= 400:
            raise SourceError(f"sina HTTP {resp.status_code}")
        resp.encoding = "gbk"
        return self.parse(symbol, resp.text)

    @classmethod
    def parse(cls, symbol: str, text: str) -> Quote:
        parts = cls._fields_of(text)
        if len(parts) < 8 or not parts[0].strip():
            raise SourceError(f"数据为空: {text[:100]!r}")
        price = cls._from_parts(parts, 0)
        if price is None:
            raise SourceError(f"价格字段解析失败: {parts[:3]}")
        name = parts[13].strip() if len(parts) > 13 and parts[13].strip() else "伦敦金现货"
        date = parts[12].strip() if len(parts) > 12 else ""
        clock = parts[6].strip() if len(parts) > 6 else ""
        return Quote(
            symbol=symbol.upper(),
            name=name,
            price=price,
            source=cls.name,
            fetched_at=time.time(),
            prev_close=cls._from_parts(parts, 7),
            open=cls._from_parts(parts, 8),
            high=cls._from_parts(parts, 4),
            low=cls._from_parts(parts, 5),
            quote_time=f"{date} {clock}".strip(),
        )


class TencentSource(_CsvSource):
    """腾讯财经伦敦金现货报价。

    ``hf_`` 系列字段::

        0 当前价 / 1 涨跌幅% / 4 最高 / 5 最低 / 6 时间 / 7 昨收 / 8 开盘
    """

    name = "tencent"
    url_template = "https://qt.gtimg.cn/q={code}"
    codes = {"XAU": "hf_XAU", "GC": "hf_GC", "XAG": "hf_XAG"}

    def _fetch_via(self, symbol: str, proxy: Optional[str]) -> Quote:
        code = self.code_for(symbol)
        resp = self._get(self.url_template.format(code=code), proxy=proxy)
        if resp.status_code >= 400:
            raise SourceError(f"tencent HTTP {resp.status_code}")
        resp.encoding = "gbk"
        return self.parse(symbol, resp.text)

    @classmethod
    def parse(cls, symbol: str, text: str) -> Quote:
        parts = cls._fields_of(text)
        if len(parts) < 8 or not parts[0].strip():
            raise SourceError(f"数据为空: {text[:100]!r}")
        price = cls._from_parts(parts, 0)
        if price is None:
            raise SourceError(f"价格字段解析失败: {parts[:3]}")
        return Quote(
            symbol=symbol.upper(),
            name="伦敦金现货",
            price=price,
            source=cls.name,
            fetched_at=time.time(),
            prev_close=cls._from_parts(parts, 7),
            open=cls._from_parts(parts, 8),
            high=cls._from_parts(parts, 4),
            low=cls._from_parts(parts, 5),
            quote_time=parts[6].strip() if len(parts) > 6 else None,
        )


class GoldApiSource(PriceSource):
    """api.gold-api.com，免密钥、免代理的 JSON 接口。"""

    name = "gold_api"
    url_template = "https://api.gold-api.com/price/{symbol}"

    def _fetch_via(self, symbol: str, proxy: Optional[str]) -> Quote:
        url = self.url_template.format(symbol=symbol.upper())
        resp = self._get(url, proxy=proxy)
        if resp.status_code >= 400:
            raise SourceError(f"gold_api HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceError(f"gold_api 返回非 JSON: {exc}") from exc
        price = self._to_float(data.get("price"))
        if price is None:
            raise SourceError(f"gold_api 未返回价格: {str(data)[:120]}")
        return Quote(
            symbol=str(data.get("symbol", symbol)).upper(),
            name=str(data.get("name", "Gold")),
            price=price,
            source=self.name,
            fetched_at=time.time(),
            currency=str(data.get("currency", "USD")),
            unit="oz",
            quote_time=data.get("updatedAt"),
        )


SOURCE_CLASSES: dict[str, type[PriceSource]] = {
    cls.name: cls
    for cls in (TradingViewSource, SinaSource, TencentSource, GoldApiSource)
}

# 默认代理模式：TradingView 必须走代理，其余优先直连
DEFAULT_PROXY_MODE = {
    TradingViewSource.name: "always",
}


def build_sources(
    names,
    session: requests.Session,
    timeout: float = 10.0,
    proxy: Optional[str] = None,
    tradingview_symbol: Optional[str] = None,
    proxy_modes: Optional[dict] = None,
) -> list[PriceSource]:
    """按名称构造行情源实例列表。"""
    proxy_modes = proxy_modes or {}
    sources: list[PriceSource] = []
    for name in names:
        cls = SOURCE_CLASSES.get(name)
        if cls is None:
            available = ", ".join(SOURCE_CLASSES)
            raise ValueError(f"未知行情源 {name!r}，可选: {available}")
        mode = proxy_modes.get(name, DEFAULT_PROXY_MODE.get(name, "auto"))
        if cls is TradingViewSource:
            sources.append(
                cls(session, timeout, proxy=proxy, proxy_mode=mode,
                    tv_symbol=tradingview_symbol)
            )
        else:
            sources.append(cls(session, timeout, proxy=proxy, proxy_mode=mode))
    return sources