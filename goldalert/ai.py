"""AI 盘面解读（DeepSeek，OpenAI 兼容接口，国内直连）。

设计原则：
- 只把"我们确实采集到的数据"交给模型，不塞未经核实的信息；
- 明确要求模型不做预测、不给买卖建议；
- 任何失败都只记日志并返回 None，绝不影响播报本身。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests

from .sources import Quote
from .util import fmt_ts

SYSTEM_PROMPT = (
    "你是一名大宗商品行情助手，负责把行情数据转述成简洁客观的中文。"
    "只依据给定的数据描述，不预测未来涨跌，不给出买卖建议，"
    "不编造未提供的消息面信息。"
)


LEVELS_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + " 本次任务是以 JSON 返回你从数据里看出来的关键价位。"
    "只允许使用给定数据中出现的价格水平，不预测、不给建议。"
)


ASK_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + " 本次任务是回答用户对当前盘面的提问：只依据给定的行情数据来回答，"
    "数据里没有的信息就说不知道，不预测涨跌、不给买卖建议、不编造消息面。"
)


def downsample(records: list[dict], buckets: int) -> list[dict]:
    """把序列等分成若干段，每段取最后一条，用于压缩成短提示、省 token。"""
    if buckets <= 0 or len(records) <= buckets:
        return list(records)
    step = len(records) / buckets
    picked: list[dict] = []
    for index in range(buckets):
        position = min(len(records) - 1, int((index + 1) * step) - 1)
        if position < 0:
            continue
        if not picked or picked[-1] is not records[position]:
            picked.append(records[position])
    return picked


def build_prompt(quote: Quote, records: list[dict], config) -> str:
    """把行情序列拼成给模型的提示词。"""
    rows: list[str] = []
    for record in records:
        try:
            rows.append(f"{fmt_ts(record['t'])[11:16]}  {float(record['price']):.2f}")
        except (KeyError, TypeError, ValueError):
            continue
    series = "\n".join(rows) if rows else "（暂无历史采样）"

    detail = [f"最新价 {quote.price:,.2f} 美元/盎司"]
    if quote.change is not None and quote.change_pct is not None:
        detail.append(f"较昨收 {quote.change:+,.2f} ({quote.change_pct:+.2f}%)")
    if quote.high is not None and quote.low is not None:
        detail.append(f"今日最高 {quote.high:,.2f} / 最低 {quote.low:,.2f}")
    if quote.bid is not None and quote.ask is not None:
        detail.append(f"买价 {quote.bid:,.2f} / 卖价 {quote.ask:,.2f}")
    detail.append(f"数据源 {quote.source}")

    return (
        f"以下是国际现货黄金（XAU，美元/盎司）最近约 {config.lookback_hours:g} 小时的采集数据，"
        f"按时间先后排列：\n\n"
        f"时间(北京)  价格\n{series}\n\n"
        "当前报价：\n" + "\n".join(detail) + "\n\n"
        f"请用不超过 {config.max_words} 字写一段盘面小结，说明主要运行区间、"
        "价格方向与节奏（震荡还是单边）、以及值得关注的关键价位。"
        "只描述数据反映的事实，不要预测，不要给建议。"
    )


def build_levels_prompt(quote: Quote, records: list[dict], config, max_levels: int) -> str:
    """要求模型按固定 JSON 结构给出关键价位。"""
    rows: list[str] = []
    for record in records:
        try:
            rows.append(f"{fmt_ts(record['t'])[11:16]}  {float(record['price']):.2f}")
        except (KeyError, TypeError, ValueError):
            continue
    series = "\n".join(rows) if rows else "（暂无历史采样）"

    bounds: list[str] = []
    if quote.high is not None and quote.low is not None:
        bounds.append(f"今日最高 {quote.high:,.2f} / 最低 {quote.low:,.2f}")

    return (
        f"以下是国际现货黄金（XAU，美元/盎司）最近约 {config.lookback_hours:g} 小时的采集数据：\n\n"
        f"时间(北京)  价格\n{series}\n\n"
        f"当前报价 {quote.price:,.2f} 美元/盎司。\n"
        + ("\n".join(bounds) + "\n" if bounds else "")
        + f"\n请挑出不超过 {max_levels} 个值得盯的关键价位（例如这段时间的高点、低点、"
        "以及价格反复停留的位置），只输出 json 不要任何多余文字，格式：\n"
        '{"levels": [{"price": 4200.62, "label": "近期高点"}]}\n'
        "要求：price 是数字（美元/盎司），label 是不超过 10 个汉字的中文说明；"
        "只使用上面数据里真实出现过的价位，不要编造，不要给交易建议。"
        "不要输出当前报价（最新价）本身——现价不是关键价位，没有参考价值；"
        "也不要写「当前报价」「现价」这类说明。宁可只给一两个真正值得盯的位置，"
        "也不要拿现价凑数。"
    )


# 这些说法描述的是"当前价"，不是关键价位，一律丢掉
NOT_A_LEVEL_WORDS = ("报价", "现价", "最新价", "当前价")


def is_price_label(label: str) -> bool:
    """标签是不是在描述"当前价"（现价、当前报价之类）。"""
    text = (label or "").replace(" ", "").replace("　", "")
    return any(word in text for word in NOT_A_LEVEL_WORDS)


def parse_levels(text: str, reference_price: float, max_levels: int = 4,
                 max_deviation: float = 0.05) -> list[dict]:
    """容错解析模型返回的关键价位 JSON。"""
    if not text:
        return []
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []

    if isinstance(data, dict):
        items = data.get("levels") or data.get("关键价位") or data.get("data") or []
    else:
        items = data
    if not isinstance(items, list):
        return []

    reference = float(reference_price or 0.0)
    picked: list[dict] = []
    seen: set[float] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        price = item.get("price", item.get("价位", item.get("level")))
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        if reference > 0 and abs(price - reference) / reference > max_deviation:
            continue  # 明显跑偏的价位直接丢掉
        label = str(item.get("label") or item.get("说明") or item.get("name") or "").strip()
        if is_price_label(label):
            continue  # 「当前报价」说的是现价，不是关键价位
        key = round(price, 2)
        if key in seen:
            continue
        seen.add(key)
        picked.append({"price": round(price, 2), "label": label[:20]})

    if reference > 0:
        picked.sort(key=lambda item: abs(item["price"] - reference))
    return picked[: max(1, int(max_levels))]


def _series_text(records: list[dict]) -> str:
    """把行情序列压成「时间 价格」多行文本，供提示词使用。"""
    rows: list[str] = []
    for record in records:
        try:
            rows.append(f"{fmt_ts(record['t'])[11:16]}  {float(record['price']):.2f}")
        except (KeyError, TypeError, ValueError):
            continue
    return "\n".join(rows) if rows else "（暂无历史采样）"


def _quote_detail(quote: Quote) -> list[str]:
    """当前报价的几行明细，供提示词使用。"""
    detail = [f"最新价 {quote.price:,.2f} 美元/盎司"]
    if quote.change is not None and quote.change_pct is not None:
        detail.append(f"较昨收 {quote.change:+,.2f} ({quote.change_pct:+.2f}%)")
    if quote.high is not None and quote.low is not None:
        detail.append(f"今日最高 {quote.high:,.2f} / 最低 {quote.low:,.2f}")
    if quote.bid is not None and quote.ask is not None:
        detail.append(f"买价 {quote.bid:,.2f} / 卖价 {quote.ask:,.2f}")
    detail.append(f"数据源 {quote.source}")
    return detail


def build_ask_prompt(quote: Quote, records: list[dict], config, question: str) -> str:
    """把行情数据与用户提问拼成提示词。"""
    return (
        f"以下是国际现货黄金（XAU，美元/盎司）最近约 {config.lookback_hours:g} 小时的采集数据，"
        f"按时间先后排列：\n\n"
        f"时间(北京)  价格\n{_series_text(records)}\n\n"
        "当前报价：\n" + "\n".join(_quote_detail(quote)) + "\n\n"
        f"用户提问：{question}\n\n"
        f"请用不超过 {config.max_words} 字回答，只根据上面的数据来讲；"
        "如果问题超出这些数据能回答的范围（例如涉及新闻、政策、机构观点），"
        "或者需要预测涨跌、给买卖建议，请直接说明无法依据现有数据回答，不要编造。"
    )


class MarketAnalyzer:
    """调用大模型生成盘面小结。"""

    def __init__(self, config, session: requests.Session, logger: Optional[logging.Logger] = None) -> None:
        self.config = config
        self.session = session
        self.logger = logger or logging.getLogger("goldalert.ai")
        self.api_key = (os.environ.get(config.api_key_env) or "").strip()

    @property
    def available(self) -> bool:
        return bool(self.config.enabled and self.api_key)

    def _proxies(self):
        if not self.config.proxy:
            return None
        return {"http": self.config.proxy, "https": self.config.proxy}

    def _chat(self, messages: list, json_mode: bool = False, label: str = "AI") -> Optional[str]:
        """向模型发一次对话请求；任何失败都记日志并返回 None。"""
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = self.session.post(
                url, json=payload, headers=headers,
                timeout=self.config.timeout, proxies=self._proxies(),
            )
            resp.raise_for_status()
            data = resp.json()
            text = (data["choices"][0]["message"]["content"] or "").strip()
        except requests.RequestException as exc:
            self.logger.warning("%s请求失败: %s", label, exc)
            return None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            self.logger.warning("%s返回格式异常: %s", label, exc)
            return None

        return text or None

    def analyze(self, quote: Quote, records: list[dict]) -> Optional[str]:
        """返回一段解析文本；任何失败都返回 None。"""
        if not self.available:
            return None

        trimmed = downsample(records, self.config.max_points)
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(quote, trimmed, self.config)},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = self.session.post(
                url, json=payload, headers=headers,
                timeout=self.config.timeout, proxies=self._proxies(),
            )
            resp.raise_for_status()
            data = resp.json()
            text = (data["choices"][0]["message"]["content"] or "").strip()
        except requests.RequestException as exc:
            self.logger.warning("AI 解读请求失败: %s", exc)
            return None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            self.logger.warning("AI 解读返回格式异常: %s", exc)
            return None

        return text or None

    def suggest_levels(self, quote: Quote, records: list[dict], max_levels: int = 4) -> list[dict]:
        """让模型给一组关键价位；失败返回空列表。"""
        if not self.available or not records:
            return []

        trimmed = downsample(records, self.config.max_points)
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": LEVELS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_levels_prompt(quote, trimmed, self.config, max_levels),
                },
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = self.session.post(
                url, json=payload, headers=headers,
                timeout=self.config.timeout, proxies=self._proxies(),
            )
            resp.raise_for_status()
            data = resp.json()
            text = (data["choices"][0]["message"]["content"] or "").strip()
        except requests.RequestException as exc:
            self.logger.warning("AI 关键价位请求失败: %s", exc)
            return []
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            self.logger.warning("AI 关键价位返回格式异常: %s", exc)
            return []

        levels = parse_levels(text, quote.price, max_levels)
        if not levels:
            self.logger.info("AI 关键价位解析为空: %s", text[:120])
        return levels

    def ask(self, quote: Quote, records: list[dict], question: str) -> Optional[str]:
        """回答用户关于当前盘面的提问；未启用或失败时返回 None。"""
        question = (question or "").strip()
        if not self.available or not question:
            return None

        trimmed = downsample(records, self.config.max_points)
        return self._chat(
            [
                {"role": "system", "content": ASK_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_ask_prompt(quote, trimmed, self.config, question),
                },
            ],
            label="AI 提问",
        )
