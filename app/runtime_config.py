from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.settings import Settings


SecretName = Literal[
    "tushare_token", "alpha_vantage_api_key", "baidu_ocr_api_key",
    "baidu_ocr_secret_key", "llm_api_key", "feishu_app_secret",
]


class RuntimeConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tushare_token: str | None = Field(default=None, max_length=1000)
    tushare_base_url: str | None = Field(default=None, max_length=1000)
    tushare_points_tier: int | None = Field(default=None, ge=0, le=1000000)

    alpha_vantage_api_key: str | None = Field(default=None, max_length=1000)
    alpha_vantage_symbols: str | None = Field(default=None, max_length=500)

    ocr_mode: Literal["disabled", "baidu", "vision"] | None = None
    baidu_ocr_api_key: str | None = Field(default=None, max_length=1000)
    baidu_ocr_secret_key: str | None = Field(default=None, max_length=1000)

    llm_enabled: bool | None = None
    llm_base_url: str | None = Field(default=None, max_length=1000)
    llm_api_key: str | None = Field(default=None, max_length=4000)
    llm_model: str | None = Field(default=None, max_length=200)

    akshare_enabled: bool | None = None
    monitor_enabled: bool | None = None
    monitor_interval_seconds: int | None = Field(default=None, ge=20, le=3600)
    monitor_max_symbols: int | None = Field(default=None, ge=1, le=20)
    feishu_enabled: bool | None = None
    feishu_app_id: str | None = Field(default=None, max_length=100)
    feishu_app_secret: str | None = Field(default=None, max_length=1000)
    feishu_allowed_users: str | None = Field(default=None, max_length=1000)
    feishu_notify_chat_id: str | None = Field(default=None, max_length=100)
    clear_secrets: list[SecretName] = Field(default_factory=list)


SECRET_FIELDS: dict[str, str] = {
    "tushare_token": "TUSHARE_TOKEN",
    "alpha_vantage_api_key": "ALPHA_VANTAGE_API_KEY",
    "baidu_ocr_api_key": "BAIDU_OCR_API_KEY",
    "baidu_ocr_secret_key": "BAIDU_OCR_SECRET_KEY",
    "llm_api_key": "LLM_API_KEY",
    "feishu_app_secret": "FEISHU_APP_SECRET",
}

FIELD_ENV: dict[str, str] = {
    **SECRET_FIELDS,
    "tushare_base_url": "TUSHARE_BASE_URL",
    "tushare_points_tier": "TUSHARE_POINTS_TIER",
    "alpha_vantage_symbols": "ALPHA_VANTAGE_SYMBOLS",
    "ocr_mode": "OCR_MODE",
    "llm_enabled": "LLM_ENABLED",
    "llm_base_url": "LLM_BASE_URL",
    "llm_model": "LLM_MODEL",
    "akshare_enabled": "AKSHARE_ENABLED",
    "monitor_enabled": "MONITOR_ENABLED",
    "monitor_interval_seconds": "MONITOR_INTERVAL_SECONDS",
    "monitor_max_symbols": "MONITOR_MAX_SYMBOLS",
    "feishu_enabled": "FEISHU_ENABLED",
    "feishu_app_id": "FEISHU_APP_ID",
    "feishu_allowed_users": "FEISHU_ALLOWED_USERS",
    "feishu_notify_chat_id": "FEISHU_NOTIFY_CHAT_ID",
}


def _public_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.hostname:
        return ""
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def public_config(settings: Settings) -> dict[str, Any]:
    """Return configuration state without ever returning a stored secret."""
    return {
        "security": {
            "auth_enabled": settings.auth_enabled,
            "production": settings.app_env.lower() == "production",
            "secrets_never_returned": True,
        },
        "tushare": {
            "configured": bool(settings.tushare_token),
            "base_url": _public_url(settings.tushare_base_url),
            "points_tier": settings.tushare_points_tier,
        },
        "alpha_vantage": {
            "configured": bool(settings.alpha_vantage_api_key),
            "symbols": ",".join(settings.alpha_vantage_symbols),
        },
        "ocr": {
            "mode": settings.ocr_mode,
            "baidu_api_key_configured": bool(settings.baidu_ocr_api_key),
            "baidu_secret_configured": bool(settings.baidu_ocr_secret_key),
        },
        "llm": {
            "enabled": settings.llm_enabled,
            "configured": bool(settings.llm_enabled and settings.llm_api_key and settings.llm_model),
            "api_key_configured": bool(settings.llm_api_key),
            "base_url": _public_url(settings.llm_base_url),
            "model": settings.llm_model,
        },
        "akshare": {"enabled": settings.akshare_enabled},
        "monitor": {
            "enabled": settings.monitor_enabled,
            "interval_seconds": settings.monitor_interval_seconds,
            "max_symbols": settings.monitor_max_symbols,
        },
        "feishu": {
            "enabled": settings.feishu_enabled,
            "configured": settings.feishu_configured,
            "app_id": settings.feishu_app_id,
            "app_secret_configured": bool(settings.feishu_app_secret),
            "allowed_users": settings.feishu_allowed_users,
            "notify_chat_id": settings.feishu_notify_chat_id,
        },
    }


class RuntimeConfigStore:
    def __init__(self, env_path: Path):
        self.env_path = env_path

    def update(self, current: Settings, body: RuntimeConfigUpdate) -> Settings:
        values = body.model_dump(exclude_none=True, exclude={"clear_secrets"})
        for secret in SECRET_FIELDS:
            if secret in values and not str(values[secret]).strip():
                values.pop(secret)
        for secret in body.clear_secrets:
            values[secret] = ""

        for name in ("tushare_base_url", "llm_base_url"):
            if name in values:
                values[name] = self._validate_url(str(values[name]), name)
        for name, value in values.items():
            if isinstance(value, str) and ("\n" in value or "\r" in value):
                raise ValueError(f"{name} 不能包含换行。")

        for name, prefix in (("feishu_app_id", "cli_"), ("feishu_notify_chat_id", "oc_")):
            if values.get(name) and (not str(values[name]).strip().startswith(prefix) or not str(values[name]).strip().replace("_", "").isalnum()):
                raise ValueError(f"{name} 格式不正确，应以 {prefix} 开头。")
        if "feishu_allowed_users" in values:
            ids = [item.strip() for item in str(values["feishu_allowed_users"]).split(",") if item.strip()]
            if any(not item.startswith("ou_") or not item.replace("_", "").isalnum() for item in ids):
                raise ValueError("飞书白名单应填写 ou_ 开头的 open_id，多个使用英文逗号分隔。")
            values["feishu_allowed_users"] = ",".join(dict.fromkeys(ids))
        for name in ("feishu_app_id", "feishu_app_secret", "feishu_notify_chat_id"):
            if name in values:
                values[name] = str(values[name]).strip()

        env_updates = {FIELD_ENV[name]: self._env_value(value) for name, value in values.items()}
        self._write_env(env_updates)
        for key, value in env_updates.items():
            os.environ[key] = value

        settings_updates = dict(values)
        if "alpha_vantage_symbols" in settings_updates:
            settings_updates["alpha_vantage_symbols"] = tuple(
                item.strip() for item in str(settings_updates["alpha_vantage_symbols"]).split(",")
                if item.strip()
            )
        return replace(current, **settings_updates)

    @staticmethod
    def _validate_url(value: str, name: str) -> str:
        clean = value.strip().rstrip("/")
        parsed = urlparse(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{name} 必须是完整的 http/https 地址。")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"{name} 不能包含账号、密码、查询参数或片段；密钥请填入单独的 Key/Token 输入框。")
        return clean

    @staticmethod
    def _env_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).strip()

    def _write_env(self, updates: dict[str, str]) -> None:
        lines = self.env_path.read_text(encoding="utf-8").splitlines() if self.env_path.exists() else []
        remaining = dict(updates)
        written: set[str] = set()
        output: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    if key not in written:
                        output.append(f"{key}={updates[key]}")
                        written.add(key)
                        remaining.pop(key, None)
                    continue
            output.append(line)
        if remaining:
            if output and output[-1].strip():
                output.append("")
            output.append("# Saved from the server-side Web configuration panel")
            output.extend(f"{key}={value}" for key, value in remaining.items())
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.env_path.with_name(f".{self.env_path.name}.tmp")
        temp_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, self.env_path)


def build_config_router(
    store: RuntimeConfigStore,
    get_settings: Callable[[], Settings],
    apply_settings: Callable[[Settings], bool],
) -> APIRouter:
    router = APIRouter(prefix="/api/config", tags=["configuration"])

    def ensure_write_allowed(request: Request, current: Settings) -> None:
        if not current.auth_enabled:
            if current.app_env.lower() == "production":
                raise HTTPException(
                    status_code=403,
                    detail="生产环境必须先在服务器 .env 配置 APP_USERNAME 和 APP_PASSWORD，才能通过网页修改密钥。",
                )
            if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise HTTPException(
                    status_code=403,
                    detail="未启用登录认证时，只允许从本机地址修改密钥。",
                )
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.url.netloc:
            raise HTTPException(status_code=403, detail="拒绝跨站配置请求。")

    @router.get("")
    def read_config():
        return public_config(get_settings())

    @router.post("")
    def save_config(body: RuntimeConfigUpdate, request: Request):
        current = get_settings()
        ensure_write_allowed(request, current)
        try:
            updated = store.update(current, body)
            hot_reloaded = apply_settings(updated)
            return {"status": "ok", "hot_reloaded": hot_reloaded,
                    "config": public_config(updated)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
