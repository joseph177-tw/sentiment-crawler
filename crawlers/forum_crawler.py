# -*- coding: utf-8 -*-
"""
金融論壇爬蟲（架構文件 4.4 節）
Mobile01 投資理財版等靜態網頁，用 requests + BeautifulSoup 解析。

用法：
    python crawlers/forum_crawler.py             # 正式模式，連線 Mobile01
    python crawlers/forum_crawler.py --offline    # 離線模式，讀 fixtures/mobile01_pages/ 測試 parser
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

log = common.setup_logging("crawler.forum")

FIXTURE_DIR = common.BASE_DIR / "fixtures" / "mobile01_pages"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sentiment-research/1.0",
}


def _sleep(delay_range: list[int]) -> None:
    lo, hi = (delay_range + [2, 3])[:2]
    time.sleep(random.uniform(lo, hi))


def _parse_topic_list(html_text: str, base_url: str) -> list[dict]:
    """解析 Mobile01 topiclist 頁面，取出每篇主題的標題/連結/回覆數/最後回覆時間。"""
    soup = BeautifulSoup(html_text, "lxml")
    records: list[dict] = []
    for row in soup.select("div.c-listTableTd__title, li.o-listCard"):
        a = row.select_one("a")
        if not a or not a.get("href"):
            continue
        title = a.get_text(strip=True)
        if not title:
            continue
        href = a["href"]
        url = href if href.startswith("http") else f"https://www.mobile01.com{href}"
        # 主題列表頁通常沒有內文，內文另外抓文章頁；此處先以標題本身作為最小內容
        records.append({"title": title, "url": url})
    return records


def _parse_topic_page(html_text: str, url: str) -> dict | None:
    """解析單篇主題頁面（第一樓內容）。"""
    soup = BeautifulSoup(html_text, "lxml")
    title_tag = soup.select_one("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""
    content_tag = soup.select_one("div.u-htmlContent, div.article-content")
    content = content_tag.get_text("\n", strip=True) if content_tag else ""
    author_tag = soup.select_one("a.c-articleAuthor__name, span.author-name")
    author = author_tag.get_text(strip=True) if author_tag else ""
    time_tag = soup.select_one("time")
    timestamp = time_tag.get("datetime", "") if time_tag else (time_tag.get_text(strip=True) if time_tag else "")

    if not title:
        return None
    return common.make_record(
        platform="Forum",
        board="Mobile01-投資理財",
        title=title,
        content=content or title,
        author=author,
        timestamp=timestamp,
        url=url,
    )


def fetch_online(settings: dict) -> list[dict]:
    forum_cfg = settings.get("forum", {}).get("mobile01", {})
    board_url = forum_cfg.get("board_url")
    delay_range = settings.get("forum", {}).get("request_delay_seconds", [2, 3])
    if not board_url:
        log.warning("未設定 forum.mobile01.board_url，略過")
        return []

    records: list[dict] = []
    try:
        resp = requests.get(board_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("論壇列表抓取失敗：%s", e)
        return records

    topics = _parse_topic_list(resp.text, board_url)
    log.info("Mobile01：取得 %d 篇主題連結", len(topics))
    with requests.Session() as session:
        for topic in topics:
            _sleep(delay_range)
            try:
                r = session.get(topic["url"], headers=HEADERS, timeout=15)
                r.raise_for_status()
            except requests.RequestException as e:
                log.warning("主題頁抓取失敗，略過：%s (%s)", topic["url"], e)
                continue
            rec = _parse_topic_page(r.text, topic["url"])
            if rec:
                records.append(rec)
    return records


def fetch_offline() -> list[dict]:
    records: list[dict] = []
    if not FIXTURE_DIR.exists():
        log.warning("找不到離線測試資料夾：%s", FIXTURE_DIR)
        return records
    for html_file in sorted(FIXTURE_DIR.glob("*.html")):
        html_text = html_file.read_text(encoding="utf-8")
        fake_url = f"https://www.mobile01.com/topicdetail.php?f=291&t={html_file.stem}"
        rec = _parse_topic_page(html_text, fake_url)
        if rec:
            records.append(rec)
    return records


def run(offline: bool = False) -> Path:
    settings = common.load_settings()
    records = fetch_offline() if offline else fetch_online(settings)
    out_path = common.raw_output_path("forum")
    common.write_json(out_path, records)
    log.info("完成，共 %d 則，寫入 %s", len(records), out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="金融論壇（Mobile01）爬蟲")
    parser.add_argument("--offline", action="store_true", help="讀取 fixtures，不連外網")
    args = parser.parse_args()
    run(offline=args.offline)
