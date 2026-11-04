from __future__ import annotations

import base64
import json
from typing import Any

from .http import request_json


SYSTEM_PROMPT = """你是A股盘前情报整理助手。你的工作是提取可核验事实，而不是推荐交易。
严格输出JSON对象，字段为 summary, sectors, tickers, catalysts, risks, stance, horizon, claims。
sectors/tickers/catalysts/risks/claims均为字符串数组；stance只能是positive/negative/neutral/mixed；
horizon只能是intraday/short/medium/unknown。明确区分事实、作者观点与未经证实的推测。"""


class LLMSource:
    def __init__(self, *, enabled: bool, base_url: str, api_key: str, model: str):
        self.enabled = enabled
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.api_key and self.model)

    def _chat(self, content: str | list[dict[str, Any]]) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("LLM is not configured")
        response = request_json(
            f"{self.base_url}/chat/completions",
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=90,
            retries=1,
        )
        raw = response["choices"][0]["message"]["content"]
        return json.loads(raw)

    def summarize(self, text: str) -> dict[str, Any]:
        return self._chat(f"请分析以下盘前信息：\n\n{text[:60000]}")

    def analyze_image(self, image_bytes: bytes, mime_type: str) -> tuple[str, dict[str, Any]]:
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        result = self._chat(
            [
                {
                    "type": "text",
                    "text": "识别图片中的文字并分析。summary中先给出完整但简洁的文字摘要。",
                },
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        )
        return str(result.get("summary", "")), result

