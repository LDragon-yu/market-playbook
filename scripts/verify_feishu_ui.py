"""Offline UI smoke test: isolated demo database, disabled providers, no sends."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    directory = Path(tempfile.mkdtemp(prefix="jianshi-feishu-ui-"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = dict(os.environ)
    environment.update({"DATA_DIR": str(directory / "data"), "APP_ENV": "development", "APP_USERNAME": "", "APP_PASSWORD": "",
                        "RUNTIME_ENV_PATH": str(directory / "runtime.env"),
                        "TUSHARE_TOKEN": "", "LLM_ENABLED": "false", "LLM_API_KEY": "", "ALPHA_VANTAGE_API_KEY": "",
                        "AKSHARE_ENABLED": "false", "MONITOR_ENABLED": "false", "OCR_MODE": "disabled",
                        "FEISHU_ENABLED": "false", "FEISHU_APP_ID": "", "FEISHU_APP_SECRET": ""})
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                               cwd=root, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(base + "/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Isolated app failed to start")
        for endpoint in ("/api/config", "/api/dashboard", "/api/channel/status"):
            with urllib.request.urlopen(base + endpoint, timeout=10) as response:
                data = json.load(response)
                if endpoint == "/api/config":
                    assert "feishu" in data and not data["feishu"]["enabled"]
                if endpoint == "/api/channel/status":
                    assert not data["process_alive"]
        browser = next((Path(p) for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                                          r"C:\Program Files\Google\Chrome\Application\chrome.exe") if Path(p).exists()), None)
        result = {"api_smoke": "passed", "artifacts": str(directory), "browser_smoke": "not_available"}
        if browser:
            screenshot = directory / "feishu-config.png"
            completed = subprocess.run([str(browser), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                f"--user-data-dir={directory / 'browser'}", "--window-size=1440,1800", "--virtual-time-budget=7000",
                "--dump-dom", f"--screenshot={screenshot}", base + "/?view=system&config=open"],
                capture_output=True, timeout=35, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            dom = completed.stdout.decode("utf-8", errors="replace")
            assert completed.returncode == 0, "Browser test process failed"
            for marker in ('name="feishu_app_id"', 'name="feishu_app_secret"', 'name="feishu_allowed_users"', 'id="channel-status"'):
                assert marker in dom, f"Expected UI element missing: {marker}"
            assert screenshot.exists(), "Screenshot not captured"
            result.update({"browser_smoke": "passed", "screenshot": str(screenshot)})
        print(json.dumps(result, ensure_ascii=False))
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


if __name__ == "__main__":
    main()
