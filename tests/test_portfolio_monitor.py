from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.engines.advisor import AdvisorEngine
from app.engines.monitoring import MonitorEngine
from app.portfolio_db import PortfolioRepository
from app.sources.advisor_llm import AdvisorLLM


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.db.initialize()
        self.repo = PortfolioRepository(self.db)
        self.repo.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_position_and_profile_roundtrip(self):
        self.repo.upsert_profile({"ts_code": "000592.SZ", "name": "平潭发展",
                                  "industry": "林业", "sw_l1": "农林牧渔",
                                  "concepts": ["海峡两岸", "福建自贸区"]})
        saved = self.repo.save_position({"ts_code": "000592.SZ", "name": "平潭发展",
                                         "total_qty": 1000, "sellable_qty": 0,
                                         "cost_price": 8.8, "buy_date": "2026-09-29",
                                         "strategy_mode": "trend"})
        self.assertEqual(saved["sellable_qty"], 0)
        self.assertEqual(self.repo.get_profile("000592.SZ")["concepts"][0], "海峡两岸")

    def test_snapshot_events_and_pair(self):
        engine = MonitorEngine()
        first = {"payload": {"ts_code": "600802.SH", "name": "福建水泥", "pre_close": 6.62,
                             "open": 6.66, "high": 7.28, "low": 6.62, "close": 7.10,
                             "amount": 500000000, "trade_time": "2026-09-29 09:34:00"}}
        second = {"payload": {**first["payload"], "close": 7.28, "amount": 600000000,
                              "trade_time": "2026-09-29 09:35:00"}}
        features = engine.features([first, second], {"up_limit": 7.28, "down_limit": 5.96})
        self.assertTrue(features["new_limit"])
        self.assertEqual(engine.alerts(features)[0]["event_type"], "limit_up")
        peer = {**features, "ts_code": "000592.SZ", "at_limit": False, "pct_chg": 3}
        self.assertEqual(engine.compare_pair(features, peer)["state"], "A领先")

    def test_advisor_fallback_obeys_t1(self):
        llm = AdvisorLLM(enabled=False, base_url="", api_key="", model="")
        decision = AdvisorEngine(llm).realtime_decision({
            "ts_code": "000592.SZ",
            "position": {"sellable_qty": 0},
            "latest_features": {"quote_time": "09:45:00", "pct_chg": -3,
                                "drawdown_from_high_pct": -6},
        })
        self.assertEqual(decision["action"], "停止加仓")
        self.assertEqual(decision["analysis_mode"], "rule_fallback")


    def test_stale_snapshot_never_produces_action(self):
        llm = AdvisorLLM(enabled=False, base_url="", api_key="", model="")
        decision = AdvisorEngine(llm).realtime_decision({
            "ts_code": "000592.SZ",
            "latest_features": {"quote_time": "2026-09-28 15:00:00", "stale": True},
        })
        self.assertEqual(decision["action"], "信息不足")
        self.assertEqual(decision["confidence"], 0)

    def test_alert_cooldown_lookup(self):
        alert = {"ts_code": "600802.SH", "event_type": "high_drawdown",
                 "severity": "warning", "title": "回撤", "features": {}}
        self.assertFalse(self.repo.recent_alert_exists("600802.SH", "high_drawdown"))
        self.repo.add_alert(alert)
        self.assertTrue(self.repo.recent_alert_exists("600802.SH", "high_drawdown"))

if __name__ == "__main__":
    unittest.main()
