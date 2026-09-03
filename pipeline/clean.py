# -*- coding: utf-8 -*-
"""
清理與去重層（架構文件第五節）
讀取 data/raw/<日期>/*.json（各爬蟲的原始輸出），依序：
  1. 去除重複貼文（用 id，即標題+時間 hash 比對）
  2. 過濾 exclude_noise 關鍵字內容（廣告、業配、抽獎文）
  3. 過濾字數過短（低於 settings.yaml clean.min_content_length）的無效留言
  4. 統一時間格式、來源標籤（已由 common.make_record 標準化，此處只需驗證）
輸出：data/raw/<日期>/cleaned.json

用法：
    python pipeline/clean.py                  # 清理今天的資料
    python pipeline/clean.py --date 2026-09-01 # 清理指定日期
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

log = common.setup_logging("pipeline.clean")

SOURCE_FILES = ["news.json", "ptt.json", "dcard.json", "forum.json"]


def _contains_noise(text: str, noise_keywords: list[str]) -> bool:
    return any(kw in text for kw in noise_keywords)


def load_raw_records(day: str) -> list[dict]:
    raw_dir = common.RAW_DIR / day
    records: list[dict] = []
    if not raw_dir.exists():
        log.warning("找不到原始資料夾：%s", raw_dir)
        return records
    for fname in SOURCE_FILES:
        path = raw_dir / fname
        if path.exists():
            items = common.read_json(path)
            log.info("讀取 %s：%d 則", fname, len(items))
            records.extend(items)
    return records


def clean(records: list[dict], settings: dict, keywords: dict) -> list[dict]:
    noise_keywords = keywords.get("exclude_noise", [])
    min_len = settings.get("clean", {}).get("min_content_length", 15)

    seen_ids: set[str] = set()
    cleaned: list[dict] = []
    dropped_dup, dropped_noise, dropped_short = 0, 0, 0

    for rec in records:
        rec_id = rec.get("id")
        if not rec_id or rec_id in seen_ids:
            dropped_dup += 1
            continue

        text = f"{rec.get('title', '')} {rec.get('content', '')}"
        if _contains_noise(text, noise_keywords):
            dropped_noise += 1
            continue

        if len(rec.get("content", "")) < min_len:
            dropped_short += 1
            continue

        seen_ids.add(rec_id)
        cleaned.append(rec)

    log.info(
        "清理完成：輸入 %d 則 -> 輸出 %d 則（去重 %d、噪音 %d、過短 %d）",
        len(records), len(cleaned), dropped_dup, dropped_noise, dropped_short,
    )
    return cleaned


def run(day: str | None = None) -> Path:
    day = day or common.today_str()
    settings = common.load_settings()
    keywords = common.load_keywords()

    raw_records = load_raw_records(day)
    cleaned_records = clean(raw_records, settings, keywords)

    out_path = common.RAW_DIR / day / "cleaned.json"
    common.write_json(out_path, cleaned_records)
    log.info("寫入 %s", out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理與去重原始爬取資料")
    parser.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD，預設今天")
    args = parser.parse_args()
    run(day=args.date)
