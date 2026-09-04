# -*- coding: utf-8 -*-
"""
彙整層（架構文件第七節）
每日：把當日 sentiment.json 寫入 data/db.sqlite（供歷史趨勢比對）。
每週：比對過去 7 天 vs 前 7 天的聲量與情緒變化，抓出「討論度高但情緒偏負」的話題缺口，
      輸出 data/raw/weekly_<end_date>.json 供 report/render.py --weekly 使用。

註：架構文件建議「觀察一週資料品質後再開發週報邏輯」，這裡先把管線搭起來讓
    weekly_report.yml 可以正常執行；週報的趨勢判讀在累積到至少 7 天真實資料前
    參考價值有限，建議先用 --dry-run 模式檢視資料結構是否正確。

用法：
    python pipeline/aggregate.py --date 2026-09-03        # 寫入當日資料到 SQLite
    python pipeline/aggregate.py --weekly                  # 產出過去7天週彙整（預設以今天為終點）
    python pipeline/aggregate.py --weekly --end-date 2026-09-07
    python pipeline/aggregate.py --keywords                # 產出累積關鍵字索引（供 keywords.html 用）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common
import keyword_lib

log = common.setup_logging("pipeline.aggregate")

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    platform TEXT,
    board TEXT,
    title TEXT,
    url TEXT,
    timestamp TEXT,
    sentiment TEXT,
    topic TEXT,
    mentions_company INTEGER,
    summary TEXT,
    push INTEGER,
    boo INTEGER
);
CREATE INDEX IF NOT EXISTS idx_posts_day ON posts(day);

CREATE TABLE IF NOT EXISTS daily_stats (
    day TEXT PRIMARY KEY,
    total INTEGER,
    positive INTEGER,
    neutral INTEGER,
    negative INTEGER
);
"""


def get_conn() -> sqlite3.Connection:
    common.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(common.DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def upsert_day(conn: sqlite3.Connection, day: str) -> dict:
    records = common.read_json(common.RAW_DIR / day / "sentiment.json")

    conn.execute("DELETE FROM posts WHERE day = ?", (day,))
    for rec in records:
        eng = rec.get("engagement", {}) or {}
        conn.execute(
            """INSERT OR REPLACE INTO posts
               (id, day, platform, board, title, url, timestamp, sentiment, topic,
                mentions_company, summary, push, boo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rec.get("id"), day, rec.get("platform"), rec.get("board"),
                rec.get("title"), rec.get("url"), rec.get("timestamp"),
                rec.get("sentiment"), rec.get("topic"),
                1 if rec.get("mentions_company") else 0,
                rec.get("summary"), eng.get("push", 0), eng.get("boo", 0),
            ),
        )

    total = len(records)
    positive = sum(1 for r in records if r.get("sentiment") == "positive")
    neutral = sum(1 for r in records if r.get("sentiment") == "neutral")
    negative = sum(1 for r in records if r.get("sentiment") == "negative")
    conn.execute(
        "INSERT OR REPLACE INTO daily_stats (day, total, positive, neutral, negative) "
        "VALUES (?, ?, ?, ?, ?)",
        (day, total, positive, neutral, negative),
    )
    conn.commit()
    log.info("寫入 SQLite：%s，%d 則貼文", day, total)
    return {"day": day, "total": total, "positive": positive, "neutral": neutral, "negative": negative}


def _date_range(end_date: str, days: int) -> list[str]:
    end = datetime.strptime(end_date, "%Y-%m-%d")
    return [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


def _window_stats(conn: sqlite3.Connection, days: list[str]) -> dict:
    placeholders = ",".join("?" for _ in days)
    row = conn.execute(
        f"""SELECT COALESCE(SUM(total),0), COALESCE(SUM(positive),0),
                   COALESCE(SUM(neutral),0), COALESCE(SUM(negative),0)
            FROM daily_stats WHERE day IN ({placeholders})""",
        days,
    ).fetchone()
    total, positive, neutral, negative = row
    return {"total": total, "positive": positive, "neutral": neutral, "negative": negative}


def _top_topics_in_window(conn: sqlite3.Connection, days: list[str], limit: int = 10) -> list[dict]:
    placeholders = ",".join("?" for _ in days)
    rows = conn.execute(
        f"""SELECT title, url, platform, board, sentiment, push, boo, mentions_company
            FROM posts WHERE day IN ({placeholders})
            ORDER BY (push - boo) DESC LIMIT ?""",
        days + [limit],
    ).fetchall()
    cols = ["title", "url", "platform", "board", "sentiment", "push", "boo", "mentions_company"]
    return [dict(zip(cols, row)) for row in rows]


def _topic_gaps(conn: sqlite3.Connection, days: list[str], limit: int = 5) -> list[dict]:
    """討論度高但情緒偏負、且提及公司的話題 —— 值得留意但公司可能尚未回應的缺口。
    （無法從公開貼文判斷公司是否已official回應，這裡以「高互動 + 負面 + 提及公司」
    做為需要留意的近似指標，非精確判定。）"""
    placeholders = ",".join("?" for _ in days)
    rows = conn.execute(
        f"""SELECT title, url, platform, board, push, boo
            FROM posts WHERE day IN ({placeholders})
              AND sentiment = 'negative' AND mentions_company = 1
            ORDER BY (push - boo) DESC LIMIT ?""",
        days + [limit],
    ).fetchall()
    cols = ["title", "url", "platform", "board", "push", "boo"]
    return [dict(zip(cols, row)) for row in rows]


def build_weekly_summary(conn: sqlite3.Connection, end_date: str) -> dict:
    this_week = _date_range(end_date, 7)
    prev_week = _date_range((datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d"), 7)

    this_stats = _window_stats(conn, this_week)
    prev_stats = _window_stats(conn, prev_week)

    return {
        "end_date": end_date,
        "week_days": this_week,
        "this_week": this_stats,
        "prev_week": prev_stats,
        "volume_change_pct": (
            round((this_stats["total"] - prev_stats["total"]) / prev_stats["total"] * 100, 1)
            if prev_stats["total"] else None
        ),
        "top_topics": _top_topics_in_window(conn, this_week, limit=10),
        "topic_gaps": _topic_gaps(conn, this_week, limit=5),
    }


def build_keyword_index(conn: sqlite3.Connection, top_n: int = 100, min_count: int = 2,
                        posts_per_keyword: int = 30) -> dict:
    """掃描 SQLite 裡累積的所有貼文（不限單日），依關鍵字彙總出現次數、來源分布、
    總互動數（push+boo）與代表性貼文列表，供 report/render.py 產出 docs/keywords.html。"""
    rows = conn.execute(
        "SELECT title, summary, url, platform, board, sentiment, push, boo, day FROM posts"
    ).fetchall()
    cols = ["title", "summary", "url", "platform", "board", "sentiment", "push", "boo", "day"]
    posts = [dict(zip(cols, row)) for row in rows]

    counts: Counter[str] = Counter()
    engagement: Counter[str] = Counter()
    sources: dict[str, Counter[str]] = {}
    post_lists: dict[str, list[dict]] = {}

    for post in posts:
        text = f"{post['title'] or ''} {post['summary'] or ''}"
        words = set(keyword_lib.tokenize(text))
        eng = (post["push"] or 0) + (post["boo"] or 0)
        for word in words:
            counts[word] += 1
            engagement[word] += eng
            sources.setdefault(word, Counter())[f"{post['platform']}·{post['board']}"] += 1
            post_lists.setdefault(word, []).append(post)

    keywords = []
    for word, count in counts.most_common():
        if count < min_count:
            break
        ranked_posts = sorted(
            post_lists[word], key=lambda p: (p["push"] or 0) + (p["boo"] or 0), reverse=True
        )[:posts_per_keyword]
        keywords.append({
            "word": word,
            "count": count,
            "total_engagement": engagement[word],
            "sources": dict(sources[word].most_common()),
            "posts": ranked_posts,
        })
        if len(keywords) >= top_n:
            break

    return {
        "generated_at": datetime.now(common.get_timezone()).isoformat(),
        "total_posts_scanned": len(posts),
        "keywords": keywords,
    }


def run_keywords() -> Path:
    conn = get_conn()
    try:
        index = build_keyword_index(conn)
    finally:
        conn.close()
    out_path = common.RAW_DIR / "keyword_index.json"
    common.write_json(out_path, index)
    log.info("關鍵字索引完成：%s（%d 個關鍵字，掃描 %d 則貼文）",
              out_path, len(index["keywords"]), index["total_posts_scanned"])
    return out_path


def run_daily(day: str) -> dict:
    conn = get_conn()
    try:
        return upsert_day(conn, day)
    finally:
        conn.close()


def run_weekly(end_date: str) -> Path:
    conn = get_conn()
    try:
        summary = build_weekly_summary(conn, end_date)
    finally:
        conn.close()
    out_path = common.RAW_DIR / f"weekly_{end_date}.json"
    common.write_json(out_path, summary)
    log.info("週彙整完成：%s（本週 %d 則 vs 上週 %d 則）",
              out_path, summary["this_week"]["total"], summary["prev_week"]["total"])
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="每日資料寫入 SQLite / 每週趨勢彙整")
    parser.add_argument("--date", default=None, help="寫入指定日期資料到 SQLite（預設今天）")
    parser.add_argument("--weekly", action="store_true", help="產出過去7天週彙整")
    parser.add_argument("--end-date", default=None, help="週彙整終點日期，預設今天（僅搭配 --weekly）")
    parser.add_argument("--keywords", action="store_true", help="產出累積關鍵字索引")
    args = parser.parse_args()

    if args.weekly:
        run_weekly(args.end_date or common.today_str())
    elif args.keywords:
        run_keywords()
    else:
        run_daily(args.date or common.today_str())
