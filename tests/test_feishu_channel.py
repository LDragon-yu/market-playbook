from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.channel_api import build_channel_router
from app.channel_commands import ChannelCommands, event_message, holding_payload
from app.db import Database
from app.engines.advisor import AdvisorEngine
from app.notifications import Notifications
from app.runtime_config import RuntimeConfigStore, RuntimeConfigUpdate, public_config
from app.services import ReviewPipeline
from app.services.portfolio_service import PortfolioService
from app.settings import settings
from app.sources.feishu import FeishuTransport
from app.sources.public_article import fetch_article, is_video_url, public_addresses, validate_url


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.current = replace(settings, data_dir=Path(self.tmp.name), username="", password="", app_env="development",
            tushare_token="", llm_enabled=False, llm_api_key="", monitor_enabled=False,
            feishu_enabled=True, feishu_app_id="cli_test", feishu_app_secret="never-return-this-secret",
            feishu_allowed_users="ou_owner", feishu_notify_chat_id="oc_destination")
        self.db = Database(self.current.db_path)
        self.db.initialize()
        self.pipeline = ReviewPipeline(self.current, self.db)
        self.portfolio = PortfolioService(self.current, self.db, self.pipeline)
        self.portfolio.initialize()
        self.portfolio.repository.upsert_profile({"ts_code": "000592.SZ", "name": "示例股票"})
        self.commands = ChannelCommands(self.current, self.db, self.pipeline, self.portfolio)
        self.repo = self.commands.repository
        self.buy = (date.today() - timedelta(days=2)).strftime("%Y%m%d")

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self, sellable: int = 1000) -> str:
        reply = self.commands.dispatch("ou_owner", "oc_private",
            f"/持仓 000592.SZ 总量=1000 可卖={sellable} 成本=10.5 买入={self.buy}")
        return re.search(r"/确认 ([a-f0-9]{10})", reply).group(1)

    def test_no_whitelist_no_data_and_identity_is_not_auto_binding(self):
        self.assertIn("尚未被授权", self.commands.dispatch("ou_stranger", "oc_private", "/持仓查看"))
        self.assertIn("ou_stranger", self.commands.dispatch("ou_stranger", "oc_private", "/身份"))
        self.assertFalse(self.commands.allowed("ou_stranger"))
        self.assertFalse(self.portfolio.repository.list_positions())

    def test_holding_confirmation_binds_user_chat_and_is_once_only(self):
        item_id = self.draft()
        self.assertFalse(self.portfolio.repository.list_positions())
        with self.assertRaises(ValueError):
            self.repo.take_draft(item_id, "ou_owner", "oc_other")
        with self.assertRaises(ValueError):
            self.repo.take_draft(item_id, "ou_other", "oc_private")
        reply = self.commands.dispatch("ou_owner", "oc_private", f"/确认 {item_id}")
        self.assertIn("已更新系统持仓", reply)
        self.assertEqual(len(self.portfolio.repository.list_positions()), 1)
        with self.assertRaises(ValueError):
            self.commands.dispatch("ou_owner", "oc_private", f"/确认 {item_id}")

    def test_expired_draft_does_not_modify_positions(self):
        item_id = self.draft()
        with self.db.connect() as c:
            c.execute("UPDATE channel_drafts SET expires=? WHERE id=?", (time.time() - 1, item_id))
        with self.assertRaises(ValueError):
            self.commands.dispatch("ou_owner", "oc_private", f"/确认 {item_id}")
        self.assertFalse(self.portfolio.repository.list_positions())

    def test_cancellation_does_not_modify_positions(self):
        item_id = self.draft()
        self.commands.dispatch("ou_owner", "oc_private", f"/取消 {item_id}")
        with self.assertRaises(ValueError):
            self.commands.dispatch("ou_owner", "oc_private", f"/确认 {item_id}")
        self.assertFalse(self.portfolio.repository.list_positions())

    def test_user_explicit_zero_sellable_is_preserved(self):
        item_id = self.draft(sellable=0)
        self.commands.dispatch("ou_owner", "oc_private", f"/确认 {item_id}")
        self.assertEqual(self.portfolio.repository.list_positions()[0]["sellable_qty"], 0)

    def test_replacement_preserves_thesis_and_detects_intervening_changes(self):
        self.portfolio.save_position({"ts_code": "000592.SZ", "total_qty": 2000, "sellable_qty": 2000,
            "cost_price": 9, "buy_date": self.buy, "thesis": "需要保留的逻辑"})
        item_id = self.draft()
        self.commands.dispatch("ou_owner", "oc_private", f"/确认 {item_id}")
        self.assertEqual(self.portfolio.repository.list_positions()[0]["thesis"], "需要保留的逻辑")
        item_id = self.draft()
        with self.db.connect() as c:
            c.execute("UPDATE positions SET updated_at='changed-after-draft',total_qty=3000")
        with self.assertRaisesRegex(ValueError, "已发生变化"):
            self.commands.dispatch("ou_owner", "oc_private", f"/确认 {item_id}")
        self.assertEqual(self.portfolio.repository.list_positions()[0]["total_qty"], 3000)

    def test_holding_parser_t1_and_invalid_numbers(self):
        today = date.today().strftime("%Y%m%d")
        for payload in (
            f"000592.SZ 总量=100 可卖=100 成本=10 买入={today}",
            f"000592.SZ 总量=-100 可卖=0 成本=10 买入={self.buy}",
            f"000592.SZ 总量=100 可卖=101 成本=10 买入={self.buy}",
            f"000592.SZ 总量=100 可卖=0 成本=nan 买入={self.buy}",
            f"000592 总量=100 可卖=0 成本=10 买入={self.buy}",
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                holding_payload(payload)

    def test_holding_confirmation_detects_same_second_write_atomically(self):
        self.portfolio.save_position({"ts_code": "000592.SZ", "total_qty": 1000, "sellable_qty": 1000,
                                      "cost_price": 9, "buy_date": self.buy})
        item_id = self.draft()
        # Simulate an update occurring between the command's check and repository write.
        original = self.portfolio.save_position
        def changed(value):
            with self.db.connect() as c:
                c.execute("UPDATE positions SET cost_price=12")
            return original(value)
        self.portfolio.save_position = changed
        with self.assertRaisesRegex(ValueError, "已发生变化"):
            self.commands.dispatch("ou_owner", "oc_private", f"/确认 {item_id}")
        self.assertEqual(self.portfolio.repository.list_positions()[0]["cost_price"], 12)

    def test_duplicate_inbox_and_crash_recovery_do_not_replay_mutations(self):
        self.assertTrue(self.repo.accept("om_1", "ou_owner", "oc_private", "/帮助"))
        self.assertFalse(self.repo.accept("om_1", "ou_owner", "oc_private", "/帮助"))
        self.assertEqual(self.repo.claim_inbox()["message_id"], "om_1")
        self.repo.recover()
        self.assertIsNone(self.repo.claim_inbox())
        self.assertEqual(self.repo.status()["interrupted_requests"], 1)

    def test_outbox_deduplication_expiry_and_retries(self):
        first = self.repo.enqueue("oc_x", "test", dedupe_key="same", ttl=90)
        self.assertEqual(first, self.repo.enqueue("oc_x", "changed", dedupe_key="same"))
        item = self.repo.claim_outbox()
        self.repo.failed(item, "safe failure", retryable=True)
        self.assertIsNone(self.repo.claim_outbox())
        with self.db.connect() as c:
            c.execute("UPDATE channel_outbox SET expires=?,next_attempt=0", (time.time() - 1,))
        self.assertIsNone(self.repo.claim_outbox())
        self.assertEqual(self.repo.status()["outbox_counts"]["expired"], 1)

    def test_delivery_status_and_permanent_failures(self):
        one = self.repo.enqueue("oc_x", "first")
        self.repo.claim_outbox()
        self.repo.delivered(one)
        self.repo.enqueue("oc_x", "second")
        self.repo.failed(self.repo.claim_outbox(), "permission error", retryable=False)
        self.assertIsNone(self.repo.claim_outbox())
        self.assertEqual(self.repo.status()["outbox_counts"], {"sent": 1, "failed": 1})

    def test_video_link_only_records_pending_until_user_supplies_text(self):
        with patch("app.channel_commands.fetch_article") as fetch:
            reply = self.commands.dispatch("ou_owner", "oc_private", "/材料 https://www.bilibili.com/video/BVexample")
            fetch.assert_not_called()
        item_id = self.repo.status()["materials"][0]["id"]
        self.assertIn("未获取字幕", reply)
        self.assertFalse(self.db.list_intelligence())
        with self.assertRaises(ValueError):
            self.commands.dispatch("ou_owner", "oc_other", f"/字幕 {item_id} 足够长度的用户提供正文示例")
        self.commands.dispatch("ou_owner", "oc_private", f"/字幕 {item_id}\n用户提供的字幕：医药板块正在分歧，这只是博主观点，不是事实证明。")
        self.assertEqual(self.repo.status()["materials"][0]["status"], "analyzed")
        self.assertEqual(len(self.db.list_intelligence()), 1)

    def test_public_article_and_failed_fetch_have_truthful_states(self):
        with patch("app.channel_commands.fetch_article", return_value=("文章标题", "医药板块消息催化正在发酵，这是一段来自博主的公开文案。")):
            self.commands.dispatch("ou_owner", "oc_private", "https://example.com/article")
        self.assertEqual(self.repo.status()["materials"][0]["status"], "analyzed")
        with patch("app.channel_commands.fetch_article", side_effect=ValueError("登录页面不可读取")):
            self.commands.dispatch("ou_owner", "oc_private", "/材料 https://example.com/private")
        self.assertEqual(self.repo.status()["materials"][0]["status"], "pending_text")
        self.assertEqual(len(self.db.list_intelligence()), 1)

    def test_question_uses_existing_service_and_has_asof(self):
        self.portfolio.ask_stock = Mock(return_value={"answer": "条件研究，不自动下单", "data_as_of": "2026-10-02 10:00:00",
                                                    "analysis_mode": "rule_fallback", "risks": ["行情缺少盘口"]})
        reply = self.commands.dispatch("ou_owner", "oc_private", "/问\n000592.SZ\n有哪些风险？")
        self.portfolio.ask_stock.assert_called_once_with("000592.SZ", "有哪些风险？")
        self.assertIn("数据截至", reply)
        self.assertIn("非交易指令", reply)

    def test_secret_roundtrip_never_returns_secret(self):
        store = RuntimeConfigStore(Path(self.tmp.name) / ".env")
        with patch.dict("os.environ", {}, clear=False):
            updated = store.update(self.current, RuntimeConfigUpdate(feishu_app_secret="changed-private-secret", feishu_allowed_users="ou_owner, ou_second"))
        self.assertEqual(updated.feishu_allowed_users, "ou_owner,ou_second")
        self.assertNotIn("changed-private-secret", json.dumps(public_config(updated)))
        self.assertTrue(public_config(updated)["feishu"]["app_secret_configured"])
        with self.assertRaises(ValueError):
            store.update(updated, RuntimeConfigUpdate(feishu_allowed_users="unverified_user"))

    def test_push_endpoint_local_security_and_reusable_dedup(self):
        worker = Mock()
        worker.status.return_value = {"process_alive": True}
        app = FastAPI()
        app.include_router(build_channel_router(lambda: self.current, self.db, worker))
        body = {"source": "another-agent", "title": "完成", "body": "任务摘要", "dedupe_key": "task-1"}
        remote = TestClient(app, base_url="http://remote.example")
        self.assertEqual(remote.post("/api/notifications/send", json=body).status_code, 403)
        local = TestClient(app, base_url="http://127.0.0.1")
        one = local.post("/api/notifications/send", json=body).json()
        two = local.post("/api/notifications/send", json=body).json()
        self.assertEqual(one["status"], "queued")
        self.assertEqual(one["id"], two["id"])
        self.assertEqual(local.post("/api/notifications/send", json=body, headers={"Origin": "https://evil.example"}).status_code, 403)
        worker.status.return_value = {"process_alive": False}
        self.assertEqual(local.post("/api/channel/test").status_code, 409)

    def test_disabled_notification_does_not_enqueue(self):
        notifier = Notifications(replace(self.current, feishu_enabled=False), self.db)
        self.assertEqual(notifier.send("test", "body")["status"], "disabled")
        self.assertIsNone(self.repo.claim_outbox())


class TransportAndURLTests(unittest.TestCase):
    def test_transport_fixed_host_token_cache_and_uuid(self):
        transport = FeishuTransport("cli_test", "secret")
        transport._request = Mock(side_effect=[{"code": 0, "tenant_access_token": "cached-token", "expire": 7200}, {"code": 0}, {"code": 0}])
        transport.send("oc_x", "hello", "stable-uuid")
        transport.send("oc_x", "hello", "stable-uuid")
        self.assertEqual(transport._request.call_count, 3)
        self.assertEqual(transport._request.call_args.kwargs["token"], "cached-token")
        self.assertEqual(transport._request.call_args.args[1]["uuid"], "stable-uuid")

    def test_url_validation_rejects_credentials_non_web_and_ports(self):
        for url in ("file:///etc/passwd", "https://user:secret@example.com", "http://example.com:8000", "https://example.com/\nattack"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        self.assertTrue(is_video_url("https://b23.tv/example"))
        self.assertFalse(is_video_url("https://example.com/article"))

    def test_dns_rejects_any_private_or_metadata_answer(self):
        for address in ("127.0.0.1", "169.254.169.254", "10.1.2.3", "::1", "::ffff:127.0.0.1"):
            with self.subTest(address=address), patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", (address, 80))]), self.assertRaises(ValueError):
                public_addresses("example.com", 80)
        with patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 443))]):
            self.assertEqual(public_addresses("example.com", 443), ["8.8.8.8"])

    def test_redirect_to_private_host_revalidated(self):
        response = Mock(status=302)
        response.getheader.return_value = "http://127.0.0.1/metadata"
        connection = Mock()
        connection.getresponse.return_value = response
        with patch("app.sources.public_article.public_addresses", side_effect=[["8.8.8.8"], ValueError("拒绝内网")]) as resolve, patch("app.sources.public_article._PinnedHTTPS", return_value=connection), self.assertRaisesRegex(ValueError, "拒绝内网"):
            fetch_article("https://example.com/redirect")
        self.assertEqual(resolve.call_count, 2)
        connection.close.assert_called_once()

    def test_event_text_post_and_bot_loop_guard(self):
        event = {"event": {"sender": {"sender_type": "user", "sender_id": {"open_id": "ou_owner"}},
            "message": {"message_id": "om_1", "chat_id": "oc_x", "chat_type": "p2p", "message_type": "text",
                        "content": json.dumps({"text": "/帮助"})}}}
        self.assertEqual(event_message(event)["text"], "/帮助")
        event["event"]["message"]["chat_type"] = "group"
        self.assertIsNone(event_message(event))
        event["event"]["message"]["mentions"] = [{"key": "@_user_1"}]
        self.assertIsNotNone(event_message(event))
        event["event"]["sender"]["sender_type"] = "app"
        self.assertIsNone(event_message(event))

    def test_stale_quote_never_calls_llm_question(self):
        llm = Mock(configured=True)
        answer = AdvisorEngine(llm).answer_question({"latest_features": {"stale": True}, "profile": {}})
        llm.answer.assert_not_called()
        self.assertIn("不能据此给出当前操作", answer["answer"])


if __name__ == "__main__":
    unittest.main()
