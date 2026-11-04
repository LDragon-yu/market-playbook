from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.portfolio_db import PortfolioRepository
from app.sources.tushare import TushareSource


ALIASES = {
    "AI": "人工智能",
    "人工智能应用": "AI应用",
    "大模型": "人工智能",
    "华为合作": "华为概念",
    "海峡两岸": "福建",
    "福建国资": "福建",
    "乡村振兴": "农业",
    "种业": "农业",
    "创新药": "医药",
    "中药": "医药",
    "医药商业": "医药",
    "机器人轴承": "机器人",
    "人形机器人": "机器人",
    "光伏设备": "光伏",
    "固态电池": "新能源",
}


def reason_themes(reason: str) -> list[str]:
    values: list[str] = []
    for raw in re.split(r"[+、，,/；;|]", reason or ""):
        value = raw.strip().strip("“”\"'")
        if not value:
            continue
        values.append(value)
        alias = next((target for key, target in ALIASES.items() if key in value), "")
        if alias and alias not in values:
            values.append(alias)
    return list(dict.fromkeys(values))[:12]


class ClassificationService:
    def __init__(self, source: TushareSource, repository: PortfolioRepository):
        self.source = source
        self.repository = repository

    def refresh_profiles(self, codes: list[str], *, max_workers: int = 1) -> list[str]:
        unique = list(dict.fromkeys(code for code in codes if code))
        warnings: list[str] = []
        concept_names: dict[str, str] = {}
        try:
            rows = self.source.query("ths_index", {}, "ts_code,name,count,type,list_date")
            concept_names = {str(row.get("ts_code")): str(row.get("name") or row.get("ts_code")) for row in rows}
        except Exception as exc:
            warnings.append(f"ths_index: {exc}")

        def load(code: str) -> dict[str, Any]:
            basic = self.source.query("stock_basic", {"ts_code": code}, "ts_code,name,industry")
            sw = self.source.query(
                "index_member_all", {"ts_code": code, "is_new": "Y"},
                "ts_code,name,l1_name,l2_name,l3_name,is_new",
            )
            members = self.source.query("ths_member", {"con_code": code}, "ts_code,con_code,con_name")
            basic_row = basic[0] if basic else {"ts_code": code, "name": code, "industry": ""}
            sw_row = sw[0] if sw else {}
            concepts = [concept_names.get(str(row.get("ts_code")), str(row.get("ts_code", "")))
                        for row in members if row.get("ts_code")]
            return {
                "ts_code": code,
                "name": basic_row.get("name") or sw_row.get("name") or code,
                "industry": basic_row.get("industry") or "",
                "sw_l1": sw_row.get("l1_name") or "",
                "sw_l2": sw_row.get("l2_name") or "",
                "sw_l3": sw_row.get("l3_name") or "",
                "concepts": list(dict.fromkeys(concepts)),
            }

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(load, code): code for code in unique}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    self.repository.upsert_profile(future.result())
                except Exception as exc:
                    warnings.append(f"profile {code}: {exc}")
        return warnings

    def ensure_profile(self, code: str) -> dict[str, Any]:
        profile = self.repository.get_profile(code)
        if profile:
            return profile
        warnings = self.refresh_profiles([code], max_workers=1)
        profile = self.repository.get_profile(code)
        if not profile:
            raise RuntimeError("；".join(warnings) or f"无法取得 {code} 的股票资料")
        return profile

    def enrich_review(self, review: dict[str, Any]) -> dict[str, Any]:
        stocks = review.get("stocks", [])
        profiles = self.repository.list_profiles([str(row.get("code", "")) for row in stocks])
        active_sectors = [str(row.get("name", "")) for row in review.get("sectors", [])]
        mapped, total = 0, len(stocks)
        for stock in stocks:
            code = str(stock.get("code", ""))
            profile = profiles.get(code, {})
            themes = reason_themes(str(stock.get("reason", "")))
            concepts = list(profile.get("concepts") or [])
            primary = self._primary_sector(themes, concepts, profile, active_sectors)
            stock.update({
                "sector": primary,
                "industry": profile.get("industry", ""),
                "industry_path": [item for item in (
                    profile.get("sw_l1"), profile.get("sw_l2"), profile.get("sw_l3")
                ) if item],
                "themes": themes,
                "concepts": concepts[:30],
            })
            if primary and primary != "待确认题材":
                mapped += 1

        classification_by_code = {
            str(stock.get("code", "")): {
                key: stock.get(key)
                for key in ("sector", "industry", "industry_path", "themes", "concepts")
            }
            for stock in stocks
        }
        related_stocks = list(review.get("candidates", []))
        for ladder in review.get("ladders", []):
            related_stocks.extend(ladder.get("stocks", []))
        for stock in related_stocks:
            stock.update(classification_by_code.get(str(stock.get("code", "")), {}))

        review["classification"] = {
            "mapped": mapped,
            "total": total,
            "mapped_rate": round(mapped / max(1, total), 4),
            "method": "涨停原因标签 + 同花顺概念成分 + 申万行业兜底",
        }
        return review

    @staticmethod
    def _primary_sector(
        themes: list[str], concepts: list[str], profile: dict[str, Any], active: list[str]
    ) -> str:
        def normalized(value: str) -> str:
            return re.sub(r"概念|板块|产业链|主题", "", value).lower()

        candidates = themes + concepts
        for sector in active:
            left = normalized(sector)
            for value in candidates:
                right = normalized(value)
                if left and right and (left in right or right in left):
                    return sector
        for theme in themes:
            if theme in ALIASES.values() or len(theme) <= 8:
                return theme
        return str(profile.get("sw_l1") or profile.get("industry") or "待确认题材")
