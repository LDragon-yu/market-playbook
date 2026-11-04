from __future__ import annotations

import re
from typing import Any

from app.sources.llm import LLMSource


SECTOR_WORDS = (
    "人工智能", "算力", "机器人", "半导体", "消费电子", "医药", "创新药", "低空经济",
    "军工", "新能源", "光伏", "锂电池", "固态电池", "证券", "银行", "房地产", "有色",
    "黄金", "煤炭", "农业", "消费", "游戏", "传媒", "数据中心", "商业航天",
)


class IntelligenceEngine:
    def __init__(self, llm: LLMSource):
        self.llm = llm

    def analyze(self, text: str) -> dict[str, Any]:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if self.llm.configured:
            return self._normalize(self.llm.summarize(cleaned))
        sentences = [part.strip() for part in re.split(r"[。！？!?\n]+", cleaned) if part.strip()]
        sectors = [word for word in SECTOR_WORDS if word in cleaned]
        tickers = sorted(set(re.findall(r"(?<!\d)(?:[036]\d{5}|[489]\d{5})(?!\d)", cleaned)))
        positive_words = ("利好", "超预期", "增长", "突破", "涨价", "订单", "催化", "回暖")
        negative_words = ("利空", "不及预期", "减持", "风险", "下滑", "监管", "退潮", "兑现")
        positive = sum(cleaned.count(word) for word in positive_words)
        negative = sum(cleaned.count(word) for word in negative_words)
        stance = "positive" if positive > negative else "negative" if negative > positive else "neutral"
        summary = "；".join(sentences[:4])[:500] or "未识别到有效文本"
        return {
            "summary": summary,
            "sectors": sectors,
            "tickers": tickers,
            "catalysts": [sentence for sentence in sentences if any(word in sentence for word in positive_words)][:5],
            "risks": [sentence for sentence in sentences if any(word in sentence for word in negative_words)][:5],
            "stance": stance,
            "horizon": "unknown",
            "claims": sentences[:8],
            "analysis_mode": "rule_fallback",
        }

    @staticmethod
    def _normalize(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary": str(value.get("summary", "")),
            "sectors": list(value.get("sectors") or []),
            "tickers": list(value.get("tickers") or []),
            "catalysts": list(value.get("catalysts") or []),
            "risks": list(value.get("risks") or []),
            "stance": str(value.get("stance") or "neutral"),
            "horizon": str(value.get("horizon") or "unknown"),
            "claims": list(value.get("claims") or []),
            "analysis_mode": "llm",
        }

