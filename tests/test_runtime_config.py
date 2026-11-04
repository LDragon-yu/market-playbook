from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.runtime_config import (
    RuntimeConfigStore, RuntimeConfigUpdate, build_config_router, public_config,
)
from app.settings import settings


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_path = Path(self.tmp.name) / ".env"
        self.env_path.write_text(
            "TUSHARE_TOKEN=old-one\nTUSHARE_TOKEN=old-duplicate\nLLM_API_KEY=keep-me\n",
            encoding="utf-8",
        )
        self.store = RuntimeConfigStore(self.env_path)
        self.current = replace(
            settings,
            tushare_token="old-one",
            llm_api_key="keep-me",
            monitor_enabled=False,
            monitor_interval_seconds=60,
            monitor_max_symbols=18,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_secret_is_written_but_never_returned(self):
        updated = self.store.update(self.current, RuntimeConfigUpdate(
            tushare_token="new-private-token",
            monitor_enabled=True,
            monitor_interval_seconds=80,
        ))
        stored = self.env_path.read_text(encoding="utf-8")
        self.assertEqual(stored.count("TUSHARE_TOKEN="), 1)
        self.assertIn("TUSHARE_TOKEN=new-private-token", stored)
        public = public_config(updated)
        self.assertTrue(public["tushare"]["configured"])
        self.assertNotIn("new-private-token", str(public))
        self.assertTrue(updated.monitor_enabled)

    def test_blank_secret_preserves_and_explicit_clear_removes(self):
        preserved = self.store.update(self.current, RuntimeConfigUpdate(llm_api_key=""))
        self.assertEqual(preserved.llm_api_key, "keep-me")
        cleared = self.store.update(preserved, RuntimeConfigUpdate(clear_secrets=["llm_api_key"]))
        self.assertEqual(cleared.llm_api_key, "")
        self.assertIn("LLM_API_KEY=\n", self.env_path.read_text(encoding="utf-8"))

    def test_invalid_base_url_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update(self.current, RuntimeConfigUpdate(llm_base_url="not-a-url"))
        with self.assertRaises(ValueError):
            self.store.update(self.current, RuntimeConfigUpdate(
                llm_base_url="https://example.com/v1?token=must-not-be-here"
            ))

    def test_public_url_removes_embedded_credentials_and_query(self):
        unsafe = replace(
            self.current,
            llm_base_url="https://user:password@example.com/v1?token=hidden#part",
        )
        payload = public_config(unsafe)
        self.assertEqual(payload["llm"]["base_url"], "https://example.com/v1")
        self.assertNotIn("password", str(payload))
        self.assertNotIn("hidden", str(payload))


    def test_unauthenticated_write_is_localhost_only(self):
        env_path = Path(self.tmp.name) / "web.env"
        store = RuntimeConfigStore(env_path)
        state = {"settings": replace(
            self.current, app_env="development", username="", password=""
        )}

        def apply(updated):
            state["settings"] = updated
            return True

        app = FastAPI()
        app.include_router(build_config_router(store, lambda: state["settings"], apply))
        remote = TestClient(app, base_url="http://remote.example")
        self.assertEqual(remote.post("/api/config", json={"monitor_enabled": True}).status_code, 403)
        self.assertFalse(env_path.exists())

        local = TestClient(app, base_url="http://127.0.0.1")
        response = local.post("/api/config", json={"monitor_enabled": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(state["settings"].monitor_enabled)

if __name__ == "__main__":
    unittest.main()
