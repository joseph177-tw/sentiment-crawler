# -*- coding: utf-8 -*-
"""
通知層（架構文件第九節）
每日推播簡短摘要（聲量、正負面比例、Top 3 話題連結）；
每週推播完整報告連結（HTML 報告以 GitHub Actions artifact 或 repo 內路徑呈現，
個人研究用途不架設對外網站，故推播路徑文字而非公開網址）。

直接用 Telegram Bot HTTP API（sendMessage），不引入 python-telegram-bot 這種
非同步框架依賴 —— 一支腳本、一次性發送，用 requests 就足夠。

需要環境變數：
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

用法：
    python notify/telegram_bot.py              # 每日摘要
    python notify/telegram_bot.py --weekly       # 每週摘要
    python notify/telegram_bot.py --dry-run      # 只印出訊息內容，不呼叫 Telegram API
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common
from report.render import build_stats, top_topics

log = common.setup_logging("notify.telegram")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_daily_message(day: str) -> str:
    records = common.read_json(common.RAW_DIR / day / "sentiment.json")
    stats = build_stats(records)
    top3 = top_topics(records, 3)

    lines = [
        f"<b>社群輿情日報 {day}</b>",
        f"今日聲量：{stats['total']} 則",
        f"正面 {stats['positive']} ({stats['positive_pct']}%)｜"
        f"中性 {stats['neutral']} ({stats['neutral_pct']}%)｜"
        f"負面 {stats['negative']} ({stats['negative_pct']}%)",
        "",
        "<b>Top 3 話題</b>",
    ]
    if not top3:
        lines.append("（今日無資料）")
    for i, rec in enumerate(top3, 1):
        title = _html_escape(rec.get("title", ""))
        url = rec.get("url", "")
        lines.append(f'{i}. <a href="{url}">{title}</a>')
    return "\n".join(lines)


def build_weekly_message(report_path: Path | None) -> str:
    lines = ["<b>社群輿情週報</b>", "本週彙整報告已產出。"]
    if report_path:
        lines.append(f"報告路徑：{report_path}")
    else:
        lines.append("（尚未產出週報，請先執行 pipeline/aggregate.py 與 report/render.py --weekly）")
    return "\n".join(lines)


def send_message(text: str, dry_run: bool = False) -> bool:
    if dry_run:
        log.info("[dry-run] 訊息內容：\n%s", text)
        return True

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 環境變數")

    resp = requests.post(
        TELEGRAM_API.format(token=token),
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        log.error("Telegram 推播失敗：%s %s", resp.status_code, resp.text)
        return False
    log.info("Telegram 推播成功")
    return True


def run(weekly: bool = False, day: str | None = None, dry_run: bool = False) -> bool:
    day = day or common.today_str()
    if weekly:
        weekly_report = common.BASE_DIR / "report" / "output" / f"weekly_{day}.html"
        report_path = weekly_report if weekly_report.exists() else None
        text = build_weekly_message(report_path)
    else:
        text = build_daily_message(day)
    return send_message(text, dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telegram 推播每日/每週摘要")
    parser.add_argument("--weekly", action="store_true", help="推播每週摘要而非每日摘要")
    parser.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD，預設今天")
    parser.add_argument("--dry-run", action="store_true", help="只印出訊息，不呼叫 Telegram API")
    args = parser.parse_args()
    ok = run(weekly=args.weekly, day=args.date, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
