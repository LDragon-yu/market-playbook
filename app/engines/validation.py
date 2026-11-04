from __future__ import annotations

from datetime import datetime
from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


class ValidationEngine:
    """Evaluate a frozen plan against the day it targeted.

    The goal is a feedback loop, not a scoreboard: candidate outcomes feed
    weight calibration, and the cash/no-trade call is checked against the next
    day's actual market breadth.
    """

    def evaluate(
        self,
        plan: dict[str, Any],
        next_daily: list[dict[str, Any]],
    ) -> dict[str, Any]:
        by_code = {str(row.get("ts_code")): row for row in next_daily}
        outcomes: list[dict[str, Any]] = []
        for candidate in plan.get("candidates", []):
            code = str(candidate.get("code", ""))
            row = by_code.get(code)
            if not row:
                outcomes.append({"code": code, "name": candidate.get("name"), "status": "missing"})
                continue
            previous = _number(row.get("pre_close"))
            opened = _number(row.get("open"))
            closed = _number(row.get("close"))
            high = _number(row.get("high"))
            low = _number(row.get("low"))
            gap = (opened / previous - 1) * 100 if previous else 0
            close_return = (closed / previous - 1) * 100 if previous else 0
            mfe = (high / opened - 1) * 100 if opened else 0
            mae = (low / opened - 1) * 100 if opened else 0
            scenario = "高开" if gap >= 2 else "低开" if gap <= -1 else "平开"
            outcomes.append(
                {
                    "code": code,
                    "name": candidate.get("name"),
                    "status": "evaluated",
                    "scenario": scenario,
                    "gap_pct": round(gap, 2),
                    "close_pct": round(close_return, 2),
                    "mfe_from_open": round(mfe, 2),
                    "mae_from_open": round(mae, 2),
                    "limit_up_close": _number(row.get("pct_chg")) >= 9.8,
                    "note": "仅用日线验证开收盘与区间；弱转强顺序需结合候选股分钟线",
                }
            )

        evaluated = [row for row in outcomes if row.get("status") == "evaluated"]
        total_market = len(next_daily)
        market_pcts = [_number(row.get("pct_chg")) for row in next_daily]
        advances = sum(pct > 0 for pct in market_pcts)
        declines = sum(pct < 0 for pct in market_pcts)
        market_avg = sum(market_pcts) / total_market if total_market else 0.0
        breadth = advances / max(1, advances + declines)

        cash_recommended = bool(
            plan.get("cash_plan", {}).get("recommended")
            or plan.get("decision") == "空仓"
        )
        if cash_recommended and market_avg <= -0.5:
            verdict, reason = "有效", f"建议空仓次日市场平均 {market_avg:+.2f}%，规避了普跌"
        elif cash_recommended and market_avg >= 1:
            verdict, reason = "偏保守", f"建议空仓次日市场平均 {market_avg:+.2f}%，可能错过反弹"
        elif cash_recommended:
            verdict, reason = "中性", f"建议空仓次日市场平均 {market_avg:+.2f}%，涨跌互现"
        elif market_avg <= -1:
            verdict, reason = "失效", f"未建议空仓但次日市场平均 {market_avg:+.2f}%"
        else:
            verdict, reason = "中性", f"次日市场平均 {market_avg:+.2f}%，门槛判断无显著偏差"

        close_returns = [row["close_pct"] for row in evaluated]
        stats = {
            "avg_close_pct": round(sum(close_returns) / len(close_returns), 2) if close_returns else 0.0,
            "win_rate": round(sum(value > 0 for value in close_returns) / len(close_returns), 3)
            if close_returns else 0.0,
            "avg_mfe": round(
                sum(row["mfe_from_open"] for row in evaluated) / len(evaluated), 2
            ) if evaluated else 0.0,
            "avg_mae": round(
                sum(row["mae_from_open"] for row in evaluated) / len(evaluated), 2
            ) if evaluated else 0.0,
            "limit_up_close": sum(1 for row in evaluated if row["limit_up_close"]),
            "scenario_counts": {
                name: sum(1 for row in evaluated if row["scenario"] == name)
                for name in ("高开", "平开", "低开")
            },
        }
        return {
            "target_date": plan.get("target_date"),
            "evaluated_at": datetime.now().isoformat(timespec="seconds"),
            "plan_decision": plan.get("decision"),
            "candidate_count": len(plan.get("candidates", [])),
            "evaluated_count": len(evaluated),
            "positive_close_rate": stats["win_rate"],
            "stats": stats,
            "market_next": {
                "avg_pct_chg": round(market_avg, 2),
                "advances": advances,
                "declines": declines,
                "breadth": round(breadth, 3),
                "sample_size": total_market,
            },
            "cash_call": {
                "recommended": cash_recommended,
                "verdict": verdict,
                "reason": reason,
            },
            "outcomes": outcomes,
        }
