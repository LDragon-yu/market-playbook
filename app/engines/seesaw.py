from __future__ import annotations

import math
from typing import Any


def _corr(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 3:
        return 0.0
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=False))
    left_var = sum((x - left_mean) ** 2 for x in left)
    right_var = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_var * right_var)
    return numerator / denominator if denominator else 0.0


class SeesawEngine:
    def analyze_pair(
        self,
        left_name: str,
        right_name: str,
        left_rows: list[dict[str, Any]],
        right_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        left_map = {str(row.get("date") or row.get("trade_date")): row for row in left_rows}
        right_map = {str(row.get("date") or row.get("trade_date")): row for row in right_rows}
        dates = sorted(set(left_map) & set(right_map))[-60:]
        left_returns = [float(left_map[day].get("return", left_map[day].get("pct_chg", 0))) for day in dates]
        right_returns = [float(right_map[day].get("return", right_map[day].get("pct_chg", 0))) for day in dates]
        opposite = sum((a > 0 > b) or (b > 0 > a) for a, b in zip(left_returns, right_returns, strict=False))
        correlation = _corr(left_returns, right_returns)
        opposite_rate = opposite / max(1, len(dates))
        score = max(0.0, min(100.0, (-correlation * 55) + opposite_rate * 60))
        confidence = "高" if len(dates) >= 40 and score >= 60 else "中" if len(dates) >= 20 and score >= 40 else "低"
        return {
            "left": left_name,
            "right": right_name,
            "sample_days": len(dates),
            "correlation": round(correlation, 3),
            "opposite_rate": round(opposite_rate, 3),
            "score": round(score, 1),
            "confidence": confidence,
            "note": "价格反向只说明共振关系；需结合成交额占比和5分钟先后关系确认资金迁移",
        }

