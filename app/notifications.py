"""One reusable outbound entry point; queue failures must not break market jobs."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.channel_db import ChannelRepository
from app.db import Database
from app.settings import Settings


def plan_text(plan: dict[str, Any]) -> str:
    lines = [f"盘前预案 {plan.get('target_date', '')}",
             f"总策略：{plan.get('decision', '信息不足')}；仓位上限 {plan.get('max_exposure_pct', 0)}%"]
    if plan.get("provenance", {}).get("is_demo"):
        lines.insert(0, "【演示数据，禁止据此交易】")
    lines.extend(str(x) for x in plan.get("market_gate", [])[:5])
    for x in plan.get("positions", [])[:8]:
        lines.append(f"持仓 {x.get('name')} {x.get('ts_code')}：{x.get('action')}；可卖 {x.get('sellable_qty')}；风险 {x.get('risk')}")
    for x in plan.get("candidates", [])[:5]:
        lines.append(f"观察 {x.get('name')} {x.get('code')} / {x.get('sector')}")
        lines.extend(f"{s.get('name')}：{s.get('condition')} → {s.get('action')}" for s in x.get("scenarios", [])[:5])
    lines.append("条件研究预案，不自动下单；需核对日期、可卖数量及最新行情。")
    return "\n".join(lines)[:6500]


def review_text(review: dict[str, Any]) -> str:
    m = review.get("market", {})
    lines = [f"盘后复盘 {review.get('trade_date', '')}",
             f"情绪：{m.get('regime', '未知')} / {m.get('emotion_score', '—')}；最高 {m.get('max_board_height', 0)} 板",
             f"涨停 {m.get('limit_up', 0)} / 跌停 {m.get('limit_down', 0)}；建议空仓：{bool(m.get('cash_recommended'))}"]
    if review.get("provenance", {}).get("is_demo"):
        lines.insert(0, "【演示数据，禁止据此交易】")
    lines.extend(f"{s.get('name')}：{s.get('tier')}，涨停 {s.get('limit_up_count', 0)}，领涨 {s.get('lead_stock', '—')}"
                 for s in review.get("sectors", [])[:6])
    for ladder in review.get("ladders", [])[:6]:
        lines.append(f"{ladder.get('height')} 板：" + "、".join(str(s.get("name")) for s in ladder.get("stocks", [])))
    lines.append("完整复盘请查看 Web；历史强势不代表次日可买。")
    return "\n".join(lines)[:6500]


class Notifications:
    def __init__(self, settings: Settings, db: Database):
        self.settings, self.db = settings, db

    def send(self, title: str, body: str, *, source: str = "见势", dedupe_key: str = "", ttl: int = 3600) -> dict[str, Any]:
        if not self.settings.feishu_configured or not self.settings.feishu_notify_chat_id:
            return {"status": "disabled", "reason": "飞书未启用或未配置通知会话 ID"}
        text = f"【{source}】{title}\n{body}"
        repo = ChannelRepository(self.db)
        item_id = repo.enqueue(self.settings.feishu_notify_chat_id, text, dedupe_key=dedupe_key, ttl=ttl)
        return {"status": "queued", "id": item_id}

    def job(self, kind: str, payload: dict[str, Any]) -> None:
        text = review_text(payload) if kind == "review" else plan_text(payload)
        digest = hashlib.sha256(text.encode()).hexdigest()[:24]
        self._safe_send(kind, text, dedupe_key=f"{kind}:{digest}", ttl=12 * 3600)

    def alert(self, alert: dict[str, Any], decision: dict[str, Any]) -> None:
        text = f"{alert.get('ts_code')}：{alert.get('title')}\n{decision.get('summary', '')}\n辅助动作：{decision.get('action', '信息不足')}\n生成：{decision.get('generated_at', '')}\n失效：{decision.get('expires_at', '')}\n请核对最新快照与 T+1；不自动下单。"
        self._safe_send("风险提醒", text, dedupe_key=f"alert:{alert.get('id')}", ttl=90)

    def _safe_send(self, title: str, text: str, **kwargs: Any) -> None:
        try:
            self.send(title, text, **kwargs)
        except Exception:
            logging.getLogger(__name__).warning("通知未能入队；不影响复盘与监控，请检查数据库。")
