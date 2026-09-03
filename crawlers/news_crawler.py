# -*- coding: utf-8 -*-
"""
新聞蒐集爬蟲（架構文件 4.3 節）
來源：config/settings.yaml -> news.sources 所列 RSS feed（經濟日報、工商時報、
鉅亨網、Yahoo財經、MoneyDJ 等）。

用法：
    python crawlers/news_crawler.py             # 正式模式，抓取所有 RSS 來源
    python crawlers/news_crawler.py --offline    # 離線模式，讀 fixtures/news_items.json 測試 pipeline
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

log = common.setup_logging("crawler.news")

FIXTURE_PATH = common.BASE_DIR / "fixtures" / "news_items.json"


def _parse_entry(source_name: str, entry: dict) -> dict | None:
    title = entry.get("title", "").strip()
    if not title:
        return None
    content = entry.get("summary", "") or entry.get("description", "")
    link = entry.get("link", "")
    published = entry.get("published", "") or entry.get("updated", "")
    author = entry.get("author", "")
    return common.make_record(
        platform="News",
        board=source_name,
        title=title,
        content=content,
        author=author,
        timestamp=published,
        url=link,
    )


def fetch_online(settings: dict) -> list[dict]:
    news_cfg = settings.get("news", {})
    sources = news_cfg.get("sources", [])
    max_items = news_cfg.get("max_items_per_source", 30)

    records: list[dict] = []
    for src in sources:
        name, rss_url = src["name"], src["rss"]
        log.info("抓取來源：%s (%s)", name, rss_url)
        try:
            feed = feedparser.parse(rss_url)
            if feed.bozo and not feed.entries:
                log.warning("來源解析失敗，略過：%s (%s)", name, getattr(feed, "bozo_exception", ""))
                continue
            for entry in feed.entries[:max_items]:
                rec = _parse_entry(name, dict(entry))
                if rec:
                    records.append(rec)
            log.info("  取得 %d 則", len(feed.entries[:max_items]))
        except Exception as e:
            log.warning("來源抓取例外，略過：%s (%s)", name, e)
        time.sleep(1)
    return records


def fetch_offline() -> list[dict]:
    if not FIXTURE_PATH.exists():
        log.warning("找不到離線測試資料：%s", FIXTURE_PATH)
        return []
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    records: list[dict] = []
    for source_name, entries in data.items():
        for entry in entries:
            rec = _parse_entry(source_name, entry)
            if rec:
                records.append(rec)
    return records


def run(offline: bool = False) -> Path:
    settings = common.load_settings()
    records = fetch_offline() if offline else fetch_online(settings)
    out_path = common.raw_output_path("news")
    common.write_json(out_path, records)
    log.info("完成，共 %d 則，寫入 %s", len(records), out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="財經新聞 RSS 蒐集")
    parser.add_argument("--offline", action="store_true", help="讀取 fixtures，不連外網")
    args = parser.parse_args()
    run(offline=args.offline)
