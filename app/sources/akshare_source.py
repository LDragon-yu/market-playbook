from __future__ import annotations

from datetime import datetime
from typing import Any


class AkShareSource:
    """Optional free supplement. It is never required for core reports."""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def sector_minutes(
        self,
        name: str,
        *,
        trade_date: str,
        kind: str = "concept",
        period: str = "5",
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("AKShare is enabled but not installed") from exc

        parsed = datetime.strptime(trade_date, "%Y%m%d")
        start = parsed.strftime("%Y-%m-%d 09:30:00")
        end = parsed.strftime("%Y-%m-%d 15:00:00")
        if kind == "industry":
            frame = ak.stock_board_industry_hist_min_em(
                symbol=name, period=period, start_date=start, end_date=end
            )
        else:
            frame = ak.stock_board_concept_hist_min_em(
                symbol=name, period=period, start_date=start, end_date=end
            )
        rows = frame.to_dict(orient="records")
        normalized: list[dict[str, Any]] = []
        for row in rows:
            normalized.append(
                {
                    "time": str(row.get("时间") or row.get("日期") or ""),
                    "open": float(row.get("开盘") or 0),
                    "high": float(row.get("最高") or 0),
                    "low": float(row.get("最低") or 0),
                    "close": float(row.get("收盘") or 0),
                    "amount": float(row.get("成交额") or 0),
                    "volume": float(row.get("成交量") or 0),
                }
            )
        return normalized

