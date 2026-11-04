"""Supervise the official SDK in its own process, isolated from FastAPI's loop."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import time
from typing import Any

from app.channel_db import ChannelRepository
from app.settings import ROOT_DIR


class FeishuWorker:
    def __init__(self, settings: Any, db: Any):
        self.settings, self.db = settings, db
        self.process: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._restart_after = 0.0

    def start(self) -> None:
        self.repository = ChannelRepository(self.db)
        if not self.settings.feishu_configured:
            return
        if importlib.util.find_spec("lark_oapi") is None:
            self.repository.heartbeat("missing_sdk", "请安装 requirements-feishu.txt 并重启服务。")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, name="feishu-supervisor", daemon=True)
        self._thread.start()

    def _supervise(self) -> None:
        while not self._stop.is_set():
            if self.process is None or self.process.poll() is not None:
                if time.time() >= self._restart_after:
                    child_env = dict(os.environ)
                    child_env.update({"FEISHU_ENABLED": "true", "FEISHU_APP_ID": self.settings.feishu_app_id,
                                      "FEISHU_APP_SECRET": self.settings.feishu_app_secret,
                                      "FEISHU_ALLOWED_USERS": self.settings.feishu_allowed_users,
                                      "FEISHU_NOTIFY_CHAT_ID": self.settings.feishu_notify_chat_id,
                                      "DATA_DIR": str(self.settings.data_dir), "PYTHONUNBUFFERED": "1"})
                    try:
                        self.process = subprocess.Popen([sys.executable, "-m", "scripts.feishu_bot"], cwd=ROOT_DIR,
                            env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        self.repository.heartbeat("starting", "正在连接飞书长连接；请确认事件订阅及应用发布。")
                    except OSError:
                        self.repository.heartbeat("start_failed", "子进程启动失败，请检查 Python 环境。")
                    self._restart_after = time.time() + 60
            self._stop.wait(2)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process = None

    def reconfigure(self, settings: Any) -> None:
        self.stop()
        self.settings = settings
        self.start()

    def status(self) -> dict[str, Any]:
        payload = ChannelRepository(self.db).status()
        heartbeat = payload.get("worker") or {}
        alive = bool(self.process and self.process.poll() is None)
        payload.update({"enabled": self.settings.feishu_enabled, "configured": self.settings.feishu_configured,
                        "process_alive": alive, "heartbeat_fresh": alive and time.time() - heartbeat.get("heartbeat", 0) < 30,
                        "notification_destination_configured": bool(self.settings.feishu_notify_chat_id),
                        "authorized_users": len([s for s in self.settings.feishu_allowed_users.split(",") if s.strip()])})
        return payload
