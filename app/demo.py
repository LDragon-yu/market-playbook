from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def latest_weekday(day: date | None = None) -> date:
    current = day or date.today()
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def next_weekday(value: str) -> str:
    current = date(int(value[:4]), int(value[4:6]), int(value[6:8])) + timedelta(days=1)
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current.strftime("%Y%m%d")


def demo_bundle(trade_date: str | None = None) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    """Small, visibly-labelled sample data for previewing the product."""
    day = trade_date or latest_weekday().strftime("%Y%m%d")
    stocks = [
        ("000001.SZ", "星海科技", 10.02, 8.80, 13.2, "人工智能", "092503", 0),
        ("000002.SZ", "远望智能", 10.00, 6.90, 18.4, "人工智能", "093108", 1),
        ("600001.SH", "华芯电子", 10.01, 12.30, 9.6, "半导体", "094211", 0),
        ("600002.SH", "联创算力", 9.98, 10.10, 22.1, "算力", "095530", 2),
        ("002001.SZ", "新源动力", 10.03, 7.20, 16.8, "新能源", "101406", 0),
        ("300001.SZ", "智航机器", 19.99, 18.40, 25.6, "机器人", "102102", 1),
        ("603001.SH", "金岭资源", 10.00, 9.40, 11.2, "有色", "110319", 0),
        ("002002.SZ", "云端数据", 10.01, 5.60, 20.4, "数据中心", "132744", 3),
    ]
    heights = {"000001.SZ": 5, "000002.SZ": 3, "600001.SH": 2, "600002.SH": 2}
    daily, basics, limits, limit_list = [], [], [], []
    for index, (code, name, pct, close, turnover, sector, first_time, opens) in enumerate(stocks):
        pre_close = round(close / (1 + pct / 100), 2)
        daily.append({
            "ts_code": code, "trade_date": day, "open": round(pre_close * 1.02, 2),
            "high": close, "low": round(pre_close * .99, 2), "close": close,
            "pre_close": pre_close, "pct_chg": pct, "amount": 650000 + index * 170000,
            "name": name,
        })
        basics.append({
            "ts_code": code, "close": close, "turnover_rate": turnover,
            "amount": 650000 + index * 170000, "total_mv": 4_000_000 + index * 300000,
            "name": name,
        })
        limits.append({"ts_code": code, "up_limit": close, "down_limit": round(pre_close * .9, 2)})
        height = heights.get(code, 1)
        limit_list.append({
            "ts_code": code, "trade_date": day, "name": name, "price": close,
            "pct_chg": pct, "open_num": opens, "lu_desc": f"{sector}板块活跃，资金关注度提升",
            "limit_type": "涨停池", "tag": f"{height}天{height}板" if height > 1 else "首板",
            "status": "封板", "first_lu_time": first_time, "turnover_rate": turnover,
            "turnover": 650000000 + index * 170000000,
        })

    for index in range(90):
        pct = round(((index * 17) % 83 - 34) / 10, 2)
        pre_close = 10 + index % 11
        close = round(pre_close * (1 + pct / 100), 2)
        daily.append({
            "ts_code": f"{100000 + index:06d}.SZ", "trade_date": day,
            "open": pre_close, "high": max(pre_close, close), "low": min(pre_close, close),
            "close": close, "pre_close": pre_close, "pct_chg": pct,
            "amount": 100000 + index * 1000,
        })

    sectors = [
        ("人工智能", 4.82, 12, 3, 4, "5天4板"),
        ("半导体", 3.71, 9, 2, 3, "3天2板"),
        ("机器人", 3.15, 7, 1, 2, "2天2板"),
        ("算力", 2.46, 6, 2, 2, "3天2板"),
        ("有色", 1.68, 4, 0, 1, "首板"),
        ("新能源", 1.22, 3, 0, 1, "首板"),
        ("医药", -.36, 1, 0, 1, "首板"),
    ]
    return day, {
        "daily": daily, "daily_basic": basics, "stk_limit": limits,
        "limit_list_ths": limit_list,
        "limit_step": [{"ts_code": code, "name": name, "trade_date": day,
                        "nums": heights.get(code, 1)} for code, name, *_ in stocks],
        "limit_cpt_list": [{
            "trade_date": day, "ts_code": f"D{index + 1:03d}", "name": name,
            "days": days, "up_stat": stat, "cons_nums": high, "up_nums": ups,
            "pct_chg": pct, "rank": index + 1,
        } for index, (name, pct, ups, high, days, stat) in enumerate(sectors)],
        "moneyflow_cnt_ths": [{
            "trade_date": day, "name": name, "lead_stock": stocks[index % len(stocks)][1],
            "pct_change": pct, "net_amount": round(12.5 - index * 2.1, 2),
        } for index, (name, pct, *_rest) in enumerate(sectors)],
        "indices": [
            {"ts_code": "000001.SH", "name": "上证指数", "close": 3286.41, "pct_chg": .56, "amount": 462300000},
            {"ts_code": "399001.SZ", "name": "深证成指", "close": 10486.25, "pct_chg": .91, "amount": 598700000},
            {"ts_code": "399006.SZ", "name": "创业板指", "close": 2118.72, "pct_chg": 1.21, "amount": 256400000},
            {"ts_code": "000300.SH", "name": "沪深300", "close": 3861.34, "pct_chg": .48, "amount": 294500000},
        ],
    }


def demo_overseas() -> list[dict[str, Any]]:
    return [
        {"symbol": "SPY", "name": "标普500 ETF", "price": 604.18, "change_pct": .42, "latest_day": "演示", "status": "ok"},
        {"symbol": "QQQ", "name": "纳指100 ETF", "price": 531.70, "change_pct": .68, "latest_day": "演示", "status": "ok"},
        {"symbol": "SOXX", "name": "费城半导体 ETF", "price": 236.44, "change_pct": 1.26, "latest_day": "演示", "status": "ok"},
        {"symbol": "KWEB", "name": "中概互联网 ETF", "price": 31.08, "change_pct": -.31, "latest_day": "演示", "status": "ok"},
    ]
