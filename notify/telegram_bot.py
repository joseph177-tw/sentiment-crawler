# -*- coding: utf-8 -*-
"""
通知層（架構文件第九節）
每日推播簡短摘要（聲量、正負面比例、Top 3 話題連結）；每週推播完整報告連結或附檔。

完整報告優先用 GitHub Pages 固定網址推播（config/settings.yaml -> report.pages_base_url，
對應 docs/index.html、docs/weekly.html，每次排程都會覆蓋更新，網址本身永遠不變）。
若沒有設定 pages_base_url，才退回用 sendDocument 把當次 HTML 報告當附檔傳過去
（適合不想開 GitHub Pages、報告內容不想公開變成「知道連結就能看」的情境）。

直接用 Telegram Bot HTTP API，不引入 python-telegram-bot 這種非同步框架依賴——
一支腳本、一次性發送，用 requests 就足夠。

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

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
CAPTION_MAX_LEN = 1024  # Telegram sendDocument caption 上限


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def get_pages_url() -> str:
    return (common.load_settings().get("report", {}) or {}).get("pages_base_url", "").rstrip("/")


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

    pages_url = get_pages_url()
    if pages_url:
        lines += ["", f'📄 <a href="{pages_url}/">完整報告（含 Top 10、關鍵字雲）</a>']
    return "\n".join(lines)


def build_weekly_message(has_report: bool) -> str:
    lines = ["<b>社群輿情週報</b>"]
    pages_url = get_pages_url()
    if has_report:
        if pages_url:
            lines.append(f'本週彙整報告已產出：<a href="{pages_url}/weekly.html">點此查看完整週報</a>')
        else:
            lines.append("本週彙整報告已產出，完整內容見附檔。")
    else:
        lines.append("（尚未產出週報，請先執行 pipeline/aggregate.py --weekly 與 report/render.py --weekly）")
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
        TELEGRAM_API.format(token=token, method="sendMessage"),
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


def send_document(file_path: Path, caption: str, dry_run: bool = False) -> bool:
    if dry_run:
        log.info("[dry-run] 附檔：%s\n[dry-run] caption：\n%s", file_path, caption)
        return True

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 環境變數")

    if len(caption) > CAPTION_MAX_LEN:
        caption = caption[:CAPTION_MAX_LEN - 1] + "…"

    with open(file_path, "rb") as f:
        resp = requests.post(
            TELEGRAM_API.format(token=token, method="sendDocument"),
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"document": (file_path.name, f, "text/html")},
            timeout=30,
        )
    if resp.status_code != 200:
        log.error("Telegram 附檔推播失敗：%s %s", resp.status_code, resp.text)
        return False
    log.info("Telegram 附檔推播成功：%s", file_path.name)
    return True


def run(weekly: bool = False, day: str | None = None, dry_run: bool = False) -> bool:
    day = day or common.today_str()
    report_dir = common.BASE_DIR / "report" / "output"
    report_path = report_dir / (f"weekly_{day}.html" if weekly else f"daily_{day}.html")

    text = build_weekly_message(report_path.exists()) if weekly else build_daily_message(day)

    if get_pages_url():
        # 有固定網址就直接推文字訊息＋連結，不用每次都附檔
        return send_message(text, dry_run=dry_run)
    if report_path.exists():
        return send_document(report_path, caption=text, dry_run=dry_run)
    log.warning("找不到報告檔 %s，改用純文字訊息", report_path)
    return send_message(text, dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telegram 推播每日/每週摘要")
    parser.add_argument("--weekly", action="store_true", help="推播每週摘要而非每日摘要")
    parser.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD，預設今天")
    parser.add_argument("--dry-run", action="store_true", help="只印出訊息，不呼叫 Telegram API")
    args = parser.parse_args()
    ok = run(weekly=args.weekly, day=args.date, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
