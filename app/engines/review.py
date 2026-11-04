from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "None"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _same_price(left: Any, right: Any) -> bool:
    a, b = _number(left), _number(right)
    return bool(a and b and math.isclose(a, b, rel_tol=0.0006, abs_tol=0.002))


def _board_height(row: dict[str, Any], step_map: dict[str, int]) -> int:
    code = str(row.get("ts_code", ""))
    if code in step_map:
        return step_map[code]
    text = " ".join(str(row.get(key, "")) for key in ("tag", "status"))
    matches = re.findall(r"(\d+)\s*(?:天)?\s*(\d+)?\s*板", text)
    heights = [int(second or first) for first, second in matches]
    return max(heights, default=1)


def _time_score(value: Any) -> float:
    digits = re.sub(r"\D", "", str(value or ""))[-6:]
    if len(digits) != 6:
        return 0.0
    hour, minute = int(digits[:2]), int(digits[2:4])
    minutes = (hour - 9) * 60 + minute - 30
    return _clamp(20 - max(0, minutes) / 12, 0, 20)


class ReviewEngine:
    def build(
        self,
        trade_date: str,
        bundle: dict[str, list[dict[str, Any]]],
        *,
        warnings: list[str] | None = None,
        seesaw: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        daily = bundle.get("daily", [])
        basics = {str(row.get("ts_code")): row for row in bundle.get("daily_basic", [])}
        limits = {str(row.get("ts_code")): row for row in bundle.get("stk_limit", [])}
        limit_rows = bundle.get("limit_list_ths", [])
        step_map = {
            str(row.get("ts_code")): int(_number(row.get("nums"), 1))
            for row in bundle.get("limit_step", [])
        }

        derived_limit_up: list[dict[str, Any]] = []
        derived_limit_down: list[dict[str, Any]] = []
        derived_broken: list[dict[str, Any]] = []
        one_word_codes: set[str] = set()
        limit_widths: dict[str, float] = {}
        for row in daily:
            code = str(row.get("ts_code", ""))
            limit_row = limits.get(code, {})
            close, high, low = row.get("close"), row.get("high"), row.get("low")
            pre_close, up_limit = _number(limit_row.get("pre_close")), _number(limit_row.get("up_limit"))
            if pre_close and up_limit:
                # 10cm / 20cm / ST 5cm / 北交所 30cm boards all share one score scale.
                limit_widths[code] = up_limit / pre_close - 1
            if _same_price(close, limit_row.get("up_limit")):
                derived_limit_up.append(row)
            elif _same_price(high, limit_row.get("up_limit")):
                derived_broken.append(row)
            if _same_price(close, limit_row.get("down_limit")):
                derived_limit_down.append(row)
            row["is_one_word"] = _same_price(high, low) and _same_price(close, limit_row.get("up_limit"))
            if row["is_one_word"]:
                one_word_codes.add(code)

        typed_limits: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in limit_rows:
            typed_limits[str(row.get("limit_type") or "涨停池")].append(row)
        official_ups = typed_limits.get("涨停池", []) + typed_limits.get("连扳池", [])
        # Some providers duplicate records between pools.
        official_up_codes = {str(row.get("ts_code")) for row in official_ups}
        limit_up_count = len(official_up_codes) if official_up_codes else len(derived_limit_up)
        broken_count = len(typed_limits.get("炸板池", [])) or len(derived_broken)
        limit_down_count = len(typed_limits.get("跌停池", [])) or len(derived_limit_down)

        advances = sum(_number(row.get("pct_chg")) > 0 for row in daily)
        declines = sum(_number(row.get("pct_chg")) < 0 for row in daily)
        flats = max(0, len(daily) - advances - declines)
        breadth = advances / max(1, advances + declines)

        max_height = max(step_map.values(), default=1 if limit_up_count else 0)
        index_rows = bundle.get("indices", [])
        index_avg = sum(_number(row.get("pct_chg")) for row in index_rows) / max(1, len(index_rows))
        breadth_score = breadth * 100
        limit_score = _clamp(48 + (limit_up_count - limit_down_count) * 0.9 - broken_count * 0.35)
        height_score = _clamp(max_height / 6 * 100)
        index_score = _clamp(50 + index_avg * 13)
        emotion_score = round(
            breadth_score * 0.35 + limit_score * 0.3 + height_score * 0.2 + index_score * 0.15,
            1,
        )
        break_rate = broken_count / max(1, broken_count + limit_up_count)
        regime, regime_note = self._regime(emotion_score, breadth, break_rate, limit_down_count, limit_up_count)
        cash_recommended = regime in {"冰点", "退潮"} or (
            limit_down_count >= max(5, int(limit_up_count * 0.75)) and breadth < 0.38
        )
        exposure = 0 if cash_recommended else 20 if regime == "分歧" else 40 if regime == "修复" else 60

        sectors = self._sectors(bundle)
        stocks = self._stocks(
            limit_rows, derived_limit_up, basics, step_map, sectors,
            limit_widths=limit_widths, one_word_codes=one_word_codes,
        )
        ladders: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for stock in stocks:
            ladders[f"{stock['board_height']}板"].append(stock)
        ladder_rows = [
            {"level": level, "height": int(level[:-1]), "stocks": rows}
            for level, rows in ladders.items()
        ]
        ladder_rows.sort(key=lambda row: row["height"], reverse=True)

        candidates = [stock for stock in stocks if stock["candidate_eligible"]][:8]
        risk_flags: list[str] = []
        if break_rate >= 0.4:
            risk_flags.append(f"炸板率{break_rate:.0%}，追高容错率偏低")
        if breadth < 0.4:
            risk_flags.append("下跌家数显著多于上涨家数")
        if limit_down_count >= limit_up_count and limit_down_count:
            risk_flags.append("跌停家数不低于涨停家数")
        if cash_recommended:
            risk_flags.insert(0, "市场门槛未通过，次日优先空仓或仅观察")

        return {
            "trade_date": trade_date,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "market": {
                "emotion_score": emotion_score,
                "regime": regime,
                "regime_note": regime_note,
                "advances": advances,
                "declines": declines,
                "flats": flats,
                "limit_up": limit_up_count,
                "limit_down": limit_down_count,
                "broken": broken_count,
                "break_rate": round(break_rate, 4),
                "max_board_height": max_height,
                "cash_recommended": cash_recommended,
                "max_exposure_pct": exposure,
                "risk_flags": risk_flags,
            },
            "indices": [
                {
                    "code": row.get("ts_code"),
                    "name": row.get("name", row.get("ts_code")),
                    "close": _number(row.get("close")),
                    "pct_chg": _number(row.get("pct_chg")),
                    "amount": _number(row.get("amount")),
                }
                for row in index_rows
            ],
            "sectors": sectors,
            "stocks": stocks,
            "ladders": ladder_rows,
            "candidates": candidates,
            "seesaw": seesaw or [],
            "warnings": warnings or [],
            "methodology": {
                "market": "广度、涨跌停、炸板率、连板高度与指数表现的确定性加权",
                "sector": "涨停扩散、连板高度、涨幅、持续天数与资金净额",
                "stock": "板块地位、连板高度、首次封板、换手与成交额；涨停幅度按板别归一化；ST 与一字板仅观察",
            },
        }

    @staticmethod
    def _regime(
        score: float,
        breadth: float,
        break_rate: float,
        limit_down: int,
        limit_up: int,
    ) -> tuple[str, str]:
        if score < 32 or (breadth < 0.32 and limit_down >= limit_up):
            return "冰点", "亏钱效应占优，不主动寻找交易机会"
        if score < 43:
            return "退潮", "高位和后排承压，等待风险释放"
        if score < 55:
            return "分歧", "强弱分化，只保留核心观察"
        if score < 66:
            return "修复", "风险偏好回升，但需验证持续性"
        if score < 80 and break_rate < 0.4:
            return "发酵", "主线扩散，关注前排与低位补涨"
        return "高潮", "一致性较高，次日重点防范分歧"

    def _sectors(self, bundle: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        strongest = bundle.get("limit_cpt_list", [])
        money = {str(row.get("name")): row for row in bundle.get("moneyflow_cnt_ths", [])}
        rows: list[dict[str, Any]] = []
        if strongest:
            for index, row in enumerate(strongest):
                name = str(row.get("name") or row.get("ts_code") or "未命名")
                pct = _number(row.get("pct_chg"))
                up_count = int(_number(row.get("up_nums")))
                high_count = int(_number(row.get("cons_nums")))
                days = int(_number(row.get("days")))
                flow = money.get(name, {})
                score = _clamp(42 + pct * 4 + up_count * 3 + high_count * 4 + min(days, 5) * 2 - index)
                rows.append(self._sector_row(name, score, pct, up_count, high_count, days, flow, row))
        else:
            for row in bundle.get("moneyflow_cnt_ths", []):
                name = str(row.get("name") or row.get("ts_code") or "未命名")
                pct = _number(row.get("pct_change"))
                net = _number(row.get("net_amount"))
                score = _clamp(50 + pct * 7 + max(-12, min(12, net / 5)))
                rows.append(self._sector_row(name, score, pct, 0, 0, 1, row, row))
        rows.sort(key=lambda row: row["score"], reverse=True)
        for index, row in enumerate(rows):
            row["rank"] = index + 1
            if index < 3 and row["score"] >= 72:
                row["tier"] = "主线"
            elif row["score"] >= 62:
                row["tier"] = "强势"
            elif row["score"] >= 50:
                row["tier"] = "轮动"
            else:
                row["tier"] = "观察"
        return rows[:30]

    @staticmethod
    def _sector_row(
        name: str,
        score: float,
        pct: float,
        up_count: int,
        high_count: int,
        days: int,
        flow: dict[str, Any],
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "name": name,
            "code": raw.get("ts_code", ""),
            "score": round(score, 1),
            "pct_chg": round(pct, 2),
            "limit_up_count": up_count,
            "high_board_count": high_count,
            "active_days": days,
            "max_height": str(raw.get("up_stat") or "—"),
            "lead_stock": flow.get("lead_stock") or "—",
            "net_amount": _number(flow.get("net_amount")),
            "tier": "观察",
            "rank": 0,
        }

    def _stocks(
        self,
        limit_rows: list[dict[str, Any]],
        derived_ups: list[dict[str, Any]],
        basics: dict[str, dict[str, Any]],
        step_map: dict[str, int],
        sectors: list[dict[str, Any]],
        *,
        limit_widths: dict[str, float] | None = None,
        one_word_codes: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        limit_widths = limit_widths or {}
        one_word_codes = one_word_codes or set()
        source = limit_rows or derived_ups
        sector_names = [str(row["name"]) for row in sectors]
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in source:
            code = str(row.get("ts_code", ""))
            if not code or code in seen:
                continue
            seen.add(code)
            basic = basics.get(code, {})
            name = str(row.get("name") or basic.get("name") or code)
            description = str(row.get("lu_desc") or "")
            sector = next((item for item in sector_names if item and item in description), "未归类")
            height = _board_height(row, step_map)
            pct = _number(row.get("pct_chg"), _number(basic.get("pct_chg")))
            turnover = _number(row.get("turnover_rate"), _number(basic.get("turnover_rate")))
            amount = _number(row.get("turnover"), _number(basic.get("amount")))
            open_count = int(_number(row.get("open_num")))
            first_time = str(row.get("first_lu_time") or "")
            width = self._board_width(code, name, limit_widths)
            # Normalize to a 10cm scale so 20cm/30cm boards do not outscore
            # ordinary main-board stocks just because the limit is wider.
            pct_norm = pct * 0.10 / width if width > 0 else pct
            is_one_word = code in one_word_codes
            score = _clamp(
                36 + height * 10 + min(15, pct_norm) * 1.2 + _time_score(first_time) - open_count * 3
                + min(turnover, 30) * 0.25
            )
            if height >= 4:
                role = "高位核心"
            elif height >= 2:
                role = "连板前排"
            elif amount >= 1_000_000_000:
                role = "趋势中军"
            else:
                role = "首板/补涨"
            risk = "高" if height >= 5 or open_count >= 3 else "中" if height >= 3 or open_count else "低"
            blocked: list[str] = []
            if "ST" in name.upper():
                blocked.append("ST")
            if is_one_word:
                blocked.append("一字板")
            buyable = not blocked
            eligible = buyable and score >= 55 and risk != "高"
            result.append(
                {
                    "code": code,
                    "name": name,
                    "sector": sector,
                    "board_height": height,
                    "board_type": self._board_type(width),
                    "tag": str(row.get("tag") or "涨停"),
                    "status": str(row.get("status") or "封板"),
                    "role": role,
                    "score": round(score, 1),
                    "risk": risk,
                    "pct_chg": round(pct, 2),
                    "turnover_rate": round(turnover, 2),
                    "amount": amount,
                    "open_count": open_count,
                    "first_limit_time": first_time or "—",
                    "is_one_word": is_one_word,
                    "buyable": buyable,
                    "not_buyable_reason": "、".join(blocked),
                    "reason": description or "根据收盘涨停与量价结构识别",
                    "candidate_eligible": eligible,
                }
            )
        result.sort(key=lambda row: (row["score"], row["board_height"]), reverse=True)
        return result

    @staticmethod
    def _board_width(code: str, name: str, widths: dict[str, float]) -> float:
        width = widths.get(code)
        if width and width > 0:
            return width
        if "ST" in name.upper():
            return 0.05
        if code.startswith(("300", "301", "688", "689")):
            return 0.20
        if code.startswith(("4", "8", "92")):
            return 0.30
        return 0.10

    @staticmethod
    def _board_type(width: float) -> str:
        if width <= 0.06:
            return "ST"
        if width >= 0.25:
            return "30cm"
        if width >= 0.15:
            return "20cm"
        return "10cm"

