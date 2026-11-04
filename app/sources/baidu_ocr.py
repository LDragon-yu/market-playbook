from __future__ import annotations

import base64
import time
from typing import Any
from urllib.parse import urlencode

from .http import request_json


class BaiduOCRSource:
    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self._token: str = ""
        self._token_expires_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        url = "https://aip.baidubce.com/oauth/2.0/token?" + urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self.secret_key,
            }
        )
        response = request_json(url, method="POST")
        token = response.get("access_token")
        if not token:
            raise RuntimeError(f"Baidu OCR auth failed: {response}")
        self._token = str(token)
        self._token_expires_at = time.time() + int(response.get("expires_in", 2592000)) - 300
        return self._token

    def extract(self, image_bytes: bytes) -> tuple[str, dict[str, Any]]:
        if not self.configured:
            raise RuntimeError("Baidu OCR credentials are not configured")
        token = self._access_token()
        endpoint = f"https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic?access_token={token}"
        response = request_json(
            endpoint,
            method="POST",
            form={
                "image": base64.b64encode(image_bytes).decode("ascii"),
                "language_type": "CHN_ENG",
                "detect_direction": "true",
                "paragraph": "true",
            },
        )
        if "error_code" in response:
            raise RuntimeError(f"Baidu OCR failed: {response}")
        words = [str(item.get("words", "")).strip() for item in response.get("words_result", [])]
        return "\n".join(word for word in words if word), response

