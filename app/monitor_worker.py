from __future__ import annotations

import threading
from datetime import datetime, time

from app.services.portfolio_service import PortfolioService


class MonitorWorker:
    """Small in-process scheduler for a single-server MVP deployment."""

    def __init__(self, service: PortfolioService):
        self.service = service
        self.enabled = service.settings.monitor_enabled
        self.interval = service.settings.monitor_interval_seconds
        self.max_symbols = service.settings.monitor_max_symbols
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""
        self.last_run = ""
        self.waiting_reason = ""

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, name="market-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def prepare_reconfigure(self) -> bool:
        self.stop()
        return not (self._thread and self._thread.is_alive())

    def reconfigure(self, service: PortfolioService) -> bool:
        if not self.prepare_reconfigure():
            return False
        self.service = service
        self.enabled = service.settings.monitor_enabled
        self.interval = service.settings.monitor_interval_seconds
        self.max_symbols = service.settings.monitor_max_symbols
        self._stop = threading.Event()
        self._thread = None
        self.start()
        return True

    def status(self) -> dict[str, object]:
        return {"enabled": self.enabled, "interval_seconds": self.interval,
                "max_symbols": self.max_symbols, "last_run": self.last_run,
                "last_error": self.last_error, "waiting_reason": self.waiting_reason}

    def _run(self) -> None:
        while not self._stop.is_set():
            now = datetime.now()
            if self._is_session(now):
                plan = self.service.db.get_plan()
                target = str((plan or {}).get("target_date", ""))
                if target != now.strftime("%Y%m%d"):
                    self.waiting_reason = "今日盘前预案尚未生成，自动监控暂停"
                else:
                    self.waiting_reason = ""
                    try:
                        self.service.run_monitor_cycle(max_symbols=self.max_symbols)
                        self.last_run, self.last_error = now.isoformat(timespec="seconds"), ""
                    except Exception as exc:
                        self.last_error = str(exc)[:500]
            self._stop.wait(self.interval)

    @staticmethod
    def _is_session(now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        current = now.time()
        return time(9, 25) <= current <= time(11, 30) or time(13, 0) <= current <= time(14, 56)
