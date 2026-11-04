from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.notifications import Notifications


class NotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=6000)
    source: str = Field(default="外部 Agent", min_length=1, max_length=60)
    dedupe_key: str = Field(default="", max_length=200)
    ttl_seconds: int = Field(default=3600, ge=30, le=86400)


def build_channel_router(get_settings, db, worker) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["feishu"])

    def protect(request: Request) -> None:
        current = get_settings()
        if not current.auth_enabled and (current.app_env.lower() == "production" or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}):
            raise HTTPException(403, "未启用登录认证时，通知接口仅允许本机开发访问。")
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.url.netloc:
            raise HTTPException(403, "拒绝跨站通知请求。")

    @router.get("/channel/status")
    def status(request: Request):
        protect(request)
        return worker.status()

    @router.post("/channel/test")
    def test(request: Request):
        protect(request)
        if not worker.status().get("process_alive"):
            raise HTTPException(409, "机器人接收/发送进程未运行。请先保存配置、安装 SDK 并重启服务。")
        return Notifications(get_settings(), db).send("双向通道测试", "测试通知：收到后可回复 /帮助 或 /身份。", ttl=300)

    @router.post("/notifications/send")
    def send(body: NotificationRequest, request: Request):
        protect(request)
        if not worker.status().get("process_alive"):
            raise HTTPException(409, "飞书进程未运行，通知无法及时发送。")
        return Notifications(get_settings(), db).send(body.title, body.body, source=body.source,
            dedupe_key=("external:" + body.source + ":" + body.dedupe_key) if body.dedupe_key else "",
            ttl=body.ttl_seconds)

    return router
