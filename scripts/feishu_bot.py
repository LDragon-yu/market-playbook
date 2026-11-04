"""Run only one worker per database. Main app supervises it; no inbound port needed."""
from __future__ import annotations

import asyncio
import json
import threading
import time

from app.channel_commands import ChannelCommands, event_message
from app.db import Database
from app.services import ReviewPipeline
from app.services.portfolio_service import PortfolioService
from app.settings import settings
from app.sources.feishu import FeishuError, FeishuTransport


def main() -> None:
    if not settings.feishu_configured:
        return
    settings.ensure_directories()
    from app.worker_lock import acquire_worker_lock
    handle = acquire_worker_lock(settings.data_dir / "feishu-worker.lock")
    if handle is None:
        return
    try:
        run()
    finally:
        handle.close()


def run() -> None:
    db = Database(settings.db_path)
    db.initialize()
    pipeline = ReviewPipeline(settings, db)
    portfolio = PortfolioService(settings, db, pipeline)
    portfolio.initialize()
    commands = ChannelCommands(settings, db, pipeline, portfolio)
    repository = commands.repository
    repository.recover()
    transport = FeishuTransport(settings.feishu_app_id, settings.feishu_app_secret)
    # SDK maintains its own event loop; Python 3.14 no longer creates one implicitly.
    asyncio.set_event_loop(asyncio.new_event_loop())
    import lark_oapi as lark

    def received(event) -> None:
        message = event_message(json.loads(lark.JSON.marshal(event)))
        if message and (commands.allowed(message["user_id"]) or message["text"] in {"/身份", "身份"}):
            repository.accept(**message)

    def handle_requests() -> None:
        while True:
            try:
                item = repository.claim_inbox()
                if item:
                    if time.time() - item["received"] > 300:
                        reply = "该请求已等待超过 5 分钟，未执行。请重新提交并核对最新状态。"
                    else:
                        try:
                            reply = commands.dispatch(item["user_id"], item["chat_id"], item["text"])
                        except ValueError as exc:
                            reply = str(exc)
                        except Exception:
                            # Never echo upstream errors: they may contain provider URLs/tokens.
                            reply = "请求未完成：数据、模型或存储服务暂不可用。请在 Web 核对状态后重试；未执行交易。"
                    repository.enqueue(item["chat_id"], reply, dedupe_key="reply:" + item["message_id"], ttl=600)
                    repository.finish_inbox(item["message_id"])
                    continue
            except Exception:
                repository.heartbeat("processing_error", "请求处理异常，请检查磁盘与数据库；中断请求不会自动重放。")
            time.sleep(0.5)

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(received).build()
    client = lark.ws.Client(settings.feishu_app_id, settings.feishu_app_secret,
                            event_handler=handler, log_level=lark.LogLevel.ERROR)

    def deliver() -> None:
        while True:
            try:
                connected = getattr(client, "_conn", None) is not None
                repository.heartbeat("connected" if connected else "connecting",
                                     "长连接已建立；仍需订阅消息事件并发布应用。" if connected else "等待长连接；核对 App ID/Secret、网络和发布状态。")
                item = repository.claim_outbox()
                if item:
                    if item["expires"] <= time.time():
                        repository.failed(item, "提醒已过期，不再发送", retryable=False)
                    else:
                        try:
                            transport.send(item["chat_id"], item["text"], item["id"])
                            repository.delivered(item["id"])
                        except FeishuError as exc:
                            repository.failed(item, str(exc), retryable=exc.retryable)
                        except Exception:
                            repository.failed(item, "飞书传输异常", retryable=True)
            except Exception:
                pass
            time.sleep(1)

    threading.Thread(target=handle_requests, name="feishu-requests", daemon=True).start()
    threading.Thread(target=deliver, name="feishu-outbox", daemon=True).start()
    try:
        client.start()  # Official SDK owns reconnect and heartbeat.
    except Exception:
        repository.heartbeat("connection_failed", "飞书长连接失败；核对密钥、网络、应用发布和事件订阅。")


if __name__ == "__main__":
    main()
