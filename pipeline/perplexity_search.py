# -*- coding: utf-8 -*-
"""
用 Perplexity API 針對盤勢頁偵測到的個股，查詢近期產業/基本面深度分析，
補進 data/raw/stock_detail.json 每檔個股的 "perplexity" 欄位，供
report/render.py --stock-detail 的個股詳細頁顯示。

沒有設定 PERPLEXITY_API_KEY 環境變數時直接跳過（不報錯），讓這支腳本可以
安全地放進 daily_crawl.yml 排程，等之後設定好 key 再自動生效，不用改 workflow。

有做查詢快取（data/raw/perplexity_cache.json）：同一檔個股在
config/settings.yaml -> perplexity.cache_days 天內不會重複查詢，
避免每天都花 API 額度查同樣的東西。

用法：
    python pipeline/perplexity_search.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common
import os

log = common.setup_logging("pipeline.perplexity")

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
CACHE_PATH = common.RAW_DIR / "perplexity_cache.json"

PROMPT_TEMPLATE = (
    "請針對台股「{name}」（股票代號 {code}）提供目前的產業地位與基本面重點分析，"
    "包含近期營收/獲利趨勢、所屬產業景氣、主要風險因素。請用繁體中文回答，"
    "200 字以內，直接給結論不要客套話。"
)


def _load_cache() -> dict:
    return common.read_json(CACHE_PATH, default={})


def _save_cache(cache: dict) -> None:
    common.write_json(CACHE_PATH, cache)


def _is_fresh(entry: dict, cache_days: int) -> bool:
    queried_at = entry.get("queried_at")
    if not queried_at:
        return False
    try:
        ts = datetime.fromisoformat(queried_at)
    except ValueError:
        return False
    return datetime.now(common.get_timezone()) - ts < timedelta(days=cache_days)


def query_stock(api_key: str, model: str, code: str, name: str) -> dict | None:
    prompt = PROMPT_TEMPLATE.format(name=name, code=code)
    try:
        resp = requests.post(
            PERPLEXITY_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("Perplexity 查詢失敗，略過 %s（%s）：%s", code, name, e)
        return None

    try:
        summary = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        log.warning("Perplexity 回應格式異常，略過 %s（%s）", code, name)
        return None

    return {
        "summary": summary,
        "citations": data.get("citations", []),
        "queried_at": datetime.now(common.get_timezone()).isoformat(),
    }


def run() -> None:
    settings = common.load_settings().get("perplexity", {})
    if not settings.get("enabled", True):
        log.info("perplexity.enabled=false，跳過")
        return

    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        log.info("未設定 PERPLEXITY_API_KEY，跳過深度分析查詢（不影響其他功能）")
        return

    detail_path = common.RAW_DIR / "stock_detail.json"
    detail_data = common.read_json(detail_path, default=None)
    if not detail_data or not detail_data.get("stocks"):
        log.warning("找不到 stock_detail.json，請先執行 pipeline/stock_detail.py")
        return

    model = settings.get("model", "sonar")
    cache_days = settings.get("cache_days", 7)
    cache = _load_cache()

    updated = 0
    for code, stock in detail_data["stocks"].items():
        name = stock.get("name", code)
        cached = cache.get(code)
        if cached and _is_fresh(cached, cache_days):
            stock["perplexity"] = cached
            continue

        log.info("查詢 Perplexity：%s %s", code, name)
        result = query_stock(api_key, model, code, name)
        if result:
            cache[code] = result
            stock["perplexity"] = result
            updated += 1
        time.sleep(0.5)

    _save_cache(cache)
    common.write_json(detail_path, detail_data)
    log.info("Perplexity 深度分析完成，%d 檔更新（其餘用快取或跳過）", updated)


if __name__ == "__main__":
    run()
