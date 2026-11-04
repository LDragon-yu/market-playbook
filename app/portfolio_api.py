from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.portfolio_service import PortfolioService


class PositionRequest(BaseModel):
    id: int | None = None
    ts_code: str
    name: str = ""
    total_qty: float = Field(default=0, ge=0)
    sellable_qty: float = Field(default=0, ge=0)
    cost_price: float = Field(default=0, ge=0)
    buy_date: str = ""
    strategy_mode: str = "comprehensive"
    thesis: str = ""
    invalidation: str = ""


class MonitorRequest(BaseModel):
    ts_code: str
    name: str = ""
    role: str = "manual"
    sector: str = ""
    priority: int = Field(default=70, ge=1, le=100)
    peer_code: str = ""
    trade_date: str | None = None


class JobRequest(BaseModel):
    date: str | None = None
    demo: bool = False


class StockQuestionRequest(BaseModel):
    ts_code: str
    question: str = Field(min_length=2, max_length=2000)


def build_portfolio_router(service: PortfolioService) -> APIRouter:
    router = APIRouter(prefix="/api")

    def fail(exc: Exception) -> HTTPException:
        return HTTPException(status_code=400 if isinstance(exc, (ValueError, RuntimeError)) else 500,
                             detail=str(exc))

    @router.post("/positions")
    def save_position(body: PositionRequest):
        try:
            return service.save_position(body.model_dump())
        except Exception as exc:
            raise fail(exc) from exc

    @router.delete("/positions/{position_id}")
    def close_position(position_id: int):
        try:
            service.close_position(position_id)
            return {"status": "ok"}
        except Exception as exc:
            raise fail(exc) from exc

    @router.post("/monitors")
    def add_monitor(body: MonitorRequest):
        try:
            return service.add_manual_monitor(body.model_dump())
        except Exception as exc:
            raise fail(exc) from exc

    @router.post("/monitor/cycle")
    def monitor_cycle(body: JobRequest):
        try:
            return service.run_monitor_cycle(body.date)
        except Exception as exc:
            raise fail(exc) from exc

    @router.post("/stocks/ask")
    def ask_stock(body: StockQuestionRequest):
        try:
            return service.ask_stock(body.ts_code, body.question)
        except Exception as exc:
            raise fail(exc) from exc

    @router.post("/jobs/enrich-review")
    def enrich_review():
        try:
            review = service.db.get_review()
            if not review:
                raise RuntimeError("尚无盘后复盘。")
            return service.after_review(review, sync_profiles=True)
        except Exception as exc:
            raise fail(exc) from exc

    return router
