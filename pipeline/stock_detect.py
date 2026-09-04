# -*- coding: utf-8 -*-
"""
從累積貼文中自動偵測提到的台股上市個股，並抓取近期價格資料，
供 report/render.py --market 產出「盤勢頁面」（含完整 K 線圖）。

偵測範圍：TWSE 上市股票（STOCK_DAY_ALL 公開清單），不含上櫃(TPEx)。
偵測方式：用公司「證券名稱」（TWSE 資料本身通常就是社群慣用的簡稱，例如「南電」
「欣興」）對貼文標題+摘要做子字串比對，非官方全名比對，可能有漏抓或極少數
誤判（例如個股簡稱恰好是常見詞的一部分），屬個人研究工具可接受的簡化。

用法：
    python pipeline/stock_detect.py              # 正式模式：抓 TWSE 清單 + Yahoo 價格
    python pipeline/stock_detect.py --offline     # 離線模式：讀 fixtures，不連外網
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common
from pipeline.aggregate import get_conn

log = common.setup_logging("pipeline.stock_detect")

TWSE_STOCK_LIST_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=csv"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{code}.TW"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sentiment-research/1.0"}

FIXTURE_STOCK_LIST = common.BASE_DIR / "fixtures" / "twse_stock_list_sample.csv"
FIXTURE_CHART = common.BASE_DIR / "fixtures" / "yahoo_chart_sample.json"


def fetch_stock_list(offline: bool = False) -> dict[str, str]:
    """回傳 {證券名稱: 代號}。只保留 4 碼數字代號的普通股，過濾 ETF/權證等。"""
    if offline:
        text = FIXTURE_STOCK_LIST.read_text(encoding="utf-8")
    else:
        resp = requests.get(TWSE_STOCK_LIST_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        text = resp.text

    reader = csv.reader(io.StringIO(text))
    name_to_code: dict[str, str] = {}
    for row in reader:
        if len(row) < 3:
            continue
        code, name = row[1].strip('="\' '), row[2].strip('="\' ')
        if code.isdigit() and len(code) == 4 and name:
            name_to_code[name] = code
    log.info("TWSE 個股清單：%d 檔", len(name_to_code))
    return name_to_code


def detect_mentions(name_to_code: dict[str, str], min_count: int = 1) -> dict[str, dict]:
    """掃描 SQLite 累積貼文，回傳 {代號: {name, count, posts}}，依提及次數排序後由呼叫端截斷。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT title, summary, url, platform, board, sentiment, push, boo, day FROM posts"
        ).fetchall()
    finally:
        conn.close()
    cols = ["title", "summary", "url", "platform", "board", "sentiment", "push", "boo", "day"]
    posts = [dict(zip(cols, row)) for row in rows]

    # 名稱較長的個股優先比對，避免短名稱先命中蓋掉更精確的長名稱
    names_sorted = sorted(name_to_code.keys(), key=len, reverse=True)

    counts: Counter[str] = Counter()
    post_lists: dict[str, list[dict]] = {}
    for post in posts:
        text = f"{post['title'] or ''} {post['summary'] or ''}"
        matched_codes: set[str] = set()
        for name in names_sorted:
            if name in text:
                matched_codes.add(name_to_code[name])
        for code in matched_codes:
            counts[code] += 1
            post_lists.setdefault(code, []).append(post)

    code_to_name = {v: k for k, v in name_to_code.items()}
    result = {}
    for code, count in counts.items():
        if count < min_count:
            continue
        ranked_posts = sorted(
            post_lists[code], key=lambda p: (p["push"] or 0) + (p["boo"] or 0), reverse=True
        )[:10]
        result[code] = {"name": code_to_name[code], "count": count, "posts": ranked_posts}
    return result


def fetch_price_history(code: str, offline: bool = False, range_: str = "6mo") -> list[dict]:
    if offline:
        import json
        data = json.loads(FIXTURE_CHART.read_text(encoding="utf-8"))
    else:
        try:
            resp = requests.get(
                YAHOO_CHART_URL.format(code=code),
                params={"range": range_, "interval": "1d"},
                headers=HEADERS, timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Yahoo Finance 抓取失敗，略過 %s：%s", code, e)
            return []

    try:
        result = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        log.warning("Yahoo Finance 回應格式異常，略過 %s", code)
        return []

    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    prices = []
    for i, ts in enumerate(timestamps):
        o, h, l, c, v = (quote[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        date = datetime.fromtimestamp(ts, common.get_timezone()).strftime("%Y-%m-%d")
        prices.append({"date": date, "open": round(o, 2), "high": round(h, 2),
                        "low": round(l, 2), "close": round(c, 2), "volume": v or 0})
    return prices


def run(offline: bool = False, top_n: int | None = None) -> Path:
    market_cfg = common.load_settings().get("market", {})
    top_n = top_n or market_cfg.get("top_n", 15)
    price_range = market_cfg.get("price_range", "6mo")

    name_to_code = fetch_stock_list(offline=offline)
    mentions = detect_mentions(name_to_code)

    ranked = sorted(mentions.items(), key=lambda kv: kv[1]["count"], reverse=True)[:top_n]
    log.info("偵測到 %d 檔個股被提及，取前 %d 檔抓價格", len(mentions), len(ranked))

    stocks = []
    for code, info in ranked:
        prices = fetch_price_history(code, offline=offline, range_=price_range)
        if not prices:
            log.warning("%s（%s）沒有價格資料，略過此檔", code, info["name"])
            continue
        stocks.append({
            "code": code, "name": info["name"], "mention_count": info["count"],
            "posts": info["posts"], "prices": prices,
        })

    market_data = {
        "generated_at": datetime.now(common.get_timezone()).isoformat(),
        "stocks": stocks,
    }
    out_path = common.RAW_DIR / "market_data.json"
    common.write_json(out_path, market_data)
    log.info("盤勢資料完成：%s（%d 檔個股）", out_path, len(stocks))
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="偵測貼文提及個股並抓取價格資料")
    parser.add_argument("--offline", action="store_true", help="讀取 fixtures，不連外網")
    args = parser.parse_args()
    run(offline=args.offline)
