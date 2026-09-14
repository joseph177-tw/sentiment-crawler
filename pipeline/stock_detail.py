# -*- coding: utf-8 -*-
"""
個股詳細頁資料層：給 pipeline/stock_detect.py 偵測到的每一檔個股（讀
data/raw/market_data.json），抓取：
  1. TWSE 官方免費即時行情快照（現價/漲跌/開高低/漲停跌停/總量）
  2. Yahoo Finance 當日/五日/五年走勢 + 2 年日線（技術指標與近月/三月/
     六月/一年分頁共用同一份日線資料，確保價格圖跟指標疊圖日期對得齊）
  3. 技術指標（MA/RSI/MACD/KD/DMI+ADX/布林通道/BIAS/OBV），全部用 (2) 的
     日線資料在本地算出來，不需要額外資料源
  4. TWSE 三大法人買賣超（近 N 個交易日趨勢）
  5. TWSE 本益比/殖利率/股價淨值比
  6. TWSE 融資融券餘額
  7. TWSE 月營收 YoY/MoM
  8. TWSE 公司基本資料（產業別、股本、上市日期、董事長等）
供 report/render.py --stock-detail 產出 docs/stocks/<代號>.html。

用法：
    python pipeline/stock_detail.py              # 正式模式
    python pipeline/stock_detail.py --offline     # 離線模式：讀 fixtures
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

log = common.setup_logging("pipeline.stock_detail")

TWSE_REALTIME_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{code}.TW"
TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TWSE_BWIBBU_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_ALL"
TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TWSE_COMPANY_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sentiment-research/1.0"}

FIXTURE_REALTIME = common.BASE_DIR / "fixtures" / "twse_realtime_sample.json"
FIXTURE_CHART = common.BASE_DIR / "fixtures" / "yahoo_chart_sample.json"

_INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m"}


# ---------------------------------------------------------------------------
# 1. TWSE 即時行情（不含五檔，個股詳細頁不再顯示五檔報價）
# ---------------------------------------------------------------------------

def fetch_realtime_quote(code: str, offline: bool = False) -> dict | None:
    if offline:
        import json
        data = json.loads(FIXTURE_REALTIME.read_text(encoding="utf-8"))
    else:
        try:
            resp = requests.get(
                TWSE_REALTIME_URL, params={"ex_ch": f"tse_{code}.tw", "json": "1"},
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
        "date": msg.get("d", ""),
        "time": (data.get("queryTime") or {}).get("sysTime", ""),
    }


# ---------------------------------------------------------------------------
# 2. Yahoo Finance 走勢（當日/五日/五年 + 技術指標基礎日線）
# ---------------------------------------------------------------------------

def fetch_range_series(code: str, range_: str, interval: str, offline: bool = False) -> list[dict]:
    if offline:
        import json
        data = json.loads(FIXTURE_CHART.read_text(encoding="utf-8"))
    else:
        try:
            resp = requests.get(
                YAHOO_CHART_URL.format(code=code), params={"range": range_, "interval": interval},
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


# ---------------------------------------------------------------------------
# 3. 技術指標（純計算，來源是 (2) 抓到的日線資料）
# ---------------------------------------------------------------------------

def compute_indicators(points: list[dict]) -> dict:
    """回傳每個指標對齊同一組 dates 的陣列，NaN/資料不足處補 None。"""
    df = pd.DataFrame(points)
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]

    def ser(s) -> list[float | None]:
        return [None if pd.isna(v) else round(float(v), 3) for v in s]

    ma_periods = (5, 10, 20, 60, 120, 240)
    ma = {n: close.rolling(n).mean() for n in ma_periods}

    # RSI-14（Wilder 平滑）
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi14 = 100 - (100 / (1 + rs))

    # MACD (12,26,9)，柱狀圖乘 2 是台股常見畫法
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_hist = (dif - dea) * 2

    # KD (9,3,3)，遞迴平滑，起始值設 50
    low9, high9 = low.rolling(9).min(), high.rolling(9).max()
    rsv = ((close - low9) / (high9 - low9).replace(0, np.nan) * 100).fillna(50)
    k_vals, d_vals = [50.0], [50.0]
    for i in range(1, len(rsv)):
        k_vals.append(k_vals[-1] * 2 / 3 + rsv.iloc[i] * 1 / 3)
        d_vals.append(d_vals[-1] * 2 / 3 + k_vals[-1] * 1 / 3)
    k = pd.Series(k_vals, index=df.index)
    d = pd.Series(d_vals, index=df.index)
    warmup = close.rolling(9).mean().isna()  # 前 8 筆資料不足，不顯示
    k[warmup] = np.nan
    d[warmup] = np.nan

    # DMI/ADX (14)，Wilder 平滑法的常見近似實作
    up_move, down_move = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    tr = pd.concat([
        high - low, (high - close.shift()).abs(), (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()

    # 布林通道 (20, 2)
    boll_mid = ma[20]
    boll_std = close.rolling(20).std()
    boll_upper, boll_lower = boll_mid + 2 * boll_std, boll_mid - 2 * boll_std

    # 乖離率 BIAS(20)
    bias20 = (close - ma[20]) / ma[20] * 100

    # OBV 能量潮
    direction = np.sign(close.diff().fillna(0))
    obv = (direction * volume).cumsum()

    return {
        "dates": df["t"].tolist(),
        "close": ser(close),
        "ma": {str(n): ser(ma[n]) for n in ma_periods},
        "rsi14": ser(rsi14),
        "macd": {"dif": ser(dif), "dea": ser(dea), "hist": ser(macd_hist)},
        "kd": {"k": ser(k), "d": ser(d)},
        "dmi": {"plus_di": ser(plus_di), "minus_di": ser(minus_di), "adx": ser(adx)},
        "boll": {"upper": ser(boll_upper), "mid": ser(boll_mid), "lower": ser(boll_lower)},
        "bias20": ser(bias20),
        "obv": [None if pd.isna(v) else int(v) for v in obv],
    }


# ---------------------------------------------------------------------------
# 4. 三大法人買賣超（近期趨勢）
# ---------------------------------------------------------------------------

def fetch_institutional_trend(codes: set[str], days: int, lookback_days: int,
                              offline: bool = False) -> dict[str, list[dict]]:
    if offline:
        return {code: [] for code in codes}

    tz = common.get_timezone()
    result: dict[str, list[dict]] = {code: [] for code in codes}
    collected = 0
    day = datetime.now(tz)

    for _ in range(lookback_days):
        if collected >= days:
            break
        if day.weekday() >= 5:  # 週六日直接跳過，不浪費請求
            day -= timedelta(days=1)
            continue
        date_str = day.strftime("%Y%m%d")
        try:
            resp = requests.get(
                TWSE_T86_URL, params={"response": "json", "date": date_str, "selectType": "ALLBUT0999"},
                headers=HEADERS, timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("T86 三大法人抓取例外（%s）：%s", date_str, e)
            day -= timedelta(days=1)
            continue

        if data.get("stat") != "OK":
            day -= timedelta(days=1)
            continue  # 非交易日或當日尚未公布

        for row in data.get("data", []):
            code = row[0].strip()
            if code not in codes:
                continue
            try:
                foreign = int(row[4].replace(",", "")) + int(row[7].replace(",", ""))
                trust = int(row[10].replace(",", ""))
                dealer = int(row[11].replace(",", ""))
                total = int(row[18].replace(",", ""))
            except (ValueError, IndexError):
                continue
            result[code].append({
                "date": day.strftime("%Y-%m-%d"), "foreign": foreign,
                "trust": trust, "dealer": dealer, "total": total,
            })
        collected += 1
        day -= timedelta(days=1)
        time.sleep(0.3)

    for code in result:
        result[code].sort(key=lambda r: r["date"])
    return result


# ---------------------------------------------------------------------------
# 5-8. 估值指標 / 融資融券 / 月營收 / 公司基本資料（每項各一次請求，抓全市場後篩選）
# ---------------------------------------------------------------------------

def fetch_valuation(codes: set[str], offline: bool = False) -> dict[str, dict]:
    if offline:
        return {}
    try:
        resp = requests.get(TWSE_BWIBBU_URL, params={"response": "json"}, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("本益比/殖利率/股價淨值比抓取失敗：%s", e)
        return {}

    def _f(v):
        try:
            return None if v in (None, "-", "") else float(v)
        except ValueError:
            return None

    out = {}
    for row in data.get("data", []):
        code = row[0].strip()
        if code in codes:
            out[code] = {"pe": _f(row[2]), "yield_pct": _f(row[3]), "pb": _f(row[4])}
    return out


def fetch_margin(codes: set[str], lookback_days: int, offline: bool = False) -> dict[str, dict]:
    if offline:
        return {}
    tz = common.get_timezone()
    day = datetime.now(tz)
    for _ in range(lookback_days):
        if day.weekday() >= 5:
            day -= timedelta(days=1)
            continue
        date_str = day.strftime("%Y%m%d")
        try:
            resp = requests.get(
                TWSE_MARGIN_URL, params={"response": "json", "date": date_str, "selectType": "ALL"},
                headers=HEADERS, timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("融資融券抓取例外（%s）：%s", date_str, e)
            day -= timedelta(days=1)
            continue

        if data.get("stat") != "OK" or len(data.get("tables", [])) < 2:
            day -= timedelta(days=1)
            continue

        rows = data["tables"][1].get("data", [])

        def _i(v):
            try:
                return int(v.replace(",", ""))
            except (ValueError, AttributeError):
                return None

        out = {}
        for row in rows:
            code = row[0].strip()
            if code not in codes:
                continue
            out[code] = {
                "date": day.strftime("%Y-%m-%d"),
                "margin_balance": _i(row[6]), "margin_prev_balance": _i(row[5]),
                "short_balance": _i(row[12]), "short_prev_balance": _i(row[11]),
            }
        return out
    return {}


def fetch_monthly_revenue(codes: set[str], offline: bool = False) -> dict[str, dict]:
    if offline:
        return {}
    try:
        resp = requests.get(TWSE_REVENUE_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("月營收抓取失敗：%s", e)
        return {}

    def _f(v):
        try:
            return None if v in (None, "", "-") else float(v)
        except ValueError:
            return None

    out = {}
    for row in data:
        code = row.get("公司代號", "").strip()
        if code in codes:
            out[code] = {
                "year_month": row.get("資料年月"),
                "industry": row.get("產業別"),
                "revenue": _f(row.get("營業收入-當月營收")),
                "mom_pct": _f(row.get("營業收入-上月比較增減(%)")),
                "yoy_pct": _f(row.get("營業收入-去年同月增減(%)")),
                "accumulated_revenue": _f(row.get("累計營業收入-當月累計營收")),
                "accumulated_yoy_pct": _f(row.get("累計營業收入-前期比較增減(%)")),
            }
    return out


def fetch_company_info(codes: set[str], offline: bool = False) -> dict[str, dict]:
    if offline:
        return {}
    try:
        resp = requests.get(TWSE_COMPANY_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("公司基本資料抓取失敗：%s", e)
        return {}

    out = {}
    for row in data:
        code = row.get("公司代號", "").strip()
        if code in codes:
            out[code] = {
                "full_name": row.get("公司名稱"),
                "capital": row.get("實收資本額"),
                "listed_date": row.get("上市日期"),
                "established_date": row.get("成立日期"),
                "chairman": row.get("董事長"),
                "general_manager": row.get("總經理"),
                "spokesperson": row.get("發言人"),
                "website": row.get("網址"),
            }
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(offline: bool = False) -> Path:
    market_data = common.read_json(common.RAW_DIR / "market_data.json", default=None)
    if not market_data or not market_data.get("stocks"):
        log.warning("找不到 market_data.json 或無偵測個股，請先執行 pipeline/stock_detect.py")
        out_path = common.RAW_DIR / "stock_detail.json"
        common.write_json(out_path, {"generated_at": datetime.now(common.get_timezone()).isoformat(), "stocks": {}})
        return out_path

    market_cfg = common.load_settings().get("market", {})
    delay_range = market_cfg.get("request_delay_seconds", [0.3, 0.6])
    detail_ranges = market_cfg.get("detail_ranges", [])
    indicator_range = market_cfg.get("indicator_range", "2y")
    tab_days = market_cfg.get("indicator_tab_days", {})
    inst_days = market_cfg.get("institutional_days", 20)
    inst_lookback = market_cfg.get("institutional_lookback_days", 45)

    codes = {s["code"] for s in market_data["stocks"]}
    log.info("批次抓取三大法人買賣超（近 %d 個交易日）...", inst_days)
    institutional = fetch_institutional_trend(codes, inst_days, inst_lookback, offline=offline)
    log.info("批次抓取估值指標／融資融券／月營收／公司基本資料...")
    valuation = fetch_valuation(codes, offline=offline)
    margin = fetch_margin(codes, inst_lookback, offline=offline)
    revenue = fetch_monthly_revenue(codes, offline=offline)
    company = fetch_company_info(codes, offline=offline)

    detail: dict[str, dict] = {}
    for stock in market_data["stocks"]:
        code, name = stock["code"], stock["name"]
        log.info("抓取個股走勢與指標：%s %s", code, name)
        quote = fetch_realtime_quote(code, offline=offline)

        series_by_range = {}
        for r in detail_ranges:
            series_by_range[r["label"]] = fetch_range_series(code, r["range"], r["interval"], offline=offline)
            if not offline:
                time.sleep(sum(delay_range) / 2)

        indicator_base = fetch_range_series(code, indicator_range, "1d", offline=offline)
        if not offline:
            time.sleep(sum(delay_range) / 2)
        indicators = compute_indicators(indicator_base) if len(indicator_base) >= 30 else None

        for label, n in tab_days.items():
            series_by_range[label] = indicator_base[-n:] if indicator_base else []

        detail[code] = {
            "code": code, "name": name,
            "quote": quote,
            "series": series_by_range,
            "indicators": indicators,
            "institutional": institutional.get(code, []),
            "valuation": valuation.get(code),
            "margin": margin.get(code),
            "revenue": revenue.get(code),
            "company": company.get(code),
        }

    out_data = {"generated_at": datetime.now(common.get_timezone()).isoformat(), "stocks": detail}
    out_path = common.RAW_DIR / "stock_detail.json"
    common.write_json(out_path, out_data)
    log.info("個股詳細資料完成：%s（%d 檔）", out_path, len(detail))
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="抓取個股詳細頁資料（即時行情＋技術指標＋籌碼面＋基本面）")
    parser.add_argument("--offline", action="store_true", help="讀取 fixtures，不連外網")
    args = parser.parse_args()
    run(offline=args.offline)
