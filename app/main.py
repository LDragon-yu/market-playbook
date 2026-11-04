from __future__ import annotations

import base64
import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db import Database
from app.services import ReviewPipeline
from app.services.portfolio_service import PortfolioService
from app.portfolio_api import build_portfolio_router
from app.runtime_config import RuntimeConfigStore, build_config_router
from app.monitor_worker import MonitorWorker
from app.feishu_worker import FeishuWorker
from app.channel_api import build_channel_router
from app.settings import ROOT_DIR, RUNTIME_ENV_PATH, settings


db = Database(settings.db_path)
pipeline = ReviewPipeline(settings, db)
portfolio = PortfolioService(settings, db, pipeline)
worker = MonitorWorker(portfolio)
feishu_worker = FeishuWorker(settings, db)
config_store = RuntimeConfigStore(RUNTIME_ENV_PATH)
WEB_DIR = ROOT_DIR / "app" / "web"


def apply_runtime_settings(updated_settings) -> bool:
    global settings
    if not worker.prepare_reconfigure():
        return False
    settings = updated_settings
    pipeline.__init__(settings, db)
    portfolio.__init__(settings, db, pipeline)
    portfolio.initialize()
    app.title = settings.app_name
    feishu_worker.reconfigure(settings)
    return worker.reconfigure(portfolio)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.ensure_directories()
    db.initialize()
    portfolio.initialize()
    pipeline.seed_demo()
    worker.start()
    feishu_worker.start()
    try:
        yield
    finally:
        worker.stop()
        feishu_worker.stop()


app = FastAPI(title=settings.app_name, version="0.3.1", lifespan=lifespan)
app.include_router(build_portfolio_router(portfolio))
app.include_router(build_config_router(config_store, lambda: settings, apply_runtime_settings))
app.include_router(build_channel_router(lambda: settings, db, feishu_worker))


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not settings.auth_enabled or request.url.path == "/health":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    valid = False
    if header.startswith("Basic "):
        try:
            supplied = base64.b64decode(header[6:]).decode("utf-8")
            valid = hmac.compare_digest(supplied, f"{settings.username}:{settings.password}")
        except (ValueError, UnicodeDecodeError):
            pass
    if not valid:
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Market Review"'})
    return await call_next(request)


class JobRequest(BaseModel):
    date: str | None = None
    demo: bool = False


class TextIntelligenceRequest(BaseModel):
    source_name: str = Field(default="手工录入", max_length=100)
    title: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=100000)


def api_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400 if isinstance(exc, (ValueError, RuntimeError)) else 500,
                         detail=str(exc))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/dashboard")
def dashboard(date: str | None = None):
    try:
        payload = portfolio.dashboard_payload()
        payload["worker"] = worker.status()
        return {**pipeline.dashboard(date), "portfolio": payload}
    except Exception as exc:
        raise api_error(exc) from exc


@app.get("/api/status")
def status():
    return pipeline.config_status()


@app.post("/api/jobs/post-market")
def post_market(body: JobRequest):
    try:
        review = pipeline.run_post_market(body.date, demo=body.demo)
        return portfolio.after_review(review, sync_profiles=not body.demo)
    except Exception as exc:
        raise api_error(exc) from exc


@app.post("/api/jobs/pre-market")
def pre_market(body: JobRequest):
    try:
        return portfolio.after_plan(pipeline.run_pre_market(body.date, demo=body.demo))
    except Exception as exc:
        raise api_error(exc) from exc


@app.post("/api/intelligence/text")
def add_text(body: TextIntelligenceRequest):
    try:
        return pipeline.add_text_intelligence(source_name=body.source_name, title=body.title, text=body.text)
    except Exception as exc:
        raise api_error(exc) from exc


@app.post("/api/intelligence/image")
async def add_image(source_name: str = Form("截图导入"), title: str = Form(""),
                    image: UploadFile = File(...)):
    try:
        content = await image.read(15 * 1024 * 1024 + 1)
        return pipeline.add_image_intelligence(source_name=source_name, title=title,
            filename=image.filename or "upload", mime_type=image.content_type or "", image_bytes=content)
    except Exception as exc:
        raise api_error(exc) from exc


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
