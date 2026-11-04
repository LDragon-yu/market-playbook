from __future__ import annotations

from datetime import datetime
from typing import Any


class PlanEngine:
    def build(
        self,
        *,
        target_date: str,
        review: dict[str, Any],
        overseas: list[dict[str, Any]],
        intelligence: list[dict[str, Any]],
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        market = review.get("market", {})
        valid_overseas = [row for row in overseas if row.get("status") == "ok"]
        overseas_avg = sum(float(row.get("change_pct", 0)) for row in valid_overseas) / max(1, len(valid_overseas))
        negative_items = sum(
            1
            for item in intelligence
            if (item.get("metadata") or {}).get("stance") == "negative"
        )
        positive_items = sum(
            1
            for item in intelligence
            if (item.get("metadata") or {}).get("stance") == "positive"
        )
        risk_adjustment = (-12 if overseas_avg <= -1.5 else -6 if overseas_avg <= -0.7 else 5 if overseas_avg >= 1 else 0)
        risk_adjustment += max(-10, min(10, (positive_items - negative_items) * 2))
        base_score = float(market.get("emotion_score", 50))
        gate_score = max(0.0, min(100.0, base_score + risk_adjustment))
        forced_cash = bool(market.get("cash_recommended")) or gate_score < 38
        decision = "空仓" if forced_cash else "轻仓观察" if gate_score < 55 else "条件参与"
        max_exposure = 0 if forced_cash else min(int(market.get("max_exposure_pct", 40)), 20 if gate_score < 55 else 50)

        candidates: list[dict[str, Any]] = []
        if not forced_cash:
            for stock in review.get("candidates", [])[:5]:
                candidates.append(self._candidate(stock, review))

        market_gate = [
            "开盘后主线板块上涨家数不能快速坍塌",
            "昨日核心股不能集体低开后继续走弱",
            "指数与情绪至少有一项确认修复；若同时走弱则取消全部计划",
        ]
        if overseas_avg <= -0.7:
            market_gate.insert(0, "外围市场偏弱，所有触发条件提高一级，禁止仅凭高开参与")
        if forced_cash:
            market_gate.insert(0, "当前默认空仓；只有盘前风险解除且主线超预期时才重新评估")

        return {
            "target_date": target_date,
            "source_trade_date": review.get("trade_date"),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": "frozen",
            "decision": decision,
            "gate_score": round(gate_score, 1),
            "max_exposure_pct": max_exposure,
            "cash_plan": {
                "allowed": True,
                "recommended": forced_cash,
                "reason": "；".join(market.get("risk_flags") or []) or "若市场门槛未通过，空仓是完整策略",
            },
            "market_gate": market_gate,
            "overseas": overseas,
            "overseas_summary": self._overseas_summary(valid_overseas, overseas_avg),
            "intelligence_summary": {
                "positive": positive_items,
                "negative": negative_items,
                "items": intelligence[:12],
            },
            "candidates": candidates,
            "warnings": warnings or [],
            "discipline": [
                "没有触发条件就没有交易；不得把观察条件解释成盘中追涨许可",
                "任何失效条件出现后，当日不因价格反抽重新启用原预案",
                "计划生成后冻结，收盘只做验证，不做事后改写",
            ],
        }

    @staticmethod
    def _overseas_summary(rows: list[dict[str, Any]], average: float) -> str:
        if not rows:
            return "外围行情接口未配置，盘前需要人工核对美股与中概股表现"
        direction = "偏强" if average >= 0.7 else "偏弱" if average <= -0.7 else "中性"
        return f"外围整体{direction}，观察组合平均涨跌幅{average:+.2f}%"

    @staticmethod
    def _candidate(stock: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
        height = int(stock.get("board_height", 1))
        high_low_band = "2%～5%" if height <= 2 else "1%～4%"
        return {
            **stock,
            "priority": "A" if float(stock.get("score", 0)) >= 75 else "B",
            "premise": [
                f"所属{stock.get('sector', '板块')}保持前排，核心股不出现快速负反馈",
                "竞价和开盘表现必须与板块同步，拒绝孤立高开",
            ],
            "scenarios": [
                {
                    "name": "合理高开",
                    "condition": f"高开约{high_low_band}，板块同步增强且开盘承接正常",
                    "action": "等待确认，不在开盘脉冲中追价；确认板块扩散后才保留观察资格",
                },
                {
                    "name": "超预期高开",
                    "condition": "高开过大或接近一字，同时板块后排没有同步",
                    "action": "按一致性兑现风险处理，不主动追涨",
                },
                {
                    "name": "弱转强",
                    "condition": "小幅低开后收复昨收/分时均价，成交放大且板块同步修复",
                    "action": "升级为重点观察；必须同时满足价格、量能和板块三项确认",
                },
                {
                    "name": "分歧",
                    "condition": "后排回落但板块核心有承接，个股仍强于板块平均",
                    "action": "只保留核心，取消跟风和后排候选",
                },
                {
                    "name": "失效",
                    "condition": "低开后持续弱于板块，或板块核心与中军同步走弱",
                    "action": "取消计划；当天不因单次反抽重新启用",
                },
            ],
            "invalidation": [
                "板块从主线/强势梯队跌出",
                "个股相对板块强度持续转负",
                "市场触发空仓门槛",
            ],
        }

