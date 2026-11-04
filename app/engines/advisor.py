from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.sources.advisor_llm import AdvisorLLM

from .monitoring import number


class AdvisorEngine:
    def __init__(self, llm: AdvisorLLM):
        self.llm = llm

    def position_view(
        self, position: dict[str, Any], *, quote: dict[str, Any] | None,
        profile: dict[str, Any] | None, review: dict[str, Any] | None,
    ) -> dict[str, Any]:
        price = number((quote or {}).get("close"))
        cost = number(position.get("cost_price"))
        pnl = (price / cost - 1) * 100 if price and cost else None
        market = (review or {}).get("market", {})
        sellable = number(position.get("sellable_qty"))
        if market.get("cash_recommended"):
            action = "若可卖则减仓" if sellable > 0 else "停止加仓"
            risk = "高"
        elif pnl is not None and pnl <= -5:
            action = "降低预期" if sellable <= 0 else "若可卖则减仓"
            risk = "高"
        else:
            action = "持有观察"
            risk = "中"
        return {
            "position_id": position["id"], "ts_code": position["ts_code"], "name": position["name"],
            "price": price or None, "cost_price": cost, "pnl_pct": round(pnl, 2) if pnl is not None else None,
            "sellable_qty": sellable, "t1_locked": sellable <= 0,
            "strategy_mode": position.get("strategy_mode"), "risk": risk, "action": action,
            "industry": (profile or {}).get("sw_l1") or (profile or {}).get("industry") or "",
            "concepts": (profile or {}).get("concepts", [])[:10],
            "thesis": position.get("thesis", ""), "invalidation": position.get("invalidation", ""),
        }

    def realtime_decision(self, context: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now()
        llm_error = ""
        if (context.get("latest_features") or {}).get("stale"):
            value = self._rule_decision(context)
            value["analysis_mode"] = "rule_fallback"
        elif self.llm.configured:
            try:
                value = self._sanitize_decision(self.llm.decide(context))
                value["analysis_mode"] = "llm"
            except Exception as exc:
                llm_error = str(exc)[:200]
                value = self._rule_decision(context)
                value["analysis_mode"] = "rule_fallback"
        else:
            value = self._rule_decision(context)
            value["analysis_mode"] = "rule_fallback"
        if llm_error:
            value.setdefault("risks", []).append(f"大模型调用失败，已退回硬规则：{llm_error}")
        seconds = max(30, min(300, int(value.get("expires_seconds", 90))))
        value.update({
            "generated_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + timedelta(seconds=seconds)).isoformat(timespec="seconds"),
            "ts_code": context.get("ts_code", ""),
        })
        return value

    def answer_question(self, context: dict[str, Any]) -> dict[str, Any]:
        llm_error = ""
        if self.llm.configured and not (context.get("latest_features") or {}).get("stale"):
            try:
                value = self.llm.answer(context)
                value["analysis_mode"] = "llm"
                return value
            except Exception as exc:
                llm_error = str(exc)[:200]
        features = context.get("latest_features") or {}
        profile = context.get("profile") or {}
        position = context.get("position")
        pct = features.get("pct_chg")
        facts = [
            f"最新价 {features.get('price', '—')}，涨跌幅 {pct if pct is not None else '—'}%",
            f"从日内高点回撤 {features.get('drawdown_from_high_pct', '—')}%",
            f"申万行业：{profile.get('sw_l1') or profile.get('industry') or '待确认'}",
        ]
        stale_note = "行情快照不是今天的数据，只能解释历史事实，不能据此给出当前操作。" if features.get("stale") else ""
        answer = ("当前未能使用大模型，以下仅为结构化事实，不生成语义推断。" + stale_note
                  + "；".join(facts))
        risks = ["大模型未配置或暂时不可用"]
        if llm_error:
            risks.append(f"模型调用失败：{llm_error}")
        if features.get("stale"):
            risks.append("行情快照已过期")
        return {
            "answer": answer, "conclusion": "配置可用的大模型后可结合题材、计划和情报回答。",
            "facts": facts, "interpretation": [],
            "holding_view": "已结合持仓约束。" if position else "当前未录入该股持仓。",
            "no_position_view": "未持仓时只作为观察，不因单次快照追价。",
            "conditions": [], "invalidation": [], "risks": risks,
            "unknowns": ["缺少语义推理"], "analysis_mode": "rule_fallback",
        }

    @staticmethod
    def _sanitize_decision(value: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "持有观察", "停止加仓", "取消买入", "降低预期", "若可卖则减仓",
            "若可卖则退出", "切换观察", "保持空仓", "信息不足",
        }
        result = dict(value) if isinstance(value, dict) else {}
        if result.get("action") not in allowed:
            result["action"] = "信息不足"
        try:
            result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0))))
        except (TypeError, ValueError):
            result["confidence"] = 0.0
        for key in ("evidence", "risks", "conditions", "invalidation", "unknowns"):
            if not isinstance(result.get(key), list):
                result[key] = []
            result[key] = [str(item)[:500] for item in result[key][:12]]
        result["summary"] = str(result.get("summary", "信息不足"))[:1000]
        return result
    @staticmethod
    def _rule_decision(context: dict[str, Any]) -> dict[str, Any]:
        features = context.get("latest_features") or {}
        position = context.get("position")
        peer = context.get("pair_comparison") or {}
        if features.get("stale"):
            return {
                "summary": "行情快照已过期，不能生成当前操作结论。", "action": "信息不足",
                "confidence": 0.0, "evidence": [f"行情时间：{features.get('quote_time', '未知')}"],
                "risks": ["过期行情可能导致误判"], "conditions": ["等待当日有效快照"],
                "invalidation": [], "expires_seconds": 30, "unknowns": ["当前实时状态未知"],
            }
        action, summary, confidence = "持有观察", "尚未触发强制风险事件。", .55
        risks: list[str] = []
        if features.get("opened_limit"):
            action, summary, confidence = "停止加仓", "涨停打开，短线一致性下降。", .78
            risks.append("炸板后回封能力尚待验证")
        if number(features.get("drawdown_from_high_pct")) <= -4:
            action, summary, confidence = "若可卖则减仓", "从日内高点明显回撤，隔夜风险上升。", .76
        if position and number(position.get("sellable_qty")) <= 0 and action.startswith("若可卖"):
            action, summary = "停止加仓", summary + " 当前受T+1限制，无可卖数量。"
        if peer.get("state") in {"A领先", "B领先"}:
            risks.append(f"卡位状态：{peer['state']}，强弱差 {peer.get('spread_pct')}%")
        return {
            "summary": summary, "action": action, "confidence": confidence,
            "evidence": [f"行情时间：{features.get('quote_time', '未知')}",
                         f"涨跌幅：{features.get('pct_chg', '—')}%",
                         f"日内高点回撤：{features.get('drawdown_from_high_pct', '—')}%"],
            "risks": risks, "conditions": ["后续快照需继续确认"],
            "invalidation": ["行情时间过期", "市场门槛发生反向变化"],
            "expires_seconds": 90, "unknowns": ["未配置大模型，仅执行硬规则"],
        }
