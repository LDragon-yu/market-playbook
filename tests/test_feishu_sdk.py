from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from app.channel_commands import event_message
from app.worker_lock import acquire_worker_lock


class WorkerLockTests(unittest.TestCase):
    def test_lock_is_exclusive_and_released_on_close(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.lock"
            first = acquire_worker_lock(path)
            self.assertIsNotNone(first)
            self.assertIsNone(acquire_worker_lock(path))
            first.close()
            next_handle = acquire_worker_lock(path)
            self.assertIsNotNone(next_handle)
            next_handle.close()


@unittest.skipUnless(importlib.util.find_spec("lark_oapi"), "optional Feishu SDK not installed")
class SDKTests(unittest.TestCase):
    def test_official_models_marshal_and_handler_constructor(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
            payload = {"schema": "2.0", "header": {"event_id": "evt-test", "event_type": "im.message.receive_v1"},
                "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": "ou_owner"}},
                    "message": {"message_id": "om_test", "chat_id": "oc_private", "chat_type": "p2p",
                                "message_type": "text", "content": json.dumps({"text": "/帮助"})}}}
            message = event_message(json.loads(lark.JSON.marshal(P2ImMessageReceiveV1(payload))))
            self.assertEqual(message["text"], "/帮助")
            handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(lambda data: None).build()
            client = lark.ws.Client("cli_test", "not-a-real-secret", event_handler=handler, log_level=lark.LogLevel.ERROR)
            self.assertIsNone(client._conn)  # No connection or external message is initiated.
        finally:
            pending = list(asyncio.all_tasks(loop))
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            asyncio.set_event_loop(None)


if __name__ == "__main__":
    unittest.main()
