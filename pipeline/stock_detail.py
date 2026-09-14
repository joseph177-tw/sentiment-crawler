# -*- coding: utf-8 -*-
"""
個股詳細頁資料層：給 pipeline/stock_detect.py 偵測到的每一檔個股（讀
data/raw/market_data.json），抓取：
  1. TWSE 官方免費即時行情快照（含五檔買賣報價、漲停/跌停、均價、總量）
  2. Yahoo Finance 多區間走勢（當日/五日/近月/三月/六月/一年/五年，對應
     一般看盤軟體的分頁籤）
供 report/render.py --stock-detail 產出 docs/stocks/<代號>.html。

用法：
    python pipeline/stock_detail.py              # 正式模式
    python pipeline/stock_detail.py --offline     # 離線模式：讀 fixtures
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

log = common.setup_logging("pipeline.stock_detail")

TWSE_REALTIME_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{code}.TW"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sentiment-research/1.0"}

FIXTURE_REALTIME = common.BASE_DIR / "fixtures" / "twse_realtime_sample.json"
FIXTURE_CHART = common.BASE_DIR / "fixtures" / "yahoo_chart_sample.json"

_INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m"}


def _split_ladder(price_str: str, vol_str: str, levels: int = 5) -> list[dict]:
    prices = (price_str or "").rstrip("_").split("_")
    vols = (vol_str or "").rstrip("_").split("_")
    ladder = []
    for p, v in zip(prices, vols):
        if not p or p == "-" or not v or v == "-":
            continue
        try:
            ladder.append({"price": float(p), "volume": int(v)})
        except ValueError:
            continue
    return ladder[:levels]


def fetch_realtime_quote(code: str, offline: bool = False) -> dict | None:
    """TWSE 官方免費即時行情（有官方揭露的延遲，非真正逐筆即時），欄位說明見
    https://mis.twse.com.tw （非正式文件，此處依實際回應欄位解析）。"""
    if offline:
        import json
        data = json.loads(FIXTURE_REALTIME.read_text(encoding="utf-8"))
    else:
        try:
            resp = requests.get(
                TWSE_REALTIME_URL,
                params={"ex_ch": f"tse_{code}.tw", "json": "1"},
                headers=HEADERS, timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("TWSE 即時行情抓取失敗，略過 %s：%s", code, e)
            return None

    msg_list = data.get("msgArray") or []
    if not msg_list:
        log.warning("TWSE 即時行情無資料（可能非交易時段或代號錯誤）：%s", code)
        return None
    msg = msg_list[0]

    def _num(key, default=None):
        val = msg.get(key)
        if val in (None, "", "-"):
            return default
        try:
            return float(val)
        except ValueError:
            return default

    last_price = _num("z") or _num("y")
    prev_close = _num("y")
    change = (last_price - prev_close) if (last_price is not None and prev_close) else None
    change_pct = (change / prev_close * 100) if (change is not None and prev_close) else None

    return {
        "code": msg.get("c", code),
        "name": msg.get("n", ""),
        "full_name": msg.get("nf", ""),
        "last_price": last_price,
        "prev_close": prev_close,
        "change": round(change, 2) if change is not None else None,
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "open": _num("o"),
        "high": _num("h"),
        "low": _num("l"),
        "limit_up": _num("u"),
        "limit_down": _num("w"),
        "total_volume": int(_num("v", 0) or 0),
        "bids": _split_ladder(msg.get("b", ""), msg.get("g", "")),
        "asks": _split_ladder(msg.get("a", ""), msg.get("f", "")),
        "date": msg.get("d", ""),
        "time": (data.get("queryTime") or {}).get("sysTime", ""),
    }


def fetch_range_series(code: str, range_: str, interval: str, offline: bool = False) -> list[dict]:
    if offline:
        import json
        data = json.loads(FIXTURE_CHART.read_text(encoding="utf-8"))
    else:
        try:
            resp = requests.get(
                YAHOO_CHART_URL.format(code=code),
                params={"range": range_, "interval": interval},
                headers=HEADERS, timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Yahoo Finance 抓取失敗，略過 %s（%s/%s）：%s", code, range_, interval, e)
            return []

    try:
        result = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return []

    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    intraday = interval in _INTRADAY_INTERVALS
    fmt = "%H:%M" if intraday else "%Y-%m-%d"

    points = []
    for i, ts in enumerate(timestamps):
        o, h, l, c, v = (quote[k][i] for k in ("open", "high", "low", "close", "volume"))
        if c is None:
            continue
        label = datetime.fromtimestamp(ts, common.get_timezone()).strftime(fmt)
        points.append({
            "t": label,
            "open": round(o, 2) if o is not None else None,
            "high": round(h, 2) if h is not None else None,
            "low": round(l, 2) if l is not None else None,
            "close": round(c, 2),
            "volume": v or 0,
        })
    return points


def run(offline: bool = False) -> Path:
    market_data = common.read_json(common.RAW_DIR / "market_data.json", default=None)
    if not market_data or not market_data.get("stocks"):
        log.warning("找不到 market_data.json 或無偵測個股，請先執行 pipeline/stock_detect.py")
        out_path = common.RAW_DIR / "stock_detail.json"
        common.write_json(out_path, {"generated_at": datetime.now(common.get_timezone()).isoformat(), "stocks": {}})
        return out_path

    market_cfg = common.load_settings().get("market", {})
    ranges = market_cfg.get("detail_ranges", [])
    delay_range = market_cfg.get("request_delay_seconds", [0.3, 0.6])

    detail: dict[str, dict] = {}
    for stock in market_data["stocks"]:
        code, name = stock["code"], stock["name"]
        log.info("抓取個股詳細資料：%s %s", code, name)
        quote = fetch_realtime_quote(code, offline=offline)

        series_by_range = {}
        for r in ranges:
            points = fetch_range_series(code, r["range"], r["interval"], offline=offline)
            series_by_range[r["label"]] = points
            if not offline:
                time.sleep(sum(delay_range) / 2)

        detail[code] = {
            "code": code, "name": name,
            "quote": quote,
            "series": series_by_range,
        }

    out_data = {"generated_at": datetime.now(common.get_timezone()).isoformat(), "stocks": detail}
    out_path = common.RAW_DIR / "stock_detail.json"
    common.write_json(out_path, out_data)
    log.info("個股詳細資料完成：%s（%d 檔）", out_path, len(detail))
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="抓取個股詳細頁資料（即時行情＋多區間走勢）")
    parser.add_argument("--offline", action="store_true", help="讀取 fixtures，不連外網")
    args = parser.parse_args()
    run(offline=args.offline)
