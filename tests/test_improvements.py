from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db import Database
from app.demo import demo_bundle
from app.engines import ReviewEngine, ValidationEngine
from app.portfolio_db import PortfolioRepository


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    PortfolioRepository(database).initialize()
    return database


def test_connection_pragmas(db):
    with db.connect() as connection:
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_plan_freeze_and_archive(db):
    payload = {"target_date": "20260105", "generated_at": "2026-01-02T08:00:00"}
    db.save_plan("20260105", "20260102", payload)
    # Re-saving the same generated version (e.g. adding positions) is allowed.
    db.save_plan("20260105", "20260102", {**payload, "positions": []})
    assert db.get_plan("20260105")["positions"] == []
    # A new version of a past plan is refused.
    with pytest.raises(ValueError):
        db.save_plan("20260105", "20260102",
                     {**payload, "generated_at": "2026-01-03T08:00:00"})
    # Explicit force archives the previous version instead of losing it.
    db.save_plan("20260105", "20260102",
                 {**payload, "generated_at": "2026-01-03T08:00:00"}, force=True)
    with db.connect() as connection:
        archived = connection.execute("SELECT COUNT(*) FROM premarket_plan_history").fetchone()[0]
    assert archived == 1


def test_validation_reports_cash_verdict_and_stats():
    plan = {
        "target_date": "20260929",
        "decision": "空仓",
        "cash_plan": {"recommended": True},
        "candidates": [
            {"code": "600001.SH", "name": "甲"},
            {"code": "600002.SH", "name": "乙"},
        ],
    }
    next_daily = [
        {"ts_code": "600001.SH", "pre_close": 10, "open": 10.3, "close": 10.6,
         "high": 10.8, "low": 10.2, "pct_chg": 6.0},
        {"ts_code": "600002.SH", "pre_close": 10, "open": 9.8, "close": 9.7,
         "high": 9.9, "low": 9.6, "pct_chg": -3.0},
        {"ts_code": "000001.SZ", "pre_close": 10, "open": 9.9, "close": 9.5,
         "high": 10, "low": 9.4, "pct_chg": -5.0},
    ]
    report = ValidationEngine().evaluate(plan, next_daily)
    assert report["cash_call"]["verdict"] == "有效"
    assert report["stats"]["win_rate"] == 0.5
    assert report["stats"]["scenario_counts"]["高开"] == 1
    assert report["market_next"]["declines"] == 2
    assert report["evaluated_count"] == 2


def test_one_word_board_is_not_buyable():
    day, bundle = demo_bundle("20260925")
    code = "600001.SH"
    for row in bundle["daily"]:
        if row["ts_code"] == code:
            row["high"] = row["low"] = row["close"]
    review = ReviewEngine().build(day, bundle)
    stock = next(row for row in review["stocks"] if row["code"] == code)
    assert stock["is_one_word"] is True
    assert stock["buyable"] is False
    assert "一字板" in stock["not_buyable_reason"]
    assert stock["candidate_eligible"] is False


def test_board_width_fallbacks():
    engine = ReviewEngine()
    assert engine._board_width("300001.SZ", "智航机器", {}) == 0.20
    assert engine._board_width("688001.SH", "科创样本", {}) == 0.20
    assert engine._board_width("600001.SH", "*ST样本", {}) == 0.05
    assert engine._board_width("830001.BJ", "北交样本", {}) == 0.30
    assert engine._board_width("600001.SH", "华芯电子", {"600001.SH": 0.05}) == 0.05


def test_structured_daily_tables_and_prune(db):
    db.save_limit_universe("20260928", [{
        "code": "600001.SH", "name": "甲", "score": 80, "is_one_word": False,
        "buyable": True, "reason": "测试",
    }])
    db.save_sector_snapshot("20260928", [{"name": "人工智能", "score": 70}])
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM limit_up_daily").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM sector_daily").fetchone()[0] == 1
    old = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO market_snapshots(captured_at,quote_time,ts_code,payload) VALUES(?,?,?,?)",
            (old, "", "600001.SH", "{}"),
        )
    result = db.prune_data()
    assert result["snapshots"] == 1


def test_evaluation_round_trip(db):
    payload = {"target_date": "20260929", "cash_call": {"verdict": "有效"}}
    db.save_evaluation("20260929", payload)
    assert db.get_evaluation()["cash_call"]["verdict"] == "有效"
