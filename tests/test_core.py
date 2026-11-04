from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.demo import demo_bundle, demo_overseas, next_weekday
from app.engines import IntelligenceEngine, PlanEngine, ReviewEngine
from app.sources.llm import LLMSource


class EngineTests(unittest.TestCase):
    def test_review_and_plan_have_required_gates(self):
        day, bundle = demo_bundle("20260925")
        review = ReviewEngine().build(day, bundle)
        self.assertGreater(review["market"]["emotion_score"], 0)
        self.assertTrue(review["sectors"])
        self.assertTrue(review["ladders"])
        plan = PlanEngine().build(target_date=next_weekday(day), review=review,
                                  overseas=demo_overseas(), intelligence=[])
        self.assertIn(plan["decision"], {"空仓", "轻仓观察", "条件参与"})
        self.assertTrue(plan["cash_plan"]["allowed"])
        self.assertTrue(plan["market_gate"])
        for candidate in plan["candidates"]:
            self.assertEqual(len(candidate["scenarios"]), 5)
            self.assertTrue(candidate["invalidation"])

    def test_rule_intelligence_fallback(self):
        llm = LLMSource(enabled=False, base_url="", api_key="", model="")
        result = IntelligenceEngine(llm).analyze("半导体订单增长形成利好，但需要警惕减持风险。")
        self.assertIn("半导体", result["sectors"])
        self.assertEqual(result["analysis_mode"], "rule_fallback")


class DatabaseTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            db.initialize()
            db.save_review("20260925", {"trade_date": "20260925", "market": {}})
            db.save_plan("20260928", "20260925", {"target_date": "20260928"})
            self.assertEqual(db.get_review()["trade_date"], "20260925")
            self.assertEqual(db.get_plan()["target_date"], "20260928")
            item_id = db.add_intelligence(source_name="测试", source_type="text", title="标题",
                                          raw_text="正文", summary="摘要", metadata={"stance": "neutral"})
            self.assertGreater(item_id, 0)
            self.assertEqual(db.list_intelligence()[0]["metadata"]["stance"], "neutral")


if __name__ == "__main__":
    unittest.main()
