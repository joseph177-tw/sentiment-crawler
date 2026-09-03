# -*- coding: utf-8 -*-
"""
Dcard 爬蟲（架構文件 4.2 節）
使用 Dcard 公開可讀的 service/api/v2 endpoint（非官方，個人研究用途），
鎖定「股票」「理財」看板。請求間隔隨機 2-3 秒。

用法：
    python crawlers/dcard_crawler.py             # 正式模式，連線 Dcard
    python crawlers/dcard_crawler.py --offline    # 離線模式，讀 fixtures/dcard_posts.json 測試 parser
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

log = common.setup_logging("crawler.dcard")

API_BASE = "https://www.dcard.tw/service/api/v2"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sentiment-research/1.0",
    "Accept": "application/json",
}
FIXTURE_PATH = common.BASE_DIR / "fixtures" / "dcard_posts.json"


def _sleep(delay_range: list[int]) -> None:
    lo, hi = (delay_range + [2, 3])[:2]
    time.sleep(random.uniform(lo, hi))


def _parse_post(forum: str, post: dict) -> dict | None:
    title = (post.get("title") or "").strip()
    if not title:
        return None
    content = post.get("excerpt") or post.get("content") or ""
    post_id = post.get("id")
    return common.make_record(
        platform="Dcard",
        board=forum,
        title=title,
        content=content,
        author=post.get("school") or post.get("gender") or "",
        timestamp=post.get("createdAt", ""),
        url=f"https://www.dcard.tw/f/{forum}/p/{post_id}" if post_id else "",
        push=post.get("likeCount", 0) or 0,
        boo=0,
    )


def fetch_forum_online(session: requests.Session, forum: str, max_pages: int, delay_range: list[int]) -> list[dict]:
    records: list[dict] = []
    before: str | None = None
    for _ in range(max_pages):
        params = {"popular": "false", "limit": 30}
        if before:
            params["before"] = before
        try:
            resp = session.get(f"{API_BASE}/forums/{forum}/posts", headers=HEADERS, params=params, timeout=15)
            resp.raise_for_status()
            posts = resp.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Dcard 看板 %s 抓取失敗，略過：%s", forum, e)
            break
        if not posts:
            break
        log.info("Dcard 看板 %s：取得 %d 篇貼文", forum, len(posts))
        for post in posts:
            rec = _parse_post(forum, post)
            if rec:
                records.append(rec)
        before = str(posts[-1].get("id", ""))
        _sleep(delay_range)
    return records


def fetch_online(settings: dict) -> list[dict]:
    dcard_cfg = settings.get("dcard", {})
    forums = dcard_cfg.get("forums", ["stock", "money"])
    max_pages = dcard_cfg.get("max_pages_per_forum", 3)
    delay_range = dcard_cfg.get("request_delay_seconds", [2, 3])

    records: list[dict] = []
    with requests.Session() as session:
        for forum in forums:
            records += fetch_forum_online(session, forum, max_pages, delay_range)
    return records


def fetch_offline() -> list[dict]:
    if not FIXTURE_PATH.exists():
        log.warning("找不到離線測試資料：%s", FIXTURE_PATH)
        return []
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    records: list[dict] = []
    for forum, posts in data.items():
        for post in posts:
            rec = _parse_post(forum, post)
            if rec:
                records.append(rec)
    return records


def run(offline: bool = False) -> Path:
    settings = common.load_settings()
    records = fetch_offline() if offline else fetch_online(settings)
    out_path = common.raw_output_path("dcard")
    common.write_json(out_path, records)
    log.info("完成，共 %d 則，寫入 %s", len(records), out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dcard 股票/理財看板爬蟲")
    parser.add_argument("--offline", action="store_true", help="讀取 fixtures，不連外網")
    args = parser.parse_args()
    run(offline=args.offline)
