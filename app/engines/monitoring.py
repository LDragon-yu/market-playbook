from __future__ import annotations

import math
from datetime import datetime
from typing import Any


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def same_price(left: Any, right: Any) -> bool:
    a, b = number(left), number(right)
    return bool(a and b and math.isclose(a, b, rel_tol=.0007, abs_tol=.003))


class MonitorEngine:
    def build_watchlist(
        self, *, trade_date: str, review: dict[str, Any], positions: list[dict[str, Any]], max_items: int = 18
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for position in positions:
            items.append({
                "trade_date": trade_date, "ts_code": position["ts_code"],
                "name": position["name"], "role": "持仓", "priority": 100,
                "sector": "", "metadata": {"position_id": position["id"]},
            })

        stocks = review.get("stocks", [])
        sector_names = [row.get("name") for row in review.get("sectors", [])[:4]]
        leaders: list[dict[str, Any]] = []
        for sector in sector_names:
            rows = [row for row in stocks if row.get("sector") == sector]
            if not rows:
                continue
            rows.sort(key=lambda row: (int(row.get("board_height", 1)), number(row.get("score"))), reverse=True)
            leader = rows[0]
            leaders.append(leader)
            items.append(self._item(trade_date, leader, "情绪龙", 90))
            confirmations = sorted(rows[1:], key=lambda row: number(row.get("amount")), reverse=True)
            if confirmations:
                items.append(self._item(trade_date, confirmations[0], "板块确认", 72,
                                        peer_code=str(leader.get("code", ""))))
                items.append(self._item(trade_date, leader, "同板块二选一", 84,
                                        peer_code=str(confirmations[0].get("code", ""))))

        # Cross-sector card-position pairs: leaders competing at the same or adjacent height.
        for index, left in enumerate(leaders):
            for right in leaders[index + 1:]:
                if abs(int(left.get("board_height", 1)) - int(right.get("board_height", 1))) <= 1:
                    items.append(self._item(trade_date, left, "跨板块卡位", 86,
                                            peer_code=str(right.get("code", ""))))
                    items.append(self._item(trade_date, right, "跨板块卡位", 86,
                                            peer_code=str(left.get("code", ""))))

        if stocks:
            height = max(int(row.get("board_height", 1)) for row in stocks)
            for row in stocks:
                if int(row.get("board_height", 1)) == height:
                    items.append(self._item(trade_date, row, "市场空间板", 94))

        items.sort(key=lambda row: row["priority"], reverse=True)
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            key = (item["ts_code"], item["role"], item.get("peer_code", ""))
            if key not in seen:
                unique.append(item)
                seen.add(key)
            if len({row["ts_code"] for row in unique}) >= max_items:
                break
        return unique

    @staticmethod
    def _item(trade_date: str, stock: dict[str, Any], role: str, priority: int,
              peer_code: str = "") -> dict[str, Any]:
        return {
            "trade_date": trade_date, "ts_code": stock.get("code", ""),
            "name": stock.get("name", stock.get("code", "")), "role": role,
            "sector": stock.get("sector", ""), "priority": priority,
            "peer_code": peer_code,
            "metadata": {"board_height": stock.get("board_height", 1),
                         "stock_role": stock.get("role", ""), "score": stock.get("score", 0)},
        }

    def features(self, snapshots: list[dict[str, Any]], limits: dict[str, Any] | None = None) -> dict[str, Any]:
        if not snapshots:
            return {}
        current = snapshots[-1]["payload"] if "payload" in snapshots[-1] else snapshots[-1]
        previous = snapshots[-2]["payload"] if len(snapshots) > 1 and "payload" in snapshots[-2] else (
            snapshots[-2] if len(snapshots) > 1 else current
        )
        pre_close, close = number(current.get("pre_close")), number(current.get("close"))
        high, low, opened = number(current.get("high")), number(current.get("low")), number(current.get("open"))
        pct = (close / pre_close - 1) * 100 if pre_close else 0
        drawdown = (close / high - 1) * 100 if high else 0
        range_position = (close - low) / (high - low) if high > low else 1
        prior_close = number(previous.get("close"), close)
        momentum = (close / prior_close - 1) * 100 if prior_close else 0
        amount_delta = max(0, number(current.get("amount")) - number(previous.get("amount")))
        up_limit = number((limits or {}).get("up_limit"))
        down_limit = number((limits or {}).get("down_limit"))
        at_limit = same_price(close, up_limit)
        was_at_limit = same_price(prior_close, up_limit)
        return {
            "ts_code": current.get("ts_code"), "name": current.get("name"),
            "quote_time": current.get("trade_time", ""), "price": close,
            "pct_chg": round(pct, 2), "open_gap_pct": round((opened / pre_close - 1) * 100, 2) if pre_close else 0,
            "drawdown_from_high_pct": round(drawdown, 2), "range_position": round(range_position, 3),
            "snapshot_momentum_pct": round(momentum, 2), "amount_delta": amount_delta,
            "up_limit": up_limit, "down_limit": down_limit, "at_limit": at_limit,
            "new_limit": at_limit and not was_at_limit, "opened_limit": was_at_limit and not at_limit,
            "distance_to_up_pct": round((up_limit / close - 1) * 100, 2) if up_limit and close else None,
            "distance_to_down_pct": round((close / down_limit - 1) * 100, 2) if down_limit and close else None,
        }

    def alerts(self, features: dict[str, Any], *, position: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not features:
            return []
        code, name = str(features.get("ts_code", "")), str(features.get("name", ""))
        result: list[dict[str, Any]] = []
        if features.get("new_limit"):
            result.append(self._alert(code, "positive", "limit_up", f"{name} 触及涨停", features))
        if features.get("opened_limit"):
            result.append(self._alert(code, "warning", "limit_open", f"{name} 涨停打开", features))
        if number(features.get("drawdown_from_high_pct")) <= -4:
            result.append(self._alert(code, "warning", "high_drawdown", f"{name} 从日内高点明显回撤", features))
        if number(features.get("range_position"), 1) <= .18 and number(features.get("pct_chg")) < 0:
            result.append(self._alert(code, "danger", "near_low", f"{name} 接近日内低位", features))
        distance_down = features.get("distance_to_down_pct")
        if distance_down is not None and number(distance_down) <= 2:
            result.append(self._alert(code, "danger", "near_limit_down", f"{name} 接近跌停价", features))
        if position and number(position.get("sellable_qty")) <= 0 and any(
            row["severity"] == "danger" for row in result
        ):
            result.append(self._alert(code, "danger", "t1_locked", f"{name} 触发风险但当前无可卖数量", features))
        return result

    def compare_pair(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        spread = number(left.get("pct_chg")) - number(right.get("pct_chg"))
        if left.get("at_limit") and not right.get("at_limit"):
            state = "A领先"
        elif right.get("at_limit") and not left.get("at_limit"):
            state = "B领先"
        elif left.get("at_limit") and right.get("at_limit"):
            state = "共同晋级"
        elif number(left.get("pct_chg")) < 0 and number(right.get("pct_chg")) < 0:
            state = "共同转弱"
        elif spread >= 3:
            state = "A领先"
        elif spread <= -3:
            state = "B领先"
        else:
            state = "竞争中"
        return {"state": state, "spread_pct": round(spread, 2), "left": left, "right": right,
                "evaluated_at": datetime.now().isoformat(timespec="seconds")}

    @staticmethod
    def _alert(code: str, severity: str, event_type: str, title: str,
               features: dict[str, Any]) -> dict[str, Any]:
        return {"ts_code": code, "severity": severity, "event_type": event_type,
                "title": title, "features": features}
