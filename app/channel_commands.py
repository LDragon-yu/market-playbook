"""Inbound requests: deterministic mutations, LLM only for analysis, never orders."""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from typing import Any

from app.channel_db import ChannelRepository
from app.notifications import plan_text, review_text
from app.sources.public_article import fetch_article, is_video_url, validate_url


HELP = """见势双向助手（研究辅助，不自动下单）
/身份：查看自己的 open_id / chat_id，填入 Web 配置
/持仓查看：查看系统持仓
/持仓 000592.SZ 总量=1000 可卖=1000 成本=10.5 买入=20260930
  创建或替换该股整笔持仓草稿；确认前不入库
/确认 草稿编号 或 /取消 草稿编号（10 分钟有效）
/问 000592.SZ 二选一时是否应继续观察？
/复盘 或 /预案：读取已生成报告，不重新生成
/文案 正文：解析博主文案并入库
/材料 https://公开文章或视频链接（也可直接发链接）
/字幕 素材编号 视频字幕或转写全文
视频只收录链接；拿到字幕后才分析。群聊请 @机器人。
持仓日期与可卖数量必须核对；本通道不连接券商。"""


def event_message(data: dict[str, Any]) -> dict[str, Any] | None:
    event = data.get("event") or {}
    sender, message = event.get("sender") or {}, event.get("message") or {}
    if sender.get("sender_type") != "user":
        return None
    # Groups must explicitly mention this bot. Only group-at event permission should be granted.
    if message.get("chat_type") == "group" and not message.get("mentions"):
        return None
    try:
        content = json.loads(message.get("content", "{}"))
    except (ValueError, TypeError):
        return None
    if message.get("message_type") == "text":
        text = str(content.get("text", ""))
    elif message.get("message_type") == "post":
        document = content if "content" in content else content.get("zh_cn") or content.get("en_us") or {}
        lines = [str(document.get("title", ""))]
        for line in document.get("content", []):
            lines.append(" ".join(str(x.get("text") or x.get("href") or "") for x in line if x.get("tag") != "at"))
        text = "\n".join(lines)
    else:
        text = "/不支持文件"
    for mention in message.get("mentions", []):
        text = text.replace(str(mention.get("key", "@_user_0")), "")
    result = {"message_id": str(message.get("message_id", "")),
              "user_id": str((sender.get("sender_id") or {}).get("open_id", "")),
              "chat_id": str(message.get("chat_id", "")), "text": text.strip()}
    if not all(result.values()):
        return None
    created = str(message.get("create_time", ""))
    result["received"] = min(time.time(), int(created) / 1000) if created.isdigit() else time.time()
    return result


def holding_payload(text: str, *, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    parts = text.split()
    if len(parts) != 5 or not re.fullmatch(r"\d{6}\.(SZ|SH|BJ)", parts[0].upper()):
        raise ValueError("请按 /持仓 000592.SZ 总量=1000 可卖=1000 成本=10.5 买入=20260930 的格式提交。")
    fields: dict[str, str] = {}
    for item in parts[1:]:
        key, sep, value = item.partition("=")
        if not sep or key in fields:
            raise ValueError("持仓字段缺失或重复。")
        fields[key] = value
    if set(fields) != {"总量", "可卖", "成本", "买入"}:
        raise ValueError("必须明确提供总量、可卖、成本、买入日期。")
    if not re.fullmatch(r"\d+", fields["总量"]) or not re.fullmatch(r"\d+", fields["可卖"]):
        raise ValueError("股票数量必须是非负整数股数，不是手数。")
    total, sellable = int(fields["总量"]), int(fields["可卖"])
    if not re.fullmatch(r"\d+(\.\d{1,6})?", fields["成本"]):
        raise ValueError("成本必须是正常正数。")
    cost = float(fields["成本"])
    if not 0 < total <= 100000000 or not 0 <= sellable <= total or not 0 < cost <= 100000:
        raise ValueError("数量、可卖数量或成本不合法；清仓请在 Web 手工处理。")
    buy = fields["买入"].replace("-", "")
    if not re.fullmatch(r"\d{8}", buy):
        raise ValueError("买入日期应为 YYYYMMDD。")
    bought = datetime.strptime(buy, "%Y%m%d").date()
    if bought > today:
        raise ValueError("买入日期不能晚于今天。")
    if bought == today and sellable:
        raise ValueError("当天买入的这笔 A 股持仓不能可卖；混合仓位请通过 Web 核对。")
    return {"ts_code": parts[0].upper(), "total_qty": total, "sellable_qty": sellable,
            "cost_price": cost, "buy_date": buy, "strategy_mode": "comprehensive"}


class ChannelCommands:
    def __init__(self, settings: Any, db: Any, pipeline: Any, portfolio: Any):
        self.settings, self.db, self.pipeline, self.portfolio = settings, db, pipeline, portfolio
        self.repository = ChannelRepository(db)

    def allowed(self, user_id: str) -> bool:
        return user_id in {s.strip() for s in self.settings.feishu_allowed_users.split(",") if s.strip()}

    def dispatch(self, user: str, chat: str, text: str) -> str:
        text = text.strip()
        if text in {"/身份", "身份"}:
            return f"你的 open_id：{user}\n当前 chat_id：{chat}\n请在 Web 的飞书配置中加入用户白名单；通知会话填这个 chat_id。"
        if not self.allowed(user):
            return "你尚未被授权。仅可发送 /身份 获取自己的 ID，再由项目管理员通过 Web 添加白名单。"
        if text in {"/帮助", "帮助", "/help"}:
            return HELP
        if text in {"/持仓查看", "持仓查看"}:
            positions = self.portfolio.repository.list_positions()
            return "系统已录入持仓（请核对券商实际可卖数量）：\n" + ("\n".join(
                f"{p['name']} {p['ts_code']}：总量 {p['total_qty']}，可卖 {p['sellable_qty']}，成本 {p['cost_price']}，买入 {p['buy_date']}"
                for p in positions) or "暂无持仓")
        if text in {"/复盘", "/预案"}:
            value = self.db.get_review() if text == "/复盘" else self.db.get_plan()
            return (review_text(value) if text == "/复盘" else plan_text(value)) if value else "暂无已生成的报告，请先在 Web 或定时任务中生成。"
        parts = text.split(maxsplit=1)
        command, rest = (parts[0], parts[1].strip() if len(parts) > 1 else "") if parts else ("", "")
        if command == "/持仓":
            payload = holding_payload(rest)
            existing = next((p for p in self.portfolio.repository.list_positions() if p["ts_code"] == payload["ts_code"]), None)
            if existing:
                # Preserve strategy notes, and require confirmation even for replacement.
                payload = {**existing, **payload}
            payload["_expected_updated_at"] = existing.get("updated_at") if existing else None
            payload["_expected_position"] = existing
            item_id = self.repository.draft(user, chat, payload)
            return (f"待确认{'替换' if existing else '新增'}：{payload['ts_code']}\n总量 {payload['total_qty']} 股；可卖 {payload['sellable_qty']} 股；成本 {payload['cost_price']}；买入 {payload['buy_date']}\n"
                    f"这是该股完整持仓，不是增量。10 分钟内发送 /确认 {item_id}；取消用 /取消 {item_id}。不执行交易。")
        if command in {"/确认", "/取消"}:
            payload = self.repository.take_draft(rest, user, chat, cancel=command == "/取消")
            if command == "/取消":
                return "草稿已取消，持仓未修改。"
            # A draft may cross midnight. Do not silently change its confirmed sellable quantity.
            try:
                current = next((p for p in self.portfolio.repository.list_positions() if p["ts_code"] == payload["ts_code"]), None)
                if current != payload.get("_expected_position"):
                    raise ValueError("提交草稿后持仓已发生变化；为避免覆盖，请重新提交并确认。")
                refreshed = holding_payload(f"{payload['ts_code']} 总量={payload['total_qty']} 可卖={payload['sellable_qty']} 成本={payload['cost_price']:.6f} 买入={payload['buy_date']}")
                saved = self.portfolio.save_position({**payload, **refreshed, "preserve_sellable_qty": True})
            except Exception:
                self.repository.finish_draft(rest, "failed")
                raise
            self.repository.finish_draft(rest, "saved")
            return f"已更新系统持仓：{saved['ts_code']}，总量 {saved['total_qty']}，可卖 {saved['sellable_qty']}。下一次盘前任务会结合它分析；现有冻结预案不会事后改写。"
        if command == "/问":
            parts = rest.split(maxsplit=1)
            code, question = (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")
            if not re.fullmatch(r"\d{6}\.(SZ|SH|BJ)", code.upper()) or not 2 <= len(question.strip()) <= 2000:
                raise ValueError("请按 /问 000592.SZ 你的问题 的格式提交，代码需带交易所后缀。")
            answer = self.portfolio.ask_stock(code, question)
            lines = [f"{answer.get('name', '')} {code.upper()} · 数据截至 {answer.get('data_as_of', '未知')}",
                     f"分析模式：{answer.get('analysis_mode', '未知')}", str(answer.get("answer", "")), str(answer.get("conclusion", ""))]
            for key, label in (("facts", "事实"), ("conditions", "条件"), ("invalidation", "失效"), ("risks", "风险"), ("unknowns", "未知")):
                if isinstance(answer.get(key), list):
                    lines.extend(f"{label}：{s}" for s in answer[key][:6])
            lines.append("非交易指令；一次快照不能证实资金流向，行情延迟时请先核对。")
            return "\n".join(lines)[:6500]
        if command == "/文案":
            return self._analyze(rest, "飞书文案", "飞书提交的博主文案")
        if command == "/字幕":
            parts = rest.split(maxsplit=1)
            item_id, transcript = (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")
            material = self.repository.material(item_id, user, chat)
            if material["status"] == "analyzed":
                return "该素材已经分析；如需更新，请重新提交链接或用 /文案 提交补充。"
            return self._analyze(transcript, "飞书字幕/用户提供", "用户提供的视频字幕", item_id, material["url"])
        if command == "/材料" or text.startswith(("https://", "http://")):
            url = rest if command == "/材料" else text
            validate_url(url)
            item_id = self.repository.add_material(user, chat, url)
            if is_video_url(url):
                reason = "视频仅收录链接，未获取字幕、未观看视频。"
                self.repository.update_material(item_id, "pending_transcript", reason)
                return f"素材 {item_id} 已收录。{reason}\n请发送 /字幕 {item_id} 字幕或转写全文，收到后才解析入库。"
            try:
                title, body = fetch_article(url)
            except ValueError as exc:
                self.repository.update_material(item_id, "pending_text", str(exc))
                return f"素材 {item_id} 已收录，但未取得正文：{exc}\n请发送 /字幕 {item_id} 正文（也适用于文章）。"
            try:
                return self._analyze(body, "飞书公开网页", title, item_id, url)
            except Exception:
                self.repository.update_material(item_id, "analysis_failed", "正文已取得，但分析失败；请重发正文。")
                raise
        return HELP

    def _analyze(self, text: str, source: str, title: str, item_id: str = "", url: str = "") -> str:
        if not 10 <= len(text.strip()) <= 30000:
            raise ValueError("正文/字幕需要 10 至 30000 字符；更长内容请分段。")
        analysis = self.pipeline.add_text_intelligence(source_name=source, title=title,
                                                      text=(f"来源链接：{url}\n用户提交/抓取时间：{datetime.now().isoformat(timespec='seconds')}\n\n" if url else "") + text)
        if item_id:
            self.repository.update_material(item_id, "analyzed", title, analysis["id"])
        summary = str(analysis.get("summary", "已完成结构化解析，请在情报中心查看。"))
        return f"情报 #{analysis['id']} 已解析入库{'；素材 ' + item_id if item_id else ''}。\n{summary}\n来源观点尚未独立验证；将在下一次盘前任务中纳入，现有冻结预案不自动改写。"
