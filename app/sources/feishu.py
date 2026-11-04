"""Feishu transport; fixed API host and redacted errors, never arbitrary endpoints."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class FeishuError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class FeishuTransport:
    HOST = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id: str, app_secret: str):
        self.app_id, self.app_secret = app_id, app_secret
        self._token = ""
        self._expires = 0.0

    def _request(self, path: str, payload: dict, *, token: str = "") -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.HOST + path, json.dumps(payload).encode(), headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                data = json.loads(response.read(1024 * 1024))
        except urllib.error.HTTPError as exc:
            # Do not print a response body, request URL or credentials.
            raise FeishuError(f"飞书 HTTP {exc.code}", exc.code == 429 or exc.code >= 500) from None
        except (OSError, ValueError):
            raise FeishuError("飞书网络连接失败或响应格式异常", True) from None
        code = data.get("code", -1)
        if code != 0:
            if code in {99991663, 99991668}:
                self._expires = 0
            raise FeishuError(f"飞书错误码 {code}；请核对应用权限、发布状态及会话 ID",
                              code in {99991400, 99991663, 99991668})
        return data

    def token(self) -> str:
        if time.time() >= self._expires:
            data = self._request("/auth/v3/tenant_access_token/internal",
                                 {"app_id": self.app_id, "app_secret": self.app_secret})
            self._token = str(data["tenant_access_token"])
            self._expires = time.time() + max(1, int(data.get("expire", 7200)) - 120)
        return self._token

    def send(self, chat_id: str, text: str, message_uuid: str) -> None:
        self._request("/im/v1/messages?receive_id_type=chat_id", {
            "receive_id": chat_id, "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False), "uuid": message_uuid,
        }, token=self.token())
