# -*- coding: utf-8 -*-
"""
PTT 版面爬蟲（架構文件 4.1 節）
使用 requests + BeautifulSoup 直接抓取看板（預設 Stock、Bank_Service），
需帶 over18=1 cookie 才能存取內容；請求間隔隨機 1-2 秒避免被視為異常流量。

用法：
    python crawlers/ptt_crawler.py             # 正式模式，連線 PTT
    python crawlers/ptt_crawler.py --offline    # 離線模式，讀 fixtures/ptt_pages/ 測試 parser
"""
from __future__ import annotations

import argparse
import random
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

log = common.setup_logging("crawler.ptt")

BASE_URL = "https://www.ptt.cc"
FIXTURE_DIR = common.BASE_DIR / "fixtures" / "ptt_pages"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sentiment-research/1.0",
}
COOKIES = {"over18": "1"}


def _sleep(delay_range: list[int]) -> None:
    lo, hi = (delay_range + [1, 2])[:2]
    time.sleep(random.uniform(lo, hi))


def _get(session: requests.Session, url: str) -> str | None:
    try:
        resp = session.get(url, headers=HEADERS, cookies=COOKIES, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        log.warning("請求失敗，略過：%s (%s)", url, e)
        return None


def _list_article_links(index_html: str) -> tuple[list[str], str | None]:
    """回傳 (本頁文章連結, 上一頁連結)"""
    soup = BeautifulSoup(index_html, "lxml")
    links = []
    for div in soup.select("div.r-ent"):
        a = div.select_one("div.title a")
        if a and a.get("href"):
            links.append(BASE_URL + a["href"])
    prev_href = None
    for a in soup.select("div.btn-group-paging a"):
        if "上頁" in a.text:
            prev_href = BASE_URL + a["href"] if a.get("href") else None
    return links, prev_href


def _parse_article(html: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("#main-content")
    if not main:
        return None

    meta_values = {}
    for meta in main.select("div.article-metaline"):
        tag = meta.select_one("span.article-meta-tag")
        value = meta.select_one("span.article-meta-value")
        if tag and value:
            meta_values[tag.text.strip()] = value.text.strip()

    title = meta_values.get("標題", "")
    author = meta_values.get("作者", "")
    timestamp = meta_values.get("時間", "")

    # 推文/噓文計數需在移除 div.push 前算好（main 與 soup 共用同一棵樹，
    # extract() 後 soup.select("div.push") 也會跟著抓不到）
    push, boo = 0, 0
    for p in main.select("div.push"):
        tag = p.select_one("span.push-tag")
        if not tag:
            continue
        t = tag.text.strip()
        if t == "推":
            push += 1
        elif t == "噓":
            boo += 1

    # 內文：移除 meta 行與推文區塊後的純文字
    for tag in main.select("div.article-metaline, div.article-metaline-right, div.push"):
        tag.extract()
    content = main.get_text("\n", strip=True)
    # 移除 IP/編輯訊息等雜訊行
    content = re.sub(r"※ 發信站.*", "", content, flags=re.S).strip()

    if not title:
        return None

    board = url.split("/bbs/")[1].split("/")[0] if "/bbs/" in url else ""
    return common.make_record(
        platform="PTT",
        board=board,
        title=title,
        content=content,
        author=author,
        timestamp=timestamp,
        url=url,
        push=push,
        boo=boo,
    )


def fetch_board_online(session: requests.Session, board: str, max_pages: int, delay_range: list[int]) -> list[dict]:
    records: list[dict] = []
    index_url = f"{BASE_URL}/bbs/{board}/index.html"
    for _ in range(max_pages):
        html = _get(session, index_url)
        if not html:
            break
        links, prev_url = _list_article_links(html)
        log.info("看板 %s：本頁 %d 篇文章", board, len(links))
        for link in links:
            _sleep(delay_range)
            article_html = _get(session, link)
            if not article_html:
                continue
            rec = _parse_article(article_html, link)
            if rec:
                records.append(rec)
        if not prev_url:
            break
        index_url = prev_url
        _sleep(delay_range)
    return records


def fetch_online(settings: dict) -> list[dict]:
    ptt_cfg = settings.get("ptt", {})
    boards = ptt_cfg.get("boards", ["Stock"])
    max_pages = ptt_cfg.get("max_pages_per_board", 5)
    delay_range = ptt_cfg.get("request_delay_seconds", [1, 2])

    records: list[dict] = []
    with requests.Session() as session:
        for board in boards:
            records += fetch_board_online(session, board, max_pages, delay_range)
    return records


def fetch_offline() -> list[dict]:
    """讀 fixtures/ptt_pages/*.html（單篇文章頁面存檔）測試 parser 正確性。"""
    records: list[dict] = []
    if not FIXTURE_DIR.exists():
        log.warning("找不到離線測試資料夾：%s", FIXTURE_DIR)
        return records
    for html_file in sorted(FIXTURE_DIR.glob("*.html")):
        html = html_file.read_text(encoding="utf-8")
        fake_url = f"{BASE_URL}/bbs/Stock/{html_file.stem}.html"
        rec = _parse_article(html, fake_url)
        if rec:
            records.append(rec)
    return records


def run(offline: bool = False) -> Path:
    settings = common.load_settings()
    records = fetch_offline() if offline else fetch_online(settings)
    out_path = common.raw_output_path("ptt")
    common.write_json(out_path, records)
    log.info("完成，共 %d 則，寫入 %s", len(records), out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PTT 看板爬蟲")
    parser.add_argument("--offline", action="store_true", help="讀取 fixtures，不連外網")
    args = parser.parse_args()
    run(offline=args.offline)
