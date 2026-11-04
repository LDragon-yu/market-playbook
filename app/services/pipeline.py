from __future__ import annotations

import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.db import Database
from app.demo import demo_bundle, demo_overseas, next_weekday
from app.engines import IntelligenceEngine, PlanEngine, ReviewEngine, ValidationEngine
from app.settings import Settings
from app.sources import AlphaVantageSource, BaiduOCRSource, LLMSource, TushareSource
from app.sources.akshare_source import AkShareSource
from app.worker_lock import job_lock


class ReviewPipeline:
    def __init__(self, settings: Settings, db: Database):
        self.settings, self.db = settings, db
        self.tushare = TushareSource(settings.tushare_token, settings.tushare_base_url)
        self.overseas = AlphaVantageSource(settings.alpha_vantage_api_key, settings.alpha_vantage_symbols)
        self.baidu_ocr = BaiduOCRSource(settings.baidu_ocr_api_key, settings.baidu_ocr_secret_key)
        self.llm = LLMSource(enabled=settings.llm_enabled, base_url=settings.llm_base_url,
                             api_key=settings.llm_api_key, model=settings.llm_model)
        self.akshare = AkShareSource(settings.akshare_enabled)
        self.review_engine, self.plan_engine = ReviewEngine(), PlanEngine()
        self.validation_engine = ValidationEngine()
        self.intelligence_engine = IntelligenceEngine(self.llm)
        self._job_guard = threading.Lock()

    @staticmethod
    def _date(value: str | None) -> str | None:
        if not value:
            return None
        compact = value.replace("-", "").strip()
        datetime.strptime(compact, "%Y%m%d")
        return compact

    def seed_demo(self, *, force: bool = False) -> dict[str, Any]:
        if self.db.get_review() and not force:
            return {"status": "skipped", "reason": "review already exists"}
        trade_date, bundle = demo_bundle()
        review = self.review_engine.build(trade_date, bundle, warnings=[
            "当前为产品演示数据；配置 TUSHARE_TOKEN 并运行盘后任务后自动替换。"
        ])
        review["provenance"] = {"mode": "demo", "label": "演示数据", "is_demo": True}
        self.db.save_review(trade_date, review)
        target = next_weekday(trade_date)
        plan = self.plan_engine.build(target_date=target, review=review,
                                      overseas=demo_overseas(), intelligence=[], warnings=[
            "外围市场为演示数据；配置 Alpha Vantage 后使用真实收盘行情。"
        ])
        plan["provenance"] = {"mode": "demo", "label": "演示预案", "is_demo": True}
        self.db.save_plan(target, trade_date, plan, force=True)
        return {"status": "ok", "trade_date": trade_date, "target_date": target}

    def run_post_market(self, trade_date: str | None = None, *, demo: bool = False) -> dict[str, Any]:
        if not self._job_guard.acquire(blocking=False):
            raise RuntimeError("另一个盘后/盘前任务正在运行，请等待其结束后再试。")
        try:
            with job_lock(self.settings.data_dir / "job.lock"):
                return self._run_post_market(trade_date, demo=demo)
        finally:
            self._job_guard.release()

    def _run_post_market(self, trade_date: str | None, *, demo: bool) -> dict[str, Any]:
        job_id = self.db.start_job("post_market")
        try:
            requested = self._date(trade_date)
            if demo:
                actual_date, bundle = demo_bundle(requested)
                warnings = ["本次任务使用演示数据。"]
            else:
                if not self.tushare.configured:
                    raise RuntimeError("尚未配置 TUSHARE_TOKEN；可先运行演示任务预览。")
                actual_date = requested or self.tushare.latest_trade_date()
                bundle, warnings = self.tushare.post_market_bundle(actual_date)
                if not bundle.get("daily"):
                    raise RuntimeError(f"Tushare 未返回 {actual_date} 日线数据，请确认交易日与积分权限。")
            review = self.review_engine.build(actual_date, bundle, warnings=warnings)
            review["provenance"] = {"mode": "demo" if demo else "live",
                                     "label": "演示数据" if demo else "Tushare 盘后数据",
                                     "is_demo": demo}
            self.db.save_review(actual_date, review)
            self.db.save_limit_universe(actual_date, review.get("stocks", []))
            self.db.save_sector_snapshot(actual_date, review.get("sectors", []))
            existing_plan = self.db.get_plan(actual_date)
            if existing_plan:
                self.db.save_evaluation(actual_date,
                    self.validation_engine.evaluate(existing_plan, bundle.get("daily", [])))
            try:
                self.db.prune_data()
            except Exception:
                warnings.append("历史数据清理失败；不影响本次复盘。")
            self.db.finish_job(job_id, "success", f"trade_date={actual_date}; warnings={len(warnings)}")
            return review
        except Exception as exc:
            self.db.finish_job(job_id, "failed", str(exc)[:1000])
            raise

    def run_pre_market(self, target_date: str | None = None, *, demo: bool = False,
                       force: bool = False) -> dict[str, Any]:
        if not self._job_guard.acquire(blocking=False):
            raise RuntimeError("另一个盘后/盘前任务正在运行，请等待其结束后再试。")
        try:
            with job_lock(self.settings.data_dir / "job.lock"):
                return self._run_pre_market(target_date, demo=demo, force=force)
        finally:
            self._job_guard.release()

    def _run_pre_market(self, target_date: str | None, *, demo: bool, force: bool) -> dict[str, Any]:
        job_id = self.db.start_job("pre_market")
        try:
            review = self.db.get_review()
            if not review:
                raise RuntimeError("没有盘后复盘，请先运行盘后任务。")
            source_date = str(review["trade_date"])
            requested = self._date(target_date)
            if requested:
                target = requested
            elif self.tushare.configured and not demo:
                target = self.tushare.next_trade_date(source_date)
            else:
                target = next_weekday(source_date)
            warnings: list[str] = []
            if demo:
                overseas = demo_overseas()
                warnings.append("本次任务使用演示外围行情。")
            else:
                overseas, source_warnings = self.overseas.overview()
                warnings.extend(source_warnings)
            plan = self.plan_engine.build(target_date=target, review=review, overseas=overseas,
                                          intelligence=self.db.recent_intelligence_raw(30), warnings=warnings)
            is_demo = bool(demo or review.get("provenance", {}).get("is_demo"))
            plan["provenance"] = {"mode": "demo" if is_demo else "live",
                                   "label": "演示预案" if is_demo else "冻结预案", "is_demo": is_demo}
            self.db.save_plan(target, source_date, plan, force=force or demo)
            self.db.finish_job(job_id, "success", f"target_date={target}; candidates={len(plan['candidates'])}")
            return plan
        except Exception as exc:
            self.db.finish_job(job_id, "failed", str(exc)[:1000])
            raise

    def add_text_intelligence(self, *, source_name: str, title: str, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("情报正文不能为空。")
        analysis = self.intelligence_engine.analyze(text)
        item_id = self.db.add_intelligence(source_name=source_name.strip() or "手工录入",
            source_type="text", title=title.strip() or "未命名情报", raw_text=text,
            summary=analysis["summary"], metadata=analysis)
        return {"id": item_id, **analysis}

    def add_image_intelligence(self, *, source_name: str, title: str, filename: str,
                               mime_type: str, image_bytes: bytes) -> dict[str, Any]:
        if not image_bytes:
            raise ValueError("图片为空。")
        if len(image_bytes) > 15 * 1024 * 1024:
            raise ValueError("图片不能超过 15MB。")
        suffixes = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
        if mime_type not in suffixes:
            raise ValueError("仅支持 JPG、PNG 或 WebP。")
        saved_name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:10]}{suffixes[mime_type]}"
        saved_path = self.settings.upload_dir / saved_name
        saved_path.write_bytes(image_bytes)
        if self.settings.ocr_mode == "baidu":
            if not self.baidu_ocr.configured:
                raise RuntimeError("OCR_MODE=baidu，但尚未配置百度 OCR 密钥。")
            raw_text, _ = self.baidu_ocr.extract(image_bytes)
            analysis = self.intelligence_engine.analyze(raw_text)
        elif self.settings.ocr_mode == "vision":
            if not self.llm.configured:
                raise RuntimeError("OCR_MODE=vision，但尚未配置支持视觉输入的大模型。")
            raw_text, llm_analysis = self.llm.analyze_image(image_bytes, mime_type)
            analysis = self.intelligence_engine._normalize(llm_analysis)
        else:
            raw_text = ""
            analysis = {"summary": "图片已保存，OCR 尚未配置，暂未纳入盘前情报判断。",
                        "sectors": [], "tickers": [], "catalysts": [],
                        "risks": ["图片内容尚未识别"], "stance": "neutral",
                        "horizon": "unknown", "claims": [], "analysis_mode": "pending_ocr"}
        item_id = self.db.add_intelligence(source_name=source_name.strip() or "截图导入",
            source_type="image", title=title.strip() or Path(filename).stem, raw_text=raw_text,
            summary=analysis["summary"], metadata=analysis, image_path=str(saved_path))
        return {"id": item_id, "filename": saved_name, **analysis}

    def dashboard(self, trade_date: str | None = None) -> dict[str, Any]:
        review = self.db.get_review(self._date(trade_date))
        plan = self.db.get_plan()
        return {"review": review, "plan": plan,
                "evaluation": self.db.get_evaluation((plan or {}).get("target_date")),
                "intelligence": self.db.list_intelligence(30),
                "review_dates": self.db.list_review_dates(), "jobs": self.db.latest_jobs(),
                "status": self.config_status()}

    def config_status(self) -> dict[str, Any]:
        return {"app_env": self.settings.app_env, "auth_enabled": self.settings.auth_enabled,
                "sources": [
            {"id": "tushare", "name": "Tushare Pro", "configured": self.tushare.configured,
             "purpose": "A股盘后行情、行业题材、涨停梯队与单股快照", "points_tier": self.settings.tushare_points_tier},
            {"id": "alpha_vantage", "name": "Alpha Vantage", "configured": self.overseas.configured,
             "purpose": "美股与中概 ETF 收盘行情"},
            {"id": "ocr", "name": "截图识别",
             "configured": (self.settings.ocr_mode == "baidu" and self.baidu_ocr.configured)
                           or (self.settings.ocr_mode == "vision" and self.llm.configured),
             "purpose": "同花顺、开盘啦及自媒体截图", "mode": self.settings.ocr_mode},
            {"id": "llm", "name": "大模型分析", "configured": self.llm.configured,
             "purpose": "情报提取、事件解释与个股问答", "model": self.settings.llm_model},
            {"id": "akshare", "name": "AKShare（可选）", "configured": self.settings.akshare_enabled,
             "purpose": "板块 5 分钟走势与跷跷板验证"},
        ]}
