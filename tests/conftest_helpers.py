"""测试公用工具：伪造 HTTP 会话与数据源。"""

from __future__ import annotations

from goldalert.sources import PriceSource, Quote, SourceError


class FakeResponse:
    def __init__(self, text: str = "", status: int = 200, json_data=None) -> None:
        self.text = text
        self.status_code = status
        self._json = json_data
        self.encoding = "utf-8"

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeSession:
    """固定返回同一个结果。"""

    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class SeqSession:
    """按顺序返回不同结果，用于测试"直连失败改走代理"。"""

    def __init__(self, results) -> None:
        self.results = list(results)
        self.proxies_seen: list = []

    def get(self, url, **kwargs):
        self.proxies_seen.append(kwargs.get("proxies"))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def fake_source(name: str, results) -> PriceSource:
    """构造一个按脚本返回结果的数据源。"""

    class _Fake(PriceSource):
        def __init__(self) -> None:
            super().__init__(session=None, timeout=1.0)
            self.name = name
            self._results = list(results)
            self.calls = 0

        def _fetch_via(self, symbol, proxy):
            self.calls += 1
            if not self._results:
                raise SourceError("脚本已耗尽")
            item = self._results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    return _Fake()


def quote(name: str, price: float = 4184.0) -> Quote:
    return Quote(symbol="XAU", name="Gold", price=price, source=name, fetched_at=0.0)