from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .http import request_json


class TushareError(RuntimeError):
    def __init__(self, api_name: str, code: int | str, message: str):
        super().__init__(f"Tushare {api_name} failed [{code}]: {message}")
        self.api_name = api_name
        self.code = code
        self.message = message


class TushareSource:
    """Minimal dependency-free Tushare HTTP client."""

    def __init__(self, token: str, base_url: str = "http://api.tushare.pro"):
        self.token = token
        self.base_url = base_url

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def query(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str = "",
    ) -> list[dict[str, Any]]:
        if not self.token:
            raise TushareError(api_name, "CONFIG", "TUSHARE_TOKEN is empty")
        response = request_json(
            self.base_url,
            method="POST",
            payload={
                "api_name": api_name,
                "token": self.token,
                "params": params or {},
                "fields": fields,
            },
        )
        code = response.get("code", -1)
        if code != 0:
            raise TushareError(api_name, code, str(response.get("msg", "unknown error")))
        data = response.get("data") or {}
        columns = data.get("fields") or []
        return [dict(zip(columns, item, strict=False)) for item in data.get("items") or []]

    def latest_trade_date(self, as_of: date | None = None) -> str:
        current = as_of or date.today()
        start = current - timedelta(days=15)
        rows = self.query(
            "trade_cal",
            {
                "exchange": "SSE",
                "start_date": start.strftime("%Y%m%d"),
                "end_date": current.strftime("%Y%m%d"),
                "is_open": "1",
            },
            "cal_date,is_open,pretrade_date",
        )
        candidates = sorted(row["cal_date"] for row in rows if row.get("is_open") in {1, "1"})
        if not candidates:
            raise TushareError("trade_cal", "EMPTY", "no open trading date returned")
        return candidates[-1]

    def next_trade_date(self, after: str) -> str:
        current = date(int(after[:4]), int(after[4:6]), int(after[6:8]))
        rows = self.query(
            "trade_cal",
            {
                "exchange": "SSE",
                "start_date": (current + timedelta(days=1)).strftime("%Y%m%d"),
                "end_date": (current + timedelta(days=15)).strftime("%Y%m%d"),
                "is_open": "1",
            },
            "cal_date,is_open",
        )
        candidates = sorted(row["cal_date"] for row in rows if row.get("is_open") in {1, "1"})
        if not candidates:
            raise TushareError("trade_cal", "EMPTY", "no next trading date returned")
        return candidates[0]

    def post_market_bundle(self, trade_date: str) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
        data: dict[str, list[dict[str, Any]]] = {}
        warnings: list[str] = []
        requests = [
            ("daily", {"trade_date": trade_date}, "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"),
            ("daily_basic", {"trade_date": trade_date}, "ts_code,trade_date,close,turnover_rate,volume_ratio,total_mv,circ_mv,limit_status"),
            ("stk_limit", {"trade_date": trade_date}, "trade_date,ts_code,pre_close,up_limit,down_limit,exchange"),
            ("limit_list_ths", {"trade_date": trade_date}, "ts_code,trade_date,name,price,pct_chg,open_num,lu_desc,limit_type,tag,status,first_lu_time,last_lu_time,limit_order,limit_amount,turnover_rate,turnover,sum_float"),
            ("limit_step", {"trade_date": trade_date}, "ts_code,name,trade_date,nums"),
            ("limit_cpt_list", {"trade_date": trade_date}, "trade_date,ts_code,name,days,up_stat,cons_nums,up_nums,pct_chg,rank"),
            ("moneyflow_cnt_ths", {"trade_date": trade_date}, "trade_date,ts_code,name,lead_stock,pct_change,company_num,pct_change_stock,net_buy_amount,net_sell_amount,net_amount"),
        ]
        for api_name, params, fields in requests:
            try:
                data[api_name] = self.query(api_name, params, fields)
            except TushareError as exc:
                data[api_name] = []
                warnings.append(str(exc))

        indices: list[dict[str, Any]] = []
        for code, name in [
            ("000001.SH", "上证指数"),
            ("399001.SZ", "深证成指"),
            ("399006.SZ", "创业板指"),
            ("000300.SH", "沪深300"),
        ]:
            try:
                rows = self.query(
                    "index_daily",
                    {"ts_code": code, "trade_date": trade_date},
                    "ts_code,trade_date,close,open,high,low,pre_close,pct_chg,vol,amount",
                )
                for row in rows:
                    row["name"] = name
                indices.extend(rows)
            except TushareError as exc:
                warnings.append(str(exc))
        data["indices"] = indices
        return data, warnings

