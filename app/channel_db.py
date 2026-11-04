"""Durable inbox/outbox for the single Feishu channel. No API secrets live here."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.db import Database


SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_inbox (
    message_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
    text TEXT NOT NULL, received REAL NOT NULL, status TEXT NOT NULL DEFAULT 'queued'
);
CREATE TABLE IF NOT EXISTS channel_outbox (
    id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE, chat_id TEXT NOT NULL,
    text TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
    next_attempt REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS channel_drafts (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
    payload TEXT NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS channel_materials (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
    url TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '', intelligence_id INTEGER
);
CREATE TABLE IF NOT EXISTS channel_worker (
    id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL,
    state TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
"""


class ChannelRepository:
    def __init__(self, db: Database):
        self.db = db
        with db.connect() as c:
            c.executescript(SCHEMA)

    def accept(self, message_id: str, user_id: str, chat_id: str, text: str, received: float | None = None) -> bool:
        with self.db.connect() as c:
            count = c.execute("SELECT count(*) FROM channel_inbox WHERE status='queued'").fetchone()[0]
            if count >= 200:
                return False
            return bool(c.execute(
                "INSERT OR IGNORE INTO channel_inbox(message_id,user_id,chat_id,text,received) VALUES(?,?,?,?,?)",
                (message_id, user_id, chat_id, text[:30000], received if received is not None else time.time()),
            ).rowcount)

    def claim_inbox(self) -> dict[str, Any] | None:
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM channel_inbox WHERE status='queued' ORDER BY received LIMIT 1").fetchone()
            if row:
                c.execute("UPDATE channel_inbox SET status='processing' WHERE message_id=?", (row["message_id"],))
            return dict(row) if row else None

    def finish_inbox(self, message_id: str) -> None:
        with self.db.connect() as c:
            c.execute("UPDATE channel_inbox SET status='done' WHERE message_id=?", (message_id,))

    def recover(self) -> None:
        # Never replay a possibly-completed holding mutation after a crash.
        with self.db.connect() as c:
            c.execute("UPDATE channel_inbox SET status='interrupted' WHERE status='processing'")
            c.execute("UPDATE channel_drafts SET status='interrupted' WHERE status='processing'")
            c.execute("UPDATE channel_outbox SET status='queued' WHERE status='sending'")

    def enqueue(self, chat_id: str, text: str, *, dedupe_key: str = "", ttl: int = 3600) -> str:
        item_id = str(uuid.uuid4())
        now = time.time()
        key = dedupe_key or item_id
        with self.db.connect() as c:
            c.execute(
                """INSERT OR IGNORE INTO channel_outbox
                (id,dedupe_key,chat_id,text,created,expires,next_attempt) VALUES(?,?,?,?,?,?,?)""",
                (item_id, key, chat_id, text[:7000], now, now + ttl, now),
            )
            return str(c.execute("SELECT id FROM channel_outbox WHERE dedupe_key=?", (key,)).fetchone()[0])

    def claim_outbox(self) -> dict[str, Any] | None:
        now = time.time()
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE channel_outbox SET status='expired' WHERE status='queued' AND expires<=?", (now,))
            row = c.execute(
                "SELECT * FROM channel_outbox WHERE status='queued' AND next_attempt<=? ORDER BY created LIMIT 1", (now,)
            ).fetchone()
            if row:
                c.execute("UPDATE channel_outbox SET status='sending' WHERE id=?", (row["id"],))
            return dict(row) if row else None

    def delivered(self, item_id: str) -> None:
        with self.db.connect() as c:
            c.execute("UPDATE channel_outbox SET status='sent',error='' WHERE id=?", (item_id,))

    def failed(self, item: dict[str, Any], error: str, *, retryable: bool) -> None:
        attempts = int(item["attempts"]) + 1
        status = "queued" if retryable and attempts < 5 else "failed"
        with self.db.connect() as c:
            c.execute("UPDATE channel_outbox SET status=?,attempts=?,error=?,next_attempt=? WHERE id=?",
                      (status, attempts, error[:160], time.time() + min(60, 2 ** attempts), item["id"]))

    def draft(self, user: str, chat: str, payload: dict[str, Any]) -> str:
        item_id = uuid.uuid4().hex[:10]
        with self.db.connect() as c:
            c.execute("INSERT INTO channel_drafts(id,user_id,chat_id,payload,expires) VALUES(?,?,?,?,?)",
                      (item_id, user, chat, json.dumps(payload, ensure_ascii=False), time.time() + 600))
        return item_id

    def take_draft(self, item_id: str, user: str, chat: str, *, cancel: bool = False) -> dict[str, Any]:
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM channel_drafts WHERE id=? AND user_id=? AND chat_id=?",
                            (item_id, user, chat)).fetchone()
            if not row or row["status"] != "pending" or row["expires"] <= time.time():
                raise ValueError("草稿不存在、已处理或超过 10 分钟，请重新提交。")
            c.execute("UPDATE channel_drafts SET status=? WHERE id=?",
                      ("cancelled" if cancel else "processing", item_id))
        return json.loads(row["payload"])

    def finish_draft(self, item_id: str, status: str) -> None:
        with self.db.connect() as c:
            c.execute("UPDATE channel_drafts SET status=? WHERE id=?", (status, item_id))

    def add_material(self, user: str, chat: str, url: str) -> str:
        item_id = uuid.uuid4().hex[:10]
        with self.db.connect() as c:
            c.execute("INSERT INTO channel_materials(id,user_id,chat_id,url,created,status) VALUES(?,?,?,?,?,'received')",
                      (item_id, user, chat, url, time.time()))
        return item_id

    def material(self, item_id: str, user: str, chat: str) -> dict[str, Any]:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM channel_materials WHERE id=? AND user_id=? AND chat_id=?",
                            (item_id, user, chat)).fetchone()
        if not row:
            raise ValueError("素材编号不存在或不是当前会话提交的素材。")
        return dict(row)

    def update_material(self, item_id: str, status: str, detail: str, intelligence_id: int | None = None) -> None:
        with self.db.connect() as c:
            c.execute("UPDATE channel_materials SET status=?,detail=?,intelligence_id=? WHERE id=?",
                      (status, detail[:500], intelligence_id, item_id))

    def heartbeat(self, state: str, detail: str = "") -> None:
        with self.db.connect() as c:
            c.execute("""INSERT INTO channel_worker(id,heartbeat,state,detail) VALUES(1,?,?,?)
                ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,state=excluded.state,detail=excluded.detail""",
                      (time.time(), state, detail[:160]))

    def status(self) -> dict[str, Any]:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM channel_worker WHERE id=1").fetchone()
            counts = dict(c.execute("SELECT status,count(*) FROM channel_outbox GROUP BY status").fetchall())
            messages = [dict(r) for r in c.execute(
                "SELECT id,created,status,attempts,error FROM channel_outbox ORDER BY created DESC LIMIT 10")]
            materials = [dict(r) for r in c.execute(
                "SELECT id,url,created,status,detail,intelligence_id FROM channel_materials ORDER BY created DESC LIMIT 20")]
            interrupted = c.execute("SELECT count(*) FROM channel_inbox WHERE status='interrupted'").fetchone()[0]
        return {"worker": dict(row) if row else None, "outbox_counts": counts,
                "messages": messages, "materials": materials, "interrupted_requests": interrupted}
