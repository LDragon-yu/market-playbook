from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlencode

from .http import HTTPClientError, request_json


SYMBOL_NAMES = {
    "SPY": "标普500 ETF",
    "QQQ": "纳指100 ETF",
    "DIA": "道指 ETF",
    "SOXX": "费城半导体 ETF",
    "FXI": "中国大盘股 ETF",
    "KWEB": "中概互联网 ETF",
}


class AlphaVantageSource:
    def __init__(self, api_key: str, symbols: tuple[str, ...]):
        self.api_key = api_key
        self.symbols = symbols
        self.base_url = "https://www.alphavantage.co/query"

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _quote(self, symbol: str) -> dict[str, Any]:
        url = f"{self.base_url}?{urlencode({'function': 'GLOBAL_QUOTE', 'symbol': symbol, 'apikey': self.api_key})}"
        response = request_json(url, timeout=20, retries=1)
        quote = response.get("Global Quote") or {}
        pct = str(quote.get("10. change percent", "0")).replace("%", "")
        return {
            "symbol": symbol,
            "name": SYMBOL_NAMES.get(symbol, symbol),
            "price": float(quote.get("05. price") or 0),
            "change_pct": float(pct or 0),
            "previous_close": float(quote.get("08. previous close") or 0),
            "latest_day": quote.get("07. latest trading day", ""),
            "status": "ok" if quote else "empty",
        }

    def overview(self) -> tuple[list[dict[str, Any]], list[str]]:
        if not self.api_key:
            return [], ["ALPHA_VANTAGE_API_KEY is empty"]
        results: list[dict[str, Any]] = []
        warnings: list[str] = []
        # Keep concurrency low because free keys have strict limits.
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(self._quote, symbol): symbol for symbol in self.symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    results.append(future.result())
                except (HTTPClientError, ValueError) as exc:
                    warnings.append(f"Alpha Vantage {symbol}: {exc}")
        order = {symbol: index for index, symbol in enumerate(self.symbols)}
        results.sort(key=lambda row: order.get(row["symbol"], 999))
        return results, warnings

