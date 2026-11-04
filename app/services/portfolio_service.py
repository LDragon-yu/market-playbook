from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any

from app.db import Database
from app.demo import next_weekday
from app.engines.advisor import AdvisorEngine
from app.engines.classification import ClassificationService
from app.engines.monitoring import MonitorEngine
from app.portfolio_db import PortfolioRepository
from app.settings import Settings
from app.notifications import Notifications
from app.sources.advisor_llm import AdvisorLLM


class PortfolioService:
    def __init__(self, settings: Settings, db: Database, pipeline: Any):
        self.settings, self.db, self.pipeline = settings, db, pipeline
        self.repository = PortfolioRepository(db)
        self.classification = ClassificationService(pipeline.tushare, self.repository)
        self.monitor = MonitorEngine()
        self.advisor_llm = AdvisorLLM(
            enabled=settings.llm_enabled, base_url=settings.llm_base_url,
            api_key=settings.llm_api_key, model=settings.llm_model,
        )
        self.advisor = AdvisorEngine(self.advisor_llm)
        self._limit_cache: dict[str, dict[str, dict[str, Any]]] = {}
        self.notifications = Notifications(settings, db)

    def initialize(self) -> None:
        self.repository.initialize()

    def after_review(self, review: dict[str, Any], *, sync_profiles: bool = True) -> dict[str, Any]:
        codes = [str(row.get("code", "")) for row in review.get("stocks", []) if row.get("code")]
        warnings: list[str] = []
        if sync_profiles and self.pipeline.tushare.configured and not review.get("provenance", {}).get("is_demo"):
            existing = self.repository.list_profiles(codes)
            missing = [code for code in codes if code not in existing]
            if missing:
                warnings = self.classification.refresh_profiles(missing[:40])
        review = self.classification.enrich_review(review)
        review["warnings"] = [item for item in review.get("warnings", []) if not str(item).startswith("profile ")]
        if warnings:
            review.setdefault("warnings", []).extend(warnings)
        self.db.save_review(str(review["trade_date"]), review)
        target = self._target_date(str(review["trade_date"]))
        items = self.monitor.build_watchlist(
            trade_date=target, review=review, positions=self.repository.list_positions()
        )
        self.repository.replace_auto_monitors(target, items)
        review["monitoring"] = {"target_date": target, "symbols": len({row["ts_code"] for row in items}),
                                "relations": len(items)}
        self.db.save_review(str(review["trade_date"]), review)
        self.notifications.job("review", review)
        return review

    def after_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        review = self.db.get_review(str(plan.get("source_trade_date"))) or self.db.get_review()
        positions = self.repository.list_positions()
        profiles = self.repository.list_profiles([row["ts_code"] for row in positions])
        analyses = []
        for position in positions:
            quote = self._safe_quote(position["ts_code"])
            analyses.append(self.advisor.position_view(
                position, quote=quote, profile=profiles.get(position["ts_code"]), review=review
            ))
        plan["positions"] = analyses
        high_risk = sum(row["risk"] == "高" for row in analyses)
        if high_risk:
            plan["max_exposure_pct"] = min(int(plan.get("max_exposure_pct", 0)), 20)
            plan.setdefault("market_gate", []).insert(0, "先处理高风险持仓，未解除前不新增仓位")
        plan["position_gate"] = {
            "open_positions": len(positions), "high_risk": high_risk,
            "remaining_new_position_allowed": not high_risk and plan.get("decision") != "空仓",
        }
        self.db.save_plan(str(plan["target_date"]), str(plan["source_trade_date"]), plan)
        review = review or {}
        items = self.monitor.build_watchlist(
            trade_date=str(plan["target_date"]), review=review, positions=positions
        )
        self.repository.replace_auto_monitors(str(plan["target_date"]), items)
        self.notifications.job("plan", plan)
        return plan

    def save_position(self, value: dict[str, Any]) -> dict[str, Any]:
        code = str(value.get("ts_code", "")).upper().strip()
        if not code or "." not in code:
            raise ValueError("股票代码必须包含交易所后缀，例如 000592.SZ。")
        total_qty = float(value.get("total_qty", 0) or 0)
        sellable_qty = float(value.get("sellable_qty", 0) or 0)
        if sellable_qty > total_qty:
            raise ValueError("可卖数量不能大于总持仓数量。")
        buy_date = str(value.get("buy_date", "")).replace("-", "")
        today = date.today().strftime("%Y%m%d")
        if buy_date and buy_date > today:
            raise ValueError("买入日期不能晚于今天。")
        if buy_date == today:
            sellable_qty = 0
        elif buy_date and buy_date < today and sellable_qty == 0 and not value.get("preserve_sellable_qty"):
            sellable_qty = total_qty
        profile = self.classification.ensure_profile(code)
        value = {
            **value, "ts_code": code, "name": value.get("name") or profile.get("name") or code,
            "total_qty": total_qty, "sellable_qty": sellable_qty,
        }
        saved = self.repository.save_position(value)
        self._rebuild_watchlist()
        return saved

    def close_position(self, position_id: int) -> None:
        self.repository.close_position(position_id)
        self._rebuild_watchlist()

    def _rebuild_watchlist(self) -> None:
        review = self.db.get_review()
        if not review:
            return
        plan = self.db.get_plan()
        target = str((plan or {}).get("target_date") or self._target_date(str(review["trade_date"])))
        items = self.monitor.build_watchlist(
            trade_date=target, review=review, positions=self.repository.list_positions()
        )
        self.repository.replace_auto_monitors(target, items)

    def add_manual_monitor(self, value: dict[str, Any]) -> dict[str, Any]:
        profile = self.classification.ensure_profile(value["ts_code"])
        plan = self.db.get_plan()
        item = {**value, "trade_date": value.get("trade_date") or (plan or {}).get("target_date")
                or date.today().strftime("%Y%m%d"), "name": value.get("name") or profile["name"]}
        item_id = self.repository.add_monitor(item)
        return {"id": item_id, **item}

    def run_monitor_cycle(self, trade_date: str | None = None, *, max_symbols: int = 18) -> dict[str, Any]:
        started = time.perf_counter()
        plan = self.db.get_plan()
        day = trade_date or (plan or {}).get("target_date") or date.today().strftime("%Y%m%d")
        monitors = self.repository.list_monitors(str(day))
        by_code: dict[str, dict[str, Any]] = {}
        for item in monitors:
            by_code.setdefault(item["ts_code"], item)
        selected = sorted(by_code.values(), key=lambda row: row["priority"], reverse=True)[:max_symbols]
        positions = {row["ts_code"]: row for row in self.repository.list_positions()}
        features_by_code: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        new_alerts: list[dict[str, Any]] = []
        for item in selected:
            code = item["ts_code"]
            try:
                quote = self._quote(code)
                self.repository.save_snapshot(code, quote)
                history = self.repository.recent_snapshots(code, 20)
                features = self.monitor.features(history, self._limit(code, str(day)))
                quote_date = str(features.get("quote_time", ""))[:10].replace("-", "")
                features["stale"] = bool(quote_date and quote_date != str(day))
                features_by_code[code] = features
                if features["stale"]:
                    continue
                for alert in self.monitor.alerts(features, position=positions.get(code)):
                    if self.repository.recent_alert_exists(code, str(alert.get("event_type", "event"))):
                        continue
                    alert["id"] = self.repository.add_alert(alert)
                    new_alerts.append(alert)
            except Exception as exc:
                errors.append(f"{code}: {exc}")

        comparisons: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for item in monitors:
            peer = item.get("peer_code")
            if not peer or item["ts_code"] not in features_by_code or peer not in features_by_code or features_by_code[item["ts_code"]].get("stale") or features_by_code[peer].get("stale"):
                continue
            key = tuple(sorted((item["ts_code"], peer)))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            value = self.monitor.compare_pair(features_by_code[item["ts_code"]], features_by_code[peer])
            value.update({"left_code": item["ts_code"], "right_code": peer, "role": item["role"]})
            comparisons.append(value)

        decisions = []
        for alert in new_alerts:
            code = alert["ts_code"]
            pair = next((row for row in comparisons if code in {row["left_code"], row["right_code"]}), None)
            context = self._decision_context(code, features_by_code.get(code, {}), positions.get(code), pair, alert)
            decision = self.advisor.realtime_decision(context)
            decision["id"] = self.repository.add_decision(decision)
            decisions.append(decision)
            self.notifications.alert(alert, decision)
        return {
            "trade_date": str(day), "captured_at": datetime.now().isoformat(timespec="seconds"),
            "symbols": len(selected), "features": list(features_by_code.values()),
            "comparisons": comparisons, "new_alerts": new_alerts, "decisions": decisions,
            "errors": errors, "elapsed_seconds": round(time.perf_counter() - started, 2),
        }

    def ask_stock(self, ts_code: str, question: str) -> dict[str, Any]:
        code = ts_code.upper().strip()
        profile = self.classification.ensure_profile(code)
        quote = self._quote(code)
        self.repository.save_snapshot(code, quote)
        features = self.monitor.features(self.repository.recent_snapshots(code, 20), self._limit(code, date.today().strftime("%Y%m%d")))
        quote_date = str(features.get("quote_time", ""))[:10].replace("-", "")
        features["stale"] = bool(quote_date and quote_date != date.today().strftime("%Y%m%d"))
        position = next((row for row in self.repository.list_positions() if row["ts_code"] == code), None)
        review, plan = self.db.get_review(), self.db.get_plan()
        start = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
        daily = self.pipeline.tushare.query(
            "daily", {"ts_code": code, "start_date": start, "end_date": date.today().strftime("%Y%m%d")},
            "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
        )
        peers = self.repository.list_monitors((plan or {}).get("target_date"))
        peer_codes = list(dict.fromkeys(row.get("peer_code") for row in peers if row["ts_code"] == code and row.get("peer_code")))
        peer_features = []
        for peer in peer_codes[:2]:
            history = self.repository.recent_snapshots(peer, 5)
            if history:
                peer_features.append(self.monitor.features(history, self._limit(peer, date.today().strftime("%Y%m%d"))))
        context = {
            "question": question, "ts_code": code, "profile": profile, "position": position,
            "latest_features": features, "daily_history": daily[:20], "peer_features": peer_features,
            "market": (review or {}).get("market", {}), "review_sectors": (review or {}).get("sectors", [])[:10],
            "premarket_plan": plan or {}, "recent_intelligence": self.db.list_intelligence(10),
            "hard_constraints": ["不自动下单", "遵守T+1", "行情过期时不得给操作结论", "允许空仓"],
            "data_as_of": features.get("quote_time") or datetime.now().isoformat(timespec="seconds"),
        }
        answer = self.advisor.answer_question(context)
        answer.update({"ts_code": code, "name": profile.get("name"), "question": question,
                       "data_as_of": context["data_as_of"]})
        answer["id"] = self.repository.add_question(code, question, str(answer.get("answer", "")), answer)
        return answer

    def dashboard_payload(self) -> dict[str, Any]:
        plan = self.db.get_plan()
        target = (plan or {}).get("target_date")
        return {
            "positions": self.repository.list_positions(),
            "monitors": self.repository.list_monitors(target),
            "alerts": self.repository.list_alerts(50),
            "decisions": self.repository.list_decisions(30),
            "questions": self.repository.list_questions(30),
            "llm_configured": self.advisor_llm.configured,
        }

    def _decision_context(self, code: str, features: dict[str, Any], position: dict[str, Any] | None,
                          pair: dict[str, Any] | None, alert: dict[str, Any]) -> dict[str, Any]:
        review, plan = self.db.get_review(), self.db.get_plan()
        return {
            "ts_code": code, "position": position, "latest_features": features,
            "pair_comparison": pair, "trigger_event": alert,
            "market": (review or {}).get("market", {}),
            "premarket_decision": {key: (plan or {}).get(key) for key in
                                   ("decision", "max_exposure_pct", "market_gate", "cash_plan")},
            "hard_constraints": ["不自动下单", "遵守T+1", "涨停不等于可买", "允许空仓"],
        }

    def _quote(self, code: str) -> dict[str, Any]:
        rows = self.pipeline.tushare.query(
            "rt_k", {"ts_code": code},
            "ts_code,name,pre_close,open,high,low,close,vol,amount,num,ask_price1,ask_volume1,bid_price1,bid_volume1,trade_time",
        )
        if not rows:
            raise RuntimeError("实时快照为空")
        return rows[0]

    def _safe_quote(self, code: str) -> dict[str, Any] | None:
        try:
            return self._quote(code)
        except Exception:
            return None

    def _limit(self, code: str, trade_date: str) -> dict[str, Any]:
        cache = self._limit_cache.setdefault(trade_date, {})
        if code not in cache or not cache[code]:
            rows = self.pipeline.tushare.query(
                "stk_limit", {"ts_code": code, "trade_date": trade_date},
                "trade_date,ts_code,pre_close,up_limit,down_limit",
            )
            cache[code] = rows[0] if rows else {}
        return cache[code]

    def _target_date(self, source_date: str) -> str:
        try:
            return self.pipeline.tushare.next_trade_date(source_date) if self.pipeline.tushare.configured else next_weekday(source_date)
        except Exception:
            return next_weekday(source_date)
