from __future__ import annotations

import json
from typing import Any

from .http import request_json


ADVISOR_SYSTEM = """你是A股短线交易决策辅助模型。你只能基于输入中的结构化事实回答，禁止编造行情、消息或盘口。
你不自动下单，也不承诺收益。必须遵守T+1、仓位上限、空仓门槛、行情新鲜度和目标股可交易性。
强者已经涨停不等于仍可安全买入；信息不足时必须选择观察或维持原计划。
严格输出JSON，字段：summary, action, confidence, evidence, risks, conditions, invalidation, expires_seconds, unknowns。
action只能是：持有观察、停止加仓、取消买入、降低预期、若可卖则减仓、若可卖则退出、切换观察、保持空仓、信息不足。
evidence/risks/conditions/invalidation/unknowns都是字符串数组，confidence范围0到1。"""


QA_SYSTEM = """你是有证据约束的A股个股研究助手。只允许使用输入中的行情、持仓、板块、题材、计划和情报。
明确区分事实、推断和未知；禁止用训练记忆补充所谓最新行情。涉及操作时必须考虑T+1、可卖数量、行情时间、仓位上限和空仓策略。
严格输出JSON，字段：answer, conclusion, facts, interpretation, holding_view, no_position_view, conditions, invalidation, risks, unknowns。
除answer/conclusion/holding_view/no_position_view为字符串外，其余字段均为字符串数组。"""


class AdvisorLLM:
    def __init__(self, *, enabled: bool, base_url: str, api_key: str, model: str):
        self.enabled = enabled
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.api_key and self.model)

    def _call(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("大模型尚未配置。")
        response = request_json(
            f"{self.base_url}/chat/completions",
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "temperature": .1,
                "response_format": {"type": "json_object"},
            },
            timeout=45,
            retries=1,
        )
        raw = response["choices"][0]["message"]["content"]
        return json.loads(raw)

    def decide(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._call(ADVISOR_SYSTEM, context)

    def answer(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._call(QA_SYSTEM, context)
