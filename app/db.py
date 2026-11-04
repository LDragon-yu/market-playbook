from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS market_reviews (
    trade_date TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS premarket_plans (
    target_date TEXT PRIMARY KEY,
    source_trade_date TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'frozen',
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_evaluations (
    target_date TEXT PRIMARY KEY,
    evaluated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intelligence_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    summary TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    image_path TEXT
);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS limit_up_daily (
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    name TEXT NOT NULL,
    sector TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    board_height INTEGER NOT NULL DEFAULT 1,
    board_type TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL DEFAULT 0,
    risk TEXT NOT NULL DEFAULT '',
    pct_chg REAL NOT NULL DEFAULT 0,
    turnover_rate REAL NOT NULL DEFAULT 0,
    amount REAL NOT NULL DEFAULT 0,
    first_limit_time TEXT NOT NULL DEFAULT '',
    is_one_word INTEGER NOT NULL DEFAULT 0,
    buyable INTEGER NOT NULL DEFAULT 1,
    not_buyable_reason TEXT NOT NULL DEFAULT '',
    tag TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, ts_code)
);

CREATE TABLE IF NOT EXISTS sector_daily (
    trade_date TEXT NOT NULL,
    name TEXT NOT NULL,
    code TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL DEFAULT 0,
    tier TEXT NOT NULL DEFAULT '',
    pct_chg REAL NOT NULL DEFAULT 0,
    limit_up_count INTEGER NOT NULL DEFAULT 0,
    high_board_count INTEGER NOT NULL DEFAULT 0,
    active_days INTEGER NOT NULL DEFAULT 0,
    net_amount REAL NOT NULL DEFAULT 0,
    lead_stock TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, name)
);

CREATE TABLE IF NOT EXISTS premarket_plan_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_date TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    archived_at TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        # These pragmas are per-connection in SQLite; concurrent writers (web,
        # monitor thread, Feishu child, cron) must wait instead of failing.
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                (2, datetime.now().isoformat(timespec="seconds")),
            )

    @staticmethod
    def _dump(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load(value: str) -> dict[str, Any]:
        return json.loads(value)

    def save_review(self, trade_date: str, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO market_reviews(trade_date, generated_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    generated_at=excluded.generated_at,
                    payload=excluded.payload
                """,
                (trade_date, datetime.now().isoformat(timespec="seconds"), self._dump(payload)),
            )

    def get_review(self, trade_date: str | None = None) -> dict[str, Any] | None:
        query = "SELECT payload FROM market_reviews"
        params: tuple[Any, ...] = ()
        if trade_date:
            query += " WHERE trade_date = ?"
            params = (trade_date,)
        else:
            query += " ORDER BY trade_date DESC LIMIT 1"
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return self._load(row["payload"]) if row else None

    def list_review_dates(self, limit: int = 60) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT trade_date FROM market_reviews ORDER BY trade_date DESC LIMIT ?", (limit,)
            ).fetchall()
        return [row["trade_date"] for row in rows]

    def save_plan(
        self,
        target_date: str,
        source_trade_date: str,
        payload: dict[str, Any],
        *,
        force: bool = False,
    ) -> None:
        """Persist a plan without silently rewriting a frozen one.

        Re-saving the same generated payload (e.g. adding positions after the
        pipeline built it) is always allowed. A new version of a plan for a past
        date, or for today after 09:15, is refused unless ``force`` is set; the
        previous version is archived instead of lost.
        """
        now = datetime.now()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT generated_at, status, payload FROM premarket_plans WHERE target_date=?",
                (target_date,),
            ).fetchone()
            if existing:
                old_payload = self._load(existing["payload"])
                same_version = old_payload.get("generated_at") == payload.get("generated_at")
                if not same_version:
                    if existing["status"] == "frozen" and not force:
                        today = now.strftime("%Y%m%d")
                        if target_date < today:
                            raise ValueError(
                                f"{target_date} 的预案属于历史记录，不能覆盖；如需重算，请显式使用 --force。"
                            )
                        if target_date == today and (now.hour, now.minute) >= (9, 15):
                            raise ValueError(
                                "当日预案已冻结（09:15 后不可改写）。"
                                "如需修正请显式使用 --force，旧版本会自动归档。"
                            )
                    connection.execute(
                        """INSERT INTO premarket_plan_history(
                        target_date, generated_at, archived_at, status, payload)
                        VALUES (?, ?, ?, ?, ?)""",
                        (
                            target_date,
                            existing["generated_at"],
                            now.isoformat(timespec="seconds"),
                            existing["status"],
                            existing["payload"],
                        ),
                    )
            connection.execute(
                """
                INSERT INTO premarket_plans(target_date, source_trade_date, generated_at, status, payload)
                VALUES (?, ?, ?, 'frozen', ?)
                ON CONFLICT(target_date) DO UPDATE SET
                    source_trade_date=excluded.source_trade_date,
                    generated_at=excluded.generated_at,
                    payload=excluded.payload,
                    status='frozen'
                """,
                (
                    target_date,
                    source_trade_date,
                    datetime.now().isoformat(timespec="seconds"),
                    self._dump(payload),
                ),
            )

    def get_plan(self, target_date: str | None = None) -> dict[str, Any] | None:
        query = "SELECT payload FROM premarket_plans"
        params: tuple[Any, ...] = ()
        if target_date:
            query += " WHERE target_date = ?"
            params = (target_date,)
        else:
            query += " ORDER BY target_date DESC LIMIT 1"
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return self._load(row["payload"]) if row else None

    def save_evaluation(self, target_date: str, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO plan_evaluations(target_date, evaluated_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(target_date) DO UPDATE SET
                    evaluated_at=excluded.evaluated_at,
                    payload=excluded.payload
                """,
                (target_date, datetime.now().isoformat(timespec="seconds"), self._dump(payload)),
            )

    def get_evaluation(self, target_date: str | None = None) -> dict[str, Any] | None:
        query = "SELECT payload FROM plan_evaluations"
        params: tuple[Any, ...] = ()
        if target_date:
            query += " WHERE target_date = ?"
            params = (target_date,)
        else:
            query += " ORDER BY target_date DESC LIMIT 1"
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return self._load(row["payload"]) if row else None

    def save_limit_universe(self, trade_date: str, stocks: list[dict[str, Any]]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute("DELETE FROM limit_up_daily WHERE trade_date=?", (trade_date,))
            for stock in stocks:
                connection.execute(
                    """INSERT OR REPLACE INTO limit_up_daily(
                    trade_date, ts_code, name, sector, role, board_height, board_type, score, risk,
                    pct_chg, turnover_rate, amount, first_limit_time, is_one_word, buyable,
                    not_buyable_reason, tag, reason, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        trade_date,
                        str(stock.get("code", "")),
                        str(stock.get("name", "")),
                        str(stock.get("sector", "")),
                        str(stock.get("role", "")),
                        int(stock.get("board_height", 1) or 1),
                        str(stock.get("board_type", "")),
                        float(stock.get("score", 0) or 0),
                        str(stock.get("risk", "")),
                        float(stock.get("pct_chg", 0) or 0),
                        float(stock.get("turnover_rate", 0) or 0),
                        float(stock.get("amount", 0) or 0),
                        str(stock.get("first_limit_time", "")),
                        1 if stock.get("is_one_word") else 0,
                        1 if stock.get("buyable", True) else 0,
                        str(stock.get("not_buyable_reason", "")),
                        str(stock.get("tag", "")),
                        str(stock.get("reason", ""))[:500],
                        now,
                    ),
                )

    def save_sector_snapshot(self, trade_date: str, sectors: list[dict[str, Any]]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute("DELETE FROM sector_daily WHERE trade_date=?", (trade_date,))
            for sector in sectors:
                connection.execute(
                    """INSERT OR REPLACE INTO sector_daily(
                    trade_date, name, code, score, tier, pct_chg, limit_up_count,
                    high_board_count, active_days, net_amount, lead_stock, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        trade_date,
                        str(sector.get("name", "")),
                        str(sector.get("code", "")),
                        float(sector.get("score", 0) or 0),
                        str(sector.get("tier", "")),
                        float(sector.get("pct_chg", 0) or 0),
                        int(sector.get("limit_up_count", 0) or 0),
                        int(sector.get("high_board_count", 0) or 0),
                        int(sector.get("active_days", 0) or 0),
                        float(sector.get("net_amount", 0) or 0),
                        str(sector.get("lead_stock", "")),
                        now,
                    ),
                )

    def prune_data(
        self,
        *,
        snapshot_days: int = 30,
        alert_days: int = 90,
        job_days: int = 180,
    ) -> dict[str, int]:
        """Delete high-volume history that no longer serves any workflow."""
        now = datetime.now()
        snapshots = (now - timedelta(days=snapshot_days)).isoformat(timespec="seconds")
        alerts = (now - timedelta(days=alert_days)).isoformat(timespec="seconds")
        jobs = (now - timedelta(days=job_days)).isoformat(timespec="seconds")
        with self.connect() as connection:
            deleted = {
                "snapshots": connection.execute(
                    "DELETE FROM market_snapshots WHERE captured_at < ?", (snapshots,)
                ).rowcount,
                "alerts": connection.execute(
                    "DELETE FROM monitor_alerts WHERE created_at < ?", (alerts,)
                ).rowcount,
                "jobs": connection.execute(
                    "DELETE FROM job_runs WHERE started_at < ? AND status != 'running'", (jobs,)
                ).rowcount,
            }
        return deleted

    def add_intelligence(
        self,
        *,
        source_name: str,
        source_type: str,
        title: str,
        raw_text: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
        image_path: str | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO intelligence_items(
                    observed_at, source_name, source_type, title, raw_text,
                    summary, metadata, image_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    source_name,
                    source_type,
                    title,
                    raw_text,
                    summary,
                    self._dump(metadata or {}),
                    image_path,
                ),
            )
            return int(cursor.lastrowid)

    def list_intelligence(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, observed_at, source_name, source_type, title, summary, metadata, image_path
                FROM intelligence_items ORDER BY observed_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                **dict(row),
                "metadata": self._load(row["metadata"]),
            }
            for row in rows
        ]

    def recent_intelligence_raw(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM intelligence_items ORDER BY observed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{**dict(row), "metadata": self._load(row["metadata"])} for row in rows]

    def start_job(self, name: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO job_runs(job_name, started_at, status) VALUES (?, ?, 'running')",
                (name, datetime.now().isoformat(timespec="seconds")),
            )
            return int(cursor.lastrowid)

    def finish_job(self, job_id: int, status: str, detail: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE job_runs SET finished_at=?, status=?, detail=? WHERE id=?",
                (datetime.now().isoformat(timespec="seconds"), status, detail, job_id),
            )

    def latest_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

