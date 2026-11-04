from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.db import Database


PORTFOLIO_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_profiles (
    ts_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    industry TEXT NOT NULL DEFAULT '',
    sw_l1 TEXT NOT NULL DEFAULT '',
    sw_l2 TEXT NOT NULL DEFAULT '',
    sw_l3 TEXT NOT NULL DEFAULT '',
    concepts TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code TEXT NOT NULL,
    name TEXT NOT NULL,
    total_qty REAL NOT NULL DEFAULT 0,
    sellable_qty REAL NOT NULL DEFAULT 0,
    cost_price REAL NOT NULL DEFAULT 0,
    buy_date TEXT NOT NULL DEFAULT '',
    strategy_mode TEXT NOT NULL DEFAULT 'comprehensive',
    thesis TEXT NOT NULL DEFAULT '',
    invalidation TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_position_open_code
ON positions(ts_code) WHERE status='open';

CREATE TABLE IF NOT EXISTS monitor_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    sector TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 50,
    peer_code TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'auto',
    enabled INTEGER NOT NULL DEFAULT 1,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_monitor_unique
ON monitor_items(trade_date, ts_code, role, peer_code);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    quote_time TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshot_code_time
ON market_snapshots(ts_code, captured_at DESC);

CREATE TABLE IF NOT EXISTS monitor_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    payload TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stock_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS trading_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    expires_at TEXT,
    ts_code TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    payload TEXT NOT NULL
);
"""


class PortfolioRepository:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback

    def initialize(self) -> None:
        with self.db.connect() as connection:
            connection.executescript(PORTFOLIO_SCHEMA)

    def upsert_profile(self, profile: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO stock_profiles(ts_code,name,industry,sw_l1,sw_l2,sw_l3,concepts,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(ts_code) DO UPDATE SET name=excluded.name,industry=excluded.industry,
                    sw_l1=excluded.sw_l1,sw_l2=excluded.sw_l2,sw_l3=excluded.sw_l3,
                    concepts=excluded.concepts,updated_at=excluded.updated_at
                """,
                (profile["ts_code"], profile.get("name", profile["ts_code"]),
                 profile.get("industry", ""), profile.get("sw_l1", ""),
                 profile.get("sw_l2", ""), profile.get("sw_l3", ""),
                 self._dump(profile.get("concepts", [])), now),
            )

    def get_profile(self, ts_code: str) -> dict[str, Any] | None:
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM stock_profiles WHERE ts_code=?", (ts_code,)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["concepts"] = self._load(value["concepts"], [])
        return value

    def list_profiles(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        if not codes:
            return {}
        marks = ",".join("?" for _ in codes)
        with self.db.connect() as connection:
            rows = connection.execute(f"SELECT * FROM stock_profiles WHERE ts_code IN ({marks})", codes).fetchall()
        result = {}
        for row in rows:
            value = dict(row)
            value["concepts"] = self._load(value["concepts"], [])
            result[value["ts_code"]] = value
        return result

    def save_position(self, value: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        position_id = value.get("id")
        with self.db.connect() as connection:
            if "_expected_position" in value:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute("SELECT * FROM positions WHERE ts_code=? AND status='open'",
                                             (value["ts_code"],)).fetchone()
                if (dict(current) if current else None) != value["_expected_position"]:
                    raise ValueError("提交草稿后持仓已发生变化；为避免覆盖，请重新提交并确认。")
            if position_id:
                connection.execute(
                    """UPDATE positions SET ts_code=?,name=?,total_qty=?,sellable_qty=?,cost_price=?,
                    buy_date=?,strategy_mode=?,thesis=?,invalidation=?,updated_at=? WHERE id=?""",
                    (value["ts_code"], value["name"], value.get("total_qty", 0),
                     value.get("sellable_qty", 0), value.get("cost_price", 0), value.get("buy_date", ""),
                     value.get("strategy_mode", "comprehensive"), value.get("thesis", ""),
                     value.get("invalidation", ""), now, position_id),
                )
            else:
                existing = connection.execute(
                    "SELECT id FROM positions WHERE ts_code=? AND status='open'", (value["ts_code"],)
                ).fetchone()
                if existing:
                    position_id = int(existing["id"])
                    connection.execute(
                        """UPDATE positions SET name=?,total_qty=?,sellable_qty=?,cost_price=?,buy_date=?,
                        strategy_mode=?,thesis=?,invalidation=?,updated_at=? WHERE id=?""",
                        (value["name"], value.get("total_qty", 0), value.get("sellable_qty", 0),
                         value.get("cost_price", 0), value.get("buy_date", ""),
                         value.get("strategy_mode", "comprehensive"), value.get("thesis", ""),
                         value.get("invalidation", ""), now, position_id),
                    )
                else:
                    cursor = connection.execute(
                        """INSERT INTO positions(ts_code,name,total_qty,sellable_qty,cost_price,buy_date,
                        strategy_mode,thesis,invalidation,status,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,'open',?,?)""",
                        (value["ts_code"], value["name"], value.get("total_qty", 0),
                         value.get("sellable_qty", 0), value.get("cost_price", 0), value.get("buy_date", ""),
                         value.get("strategy_mode", "comprehensive"), value.get("thesis", ""),
                         value.get("invalidation", ""), now, now),
                    )
                    position_id = int(cursor.lastrowid)
        return self.get_position(int(position_id))

    def get_position(self, position_id: int) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        if not row:
            raise ValueError("持仓不存在。")
        return dict(row)

    def list_positions(self, open_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM positions"
        if open_only:
            query += " WHERE status='open'"
        query += " ORDER BY updated_at DESC"
        with self.db.connect() as connection:
            return [dict(row) for row in connection.execute(query).fetchall()]

    def close_position(self, position_id: int) -> None:
        with self.db.connect() as connection:
            connection.execute("UPDATE positions SET status='closed',updated_at=? WHERE id=?",
                               (datetime.now().isoformat(timespec="seconds"), position_id))

    def replace_auto_monitors(self, trade_date: str, items: list[dict[str, Any]]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.db.connect() as connection:
            connection.execute("DELETE FROM monitor_items WHERE trade_date=? AND source='auto'", (trade_date,))
            for item in items:
                connection.execute(
                    """INSERT OR IGNORE INTO monitor_items(trade_date,ts_code,name,role,sector,priority,
                    peer_code,source,enabled,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,1,?,?)""",
                    (trade_date, item["ts_code"], item.get("name", item["ts_code"]), item["role"],
                     item.get("sector", ""), item.get("priority", 50), item.get("peer_code", ""),
                     item.get("source", "auto"), self._dump(item.get("metadata", {})), now),
                )

    def add_monitor(self, item: dict[str, Any]) -> int:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO monitor_items(trade_date,ts_code,name,role,sector,priority,peer_code,
                source,enabled,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,1,?,?)""",
                (item["trade_date"], item["ts_code"], item.get("name", item["ts_code"]),
                 item.get("role", "manual"), item.get("sector", ""), item.get("priority", 50),
                 item.get("peer_code", ""), "manual", self._dump(item.get("metadata", {})),
                 datetime.now().isoformat(timespec="seconds")),
            )
            return int(cursor.lastrowid)

    def list_monitors(self, trade_date: str | None = None, enabled_only: bool = True) -> list[dict[str, Any]]:
        clauses, params = [], []
        if trade_date:
            clauses.append("trade_date=?")
            params.append(trade_date)
        if enabled_only:
            clauses.append("enabled=1")
        query = "SELECT * FROM monitor_items"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY priority DESC,id"
        with self.db.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["metadata"] = self._load(value["metadata"], {})
            result.append(value)
        return result

    def save_snapshot(self, ts_code: str, quote: dict[str, Any], captured_at: str | None = None) -> int:
        when = captured_at or datetime.now().isoformat(timespec="seconds")
        with self.db.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO market_snapshots(captured_at,quote_time,ts_code,payload) VALUES(?,?,?,?)",
                (when, str(quote.get("trade_time", "")), ts_code, self._dump(quote)),
            )
            return int(cursor.lastrowid)

    def recent_snapshots(self, ts_code: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM market_snapshots WHERE ts_code=? ORDER BY id DESC LIMIT ?", (ts_code, limit)
            ).fetchall()
        result = []
        for row in reversed(rows):
            value = dict(row)
            value["payload"] = self._load(value["payload"], {})
            result.append(value)
        return result

    def recent_alert_exists(self, ts_code: str, event_type: str, minutes: int = 5) -> bool:
        since = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM monitor_alerts WHERE ts_code=? AND event_type=? AND created_at>=? LIMIT 1",
                (ts_code, event_type, since),
            ).fetchone()
        return row is not None

    def add_alert(self, alert: dict[str, Any]) -> int:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO monitor_alerts(created_at,ts_code,severity,event_type,title,payload)
                VALUES(?,?,?,?,?,?)""",
                (datetime.now().isoformat(timespec="seconds"), alert.get("ts_code", ""),
                 alert.get("severity", "info"), alert.get("event_type", "event"),
                 alert.get("title", "监控事件"), self._dump(alert)),
            )
            return int(cursor.lastrowid)

    def list_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute("SELECT * FROM monitor_alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": self._load(row["payload"], {})} for row in rows]

    def add_question(self, ts_code: str, question: str, answer: str, metadata: dict[str, Any]) -> int:
        with self.db.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO stock_questions(asked_at,ts_code,question,answer,metadata) VALUES(?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), ts_code, question, answer,
                 self._dump(metadata)),
            )
            return int(cursor.lastrowid)

    def list_questions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute("SELECT * FROM stock_questions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "metadata": self._load(row["metadata"], {})} for row in rows]

    def add_decision(self, value: dict[str, Any]) -> int:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO trading_decisions(generated_at,expires_at,ts_code,action,confidence,payload)
                VALUES(?,?,?,?,?,?)""",
                (value["generated_at"], value.get("expires_at"), value.get("ts_code", ""),
                 value.get("action", "观察"), value.get("confidence", 0), self._dump(value)),
            )
            return int(cursor.lastrowid)

    def list_decisions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute("SELECT * FROM trading_decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "payload": self._load(row["payload"], {})} for row in rows]
